"""Minimal Draw Things gRPC client used by the Infinite-Canvas image path."""

from __future__ import annotations

import base64
import io
import os
import secrets
import struct
import sys
from pathlib import Path
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SRC = REPO_ROOT / "draw-things-comfyui" / "src"
if str(PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SRC))


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7859
DEFAULT_SIZE = (1024, 1024)


def draw_things_model_supports_editing(model: str) -> bool:
    normalized = str(model or "").strip().lower().replace("-", "_")
    if "klein" in normalized:
        return True
    return "qwen" in normalized and "edit" in normalized


def _settings(endpoint: str = "") -> tuple[str, int, bool, str]:
    host = str(os.getenv("DRAW_THINGS_GRPC_HOST", DEFAULT_HOST)).strip() or DEFAULT_HOST
    try:
        port = int(os.getenv("DRAW_THINGS_GRPC_PORT", str(DEFAULT_PORT)))
    except ValueError:
        port = DEFAULT_PORT
    custom_endpoint = str(endpoint or "").strip()
    if custom_endpoint:
        # A provider-specific host:port takes precedence over environment
        # defaults, while grpc:// and https:// prefixes remain accepted.
        parsed = urlsplit(
            custom_endpoint
            if "://" in custom_endpoint
            else f"//{custom_endpoint}"
        )
        if not parsed.hostname:
            raise ValueError(
                "Draw Things gRPCServerCLI 地址无效，请填写主机:端口，例如 127.0.0.1:7859。"
            )
        host = parsed.hostname
        if parsed.port is not None:
            port = parsed.port
    # gRPCServerCLI enables TLS by default. Plaintext remains available only
    # when a user explicitly sets DRAW_THINGS_GRPC_TLS=false.
    use_tls = str(os.getenv("DRAW_THINGS_GRPC_TLS", "true")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    shared_secret = str(os.getenv("DRAW_THINGS_GRPC_SHARED_SECRET", "")).strip()
    return host, port, use_tls, shared_secret


def _parse_size(size: str) -> tuple[int, int]:
    import re

    match = re.fullmatch(r"\s*(\d+)\s*[xX*]\s*(\d+)\s*", str(size or ""))
    if not match:
        # The upstream Infinite-Canvas API defaults to 1024x1024. Normal
        # canvas requests carry an explicit size from the node's settings.
        return DEFAULT_SIZE
    width = max(64, min(2048, int(match.group(1)) // 64 * 64))
    height = max(64, min(2048, int(match.group(2)) // 64 * 64))
    return width, height


def _build_configuration(
    model: str,
    width: int,
    height: int,
    seed: int | None = None,
    strength: float | None = None,
    batch_size: int = 1,
) -> bytes:
    import flatbuffers
    from generated import config_generated

    model_name = str(model or "").lower()
    is_klein = "klein" in model_name
    is_z_image = "z_image" in model_name or "zimage" in model_name
    config = config_generated.GenerationConfigurationT()
    config.model = model
    config.startWidth = width // 64
    config.startHeight = height // 64
    default_steps = "4"
    config.steps = int(os.getenv("DRAW_THINGS_GRPC_STEPS", default_steps))
    default_guidance = "1.0" if (is_klein or is_z_image) else "3.5"
    config.guidanceScale = float(
        os.getenv("DRAW_THINGS_GRPC_GUIDANCE", default_guidance)
    )
    if seed is not None:
        # The canvas dice control supplies an explicit seed for this request.
        config.seed = int(seed) % 4294967295
    else:
        configured_seed = os.getenv("DRAW_THINGS_GRPC_SEED")
        if configured_seed is None or not configured_seed.strip():
            # Generate a fresh seed per request so batch generations do not
            # repeat the same image when no canvas seed was supplied.
            config.seed = secrets.randbelow(4294967295)
        else:
            # An explicit environment value intentionally enables deterministic
            # generation for debugging and reproduction.
            config.seed = int(configured_seed) % 4294967295
    config.batchCount = 1
    config.batchSize = max(1, min(8, int(batch_size or 1)))
    if strength is not None:
        # Draw Things uses strength for image-to-image denoising. Keep it in
        # the same [0, 1] range exposed by the ComfyUI plugin.
        config.strength = max(0.0, min(1.0, float(strength)))
    if is_klein:
        # FLUX.2 Klein's known-good Draw Things setup is 4-step DDIM Trailing
        # with CFG 1, ScaleAlike seeds, shift 3, and no resolution shift.
        config.sampler = 16  # SamplerType.DDIMTrailing
        config.seedMode = 2  # SeedMode.ScaleAlike
        config.shift = 3.0
        config.resolutionDependentShift = False
        config.speedUpWithGuidanceEmbed = True
        config.guidanceEmbed = 3.5
    elif is_z_image:
        # Z Image Turbo's official Draw Things setup uses UniPC Trailing,
        # ScaleAlike seeds, shift 3, and resolution-independent shift.
        config.sampler = 17  # SamplerType.UniPCTrailing
        config.seedMode = 2  # SeedMode.ScaleAlike
        config.shift = 3.0
        config.resolutionDependentShift = False

    builder = flatbuffers.Builder(0)
    builder.Finish(config.Pack(builder))
    return bytes(builder.Output())


def _reference_image_bytes(reference: object) -> bytes:
    """Read one Infinite-Canvas reference image from a local source."""
    value = reference
    if isinstance(reference, dict):
        value = (
            reference.get("url")
            or reference.get("path")
            or reference.get("file")
            or reference.get("data")
        )
    if isinstance(value, bytes):
        return value
    source = str(value or "").strip()
    if not source:
        raise ValueError("参考图缺少本地 url、path、file 或 data 字段。")
    if source.startswith("data:"):
        try:
            _, encoded = source.split(",", 1)
            return base64.b64decode(encoded)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError("参考图 data URL 无法解码。") from exc
    if source.startswith("http://") or source.startswith("https://"):
        raise ValueError("阶段 3 只读取本地参考图，不下载远程 URL。")
    if source.startswith("file://"):
        source = urlsplit(source).path
    path = Path(source).expanduser()
    if not path.is_absolute():
        candidates = (
            Path.cwd() / path,
            Path(__file__).resolve().parent / path,
            REPO_ROOT / path,
        )
        path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValueError(f"参考图无法读取：{path}") from exc


def _resize_crop_reference(image, width: int, height: int):
    """Match draw-things-comfyui's resize-then-center-crop behavior."""
    from PIL import Image

    image = image.convert("RGB")
    if image.size == (width, height):
        return image
    source_width, source_height = image.size
    scale = max(width / source_width, height / source_height)
    resized = image.resize(
        (max(width, int(source_width * scale)), max(height, int(source_height * scale))),
        Image.Resampling.BILINEAR,
    )
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _encode_image_for_request(reference: object, width: int, height: int) -> bytes:
    """Encode a local image as Draw Things' RGB NHWC FP16 tensor."""
    import numpy as np
    from PIL import Image

    raw = _reference_image_bytes(reference)
    with Image.open(io.BytesIO(raw)) as source:
        image = _resize_crop_reference(source, width, height)
        pixels = np.asarray(image, dtype=np.float32) / 255.0 * 2.0 - 1.0

    # This is the same 68-byte CCV header and HWC FP16 payload used by the
    # draw-things-comfyui plugin. It is an image input, not a HintProto.
    encoded = bytearray(68 + width * height * 3 * 2)
    struct.pack_into(
        "<9I",
        encoded,
        0,
        0,
        0x1,       # CCV_TENSOR_CPU_MEMORY
        0x2,       # CCV_TENSOR_FORMAT_NHWC
        0x20000,   # CCV_16F
        0,
        1,
        height,
        width,
        3,
    )
    encoded[68:] = pixels.astype(np.float16, copy=False).tobytes(order="C")
    return bytes(encoded)


def _parse_strength(value: object, default: float = 0.75) -> float:
    try:
        strength = float(value)
    except (TypeError, ValueError):
        strength = default
    return max(0.0, min(1.0, strength))


def _parse_hint_weight(value: object, default: float = 1.0) -> float:
    """Normalize one Hint tensor weight to the range accepted by Draw Things."""
    try:
        weight = float(value)
    except (TypeError, ValueError):
        weight = default
    if weight < 0:
        raise ValueError("Draw Things Hint 权重不能小于 0。")
    return weight


def _build_hint_protos(
    references: list[object],
    width: int,
    height: int,
    hint_type: str = "shuffle",
    weights: list[object] | None = None,
):
    """Build one HintProto from one or more local images.

    Draw Things expects all images belonging to one control type inside the
    same HintProto. This mirrors draw-things-comfyui's request construction
    and keeps ordinary request.image input separate from Hint inputs.
    """
    from generated import imageService_pb2

    normalized_type = str(hint_type or "").strip().lower()
    if not normalized_type:
        raise ValueError("Draw Things Hint 类型不能为空。")
    if not references:
        raise ValueError("Draw Things Hint 至少需要一张参考图。")
    if weights is not None and len(weights) not in {0, len(references)}:
        raise ValueError("Hint 权重数量必须与参考图数量一致。")

    tensor_weights = []
    for index, reference in enumerate(references):
        reference_weight = None
        if isinstance(reference, dict):
            reference_weight = reference.get("weight")
        if weights:
            reference_weight = weights[index]
        tensor_weights.append(
            (
                _encode_image_for_request(reference, width, height),
                _parse_hint_weight(reference_weight),
            )
        )

    hint = imageService_pb2.HintProto(hintType=normalized_type)
    hint.tensors.extend(
        imageService_pb2.TensorAndWeight(tensor=tensor, weight=weight)
        for tensor, weight in tensor_weights
    )
    return [hint]


def _decode_response_image(response_image: bytes) -> bytes:
    import fpzip
    import numpy as np
    from PIL import Image

    header = np.frombuffer(response_image, dtype=np.uint32, count=17)
    height, width, channels = (int(value) for value in header[6:9])
    sample_count = width * height * channels
    payload = response_image[68:]
    if int(header[0]) == 1012247:
        values = fpzip.decompress(payload, order="C").astype(np.float16).reshape(-1)
        values = values[:sample_count]
    else:
        values = np.frombuffer(payload, dtype=np.float16, count=sample_count)
    if values.size != sample_count:
        raise ValueError(
            f"Draw Things returned an invalid image payload: "
            f"expected {sample_count} values, got {values.size}"
        )
    pixels = np.clip((values + 1) * 127.5, 0, 255).astype(np.uint8)
    mode = "RGBA" if channels == 4 else "RGB"
    image = Image.frombytes(mode, (width, height), pixels.tobytes())
    from io import BytesIO

    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _channel(target: str, use_tls: bool):
    import grpc
    from credentials import credentials

    options = [
        ("grpc.max_send_message_length", -1),
        ("grpc.max_receive_message_length", -1),
    ]
    if use_tls:
        return grpc.aio.secure_channel(target, credentials, options=options)
    return grpc.aio.insecure_channel(target, options=options)


def _model_files(echo_reply) -> list[str]:
    import json

    try:
        raw_models = bytes(echo_reply.override.models or b"")
        models = json.loads(raw_models.decode("utf-8")) if raw_models else []
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        models = []

    # Older/newer server builds may expose the model browser as EchoReply.files
    # instead of MetadataOverride.models. Keep both forms compatible.
    if not models:
        models = list(getattr(echo_reply, "files", ()) or ())

    files = []
    for model in models if isinstance(models, list) else []:
        if isinstance(model, str):
            filename = model.strip()
        elif isinstance(model, dict):
            filename = str(model.get("file") or "").strip()
        else:
            filename = ""
        if filename and filename not in files:
            files.append(filename)
    return files


async def list_draw_things_models(endpoint: str = "") -> dict:
    """Read the live model list exposed by gRPCServerCLI's model browser."""
    import grpc
    from generated import imageService_pb2, imageService_pb2_grpc

    host, port, use_tls, shared_secret = _settings(endpoint)
    target = f"{host}:{port}"
    try:
        async with _channel(target, use_tls) as channel:
            stub = imageService_pb2_grpc.ImageGenerationServiceStub(channel)
            echo_request = imageService_pb2.EchoRequest(name="Infinite-Canvas")
            if shared_secret:
                echo_request.sharedSecret = shared_secret
            reply = await stub.Echo(echo_request, timeout=10)
            return {
                "connected": True,
                "models": _model_files(reply),
                "host": host,
                "port": port,
            }
    except grpc.aio.AioRpcError as exc:
        return {
            "connected": False,
            "models": [],
            "host": host,
            "port": port,
            "error": (
                "无法连接 Draw Things gRPCServerCLI。请确认服务已由用户手动启动，"
                f"并检查地址 {host}:{port}、TLS 和 shared secret 配置。"
                f" gRPC={exc.code().name}: {exc.details()}"
            ),
        }


async def generate_draw_things_image(
    prompt: str,
    size: str,
    model: str = "",
    reference_images: list[dict] | None = None,
    endpoint: str = "",
    seed: int | None = None,
    strength: float | None = None,
    hint_images: list[dict] | None = None,
    hint_type: str = "shuffle",
    hint_weights: list[object] | None = None,
    batch_size: int = 1,
) -> tuple[dict, dict]:
    """Generate one image and return the project's standard image item shape."""
    import grpc
    from generated import imageService_pb2, imageService_pb2_grpc

    references = [item for item in (reference_images or []) if item]
    if len(references) > 1:
        raise RuntimeError(
            "当前 Draw Things 模型不支持多图图像编辑，请只保留一张输入图，或切换到 Klein/Qwen Edit 模型。"
        )
    hints = [item for item in (hint_images or []) if item]
    if references and hints:
        raise RuntimeError(
            "Draw Things gRPC 请求不能同时使用普通图生图 image 和 Hint 输入。"
        )

    host, port, use_tls, shared_secret = _settings(endpoint)
    target = f"{host}:{port}"
    selected_model = str(model or "").strip()
    if not selected_model:
        raise RuntimeError(
            "Draw Things gRPCServerCLI 未选择模型。请连接服务后从实时模型列表中选择。"
        )
    width, height = _parse_size(size)
    input_image = None
    image_strength = None
    if references:
        # Ordinary image-to-image is carried by request.image. It must remain
        # separate from HintProto, which is reserved for later control inputs.
        input_image = _encode_image_for_request(references[0], width, height)
        image_strength = _parse_strength(
            strength
            if strength is not None
            else os.getenv("DRAW_THINGS_GRPC_STRENGTH", "0.75")
        )
    request_hints = _build_hint_protos(
        hints,
        width,
        height,
        hint_type=hint_type,
        weights=hint_weights,
    ) if hints else []

    try:
        async with _channel(target, use_tls) as channel:
            stub = imageService_pb2_grpc.ImageGenerationServiceStub(channel)
            echo_request = imageService_pb2.EchoRequest(name="Infinite-Canvas")
            if shared_secret:
                echo_request.sharedSecret = shared_secret
            echo_reply = await stub.Echo(echo_request, timeout=10)
            available_models = _model_files(echo_reply)
            if available_models and selected_model not in available_models:
                raise RuntimeError(
                    f"Draw Things 模型不可用：{selected_model}。"
                    "请从当前 gRPCServerCLI 模型列表中重新选择。"
                )

            request = imageService_pb2.ImageGenerationRequest(
                image=input_image or b"",
                scaleFactor=1,
                hints=request_hints,
                prompt=str(prompt or ""),
                negativePrompt="",
                configuration=_build_configuration(
                    selected_model,
                    width,
                    height,
                    seed,
                    strength=image_strength,
                    batch_size=batch_size,
                ),
                user="Infinite-Canvas",
                device=imageService_pb2.LAPTOP,
            )
            if shared_secret:
                request.sharedSecret = shared_secret

            generated = []
            async for response in stub.GenerateImage(request, timeout=1800):
                generated.extend(response.generatedImages)
            if not generated:
                raise RuntimeError("Draw Things gRPCServerCLI 未返回图片。")

            generated_items = []
            for generated_image in generated:
                png = _decode_response_image(generated_image)
                generated_items.append({
                    "b64_json": base64.b64encode(png).decode("ascii"),
                    "mime_type": "image/png",
                })
            image_item = {
                "type": "b64",
                "value": generated_items[0]["b64_json"],
                "mime_type": "image/png",
            }
            return image_item, {
                "provider": "drawthings",
                "model": selected_model,
                "width": width,
                "height": height,
                "image_to_image": bool(input_image),
                "strength": image_strength,
                "hint_type": str(hint_type or "").strip().lower() if hints else "",
                "hint_count": len(hints),
                "hint_weights": [
                    float(tensor.weight)
                    for hint in request_hints
                    for tensor in hint.tensors
                ],
                "images": generated_items,
            }
    except grpc.aio.AioRpcError as exc:
        raise RuntimeError(
            "无法连接 Draw Things gRPCServerCLI。请确认服务已由用户手动启动，"
            f"并检查地址 {host}:{port}、TLS 和 shared secret 配置。"
            f" gRPC={exc.code().name}: {exc.details()}"
        ) from exc
