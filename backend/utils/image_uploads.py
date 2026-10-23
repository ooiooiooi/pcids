from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import warnings

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError


AVATAR_MAX_BYTES = 2 * 1024 * 1024
PRODUCT_IMAGE_MAX_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}
_CANONICAL_EXTENSION_TO_MIME = {
    "jpg": "image/jpeg",
    "png": "image/png",
}
_ALLOWED_CONTENT_TYPES = {
    "jpg": {"image/jpeg", "image/pjpeg"},
    "png": {"image/png"},
}


@dataclass(frozen=True)
class SanitizedImage:
    content: bytes
    extension: str
    media_type: str


def validate_image_filename(filename: str) -> str:
    name = Path(str(filename or "")).name
    if not name or name != str(filename or ""):
        raise HTTPException(status_code=400, detail="非法文件名")
    extension = Path(name).suffix.lower().lstrip(".")
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 jpg、jpeg、png 图片")
    return extension


def _canonical_extension(extension: str) -> str:
    lowered = str(extension or "").strip().lower()
    if lowered == "jpeg":
        return "jpg"
    return lowered


def _normalize_declared_content_type(content_type: str | None) -> str | None:
    raw = str(content_type or "").split(";", 1)[0].strip().lower()
    return raw or None


def _is_valid_png(content: bytes) -> bool:
    signature = b"\x89PNG\r\n\x1a\n"
    if not content.startswith(signature):
        return False

    offset = len(signature)
    seen_ihdr = False
    while offset + 12 <= len(content):
        chunk_length = int.from_bytes(content[offset : offset + 4], "big")
        chunk_type = content[offset + 4 : offset + 8]
        chunk_data_start = offset + 8
        chunk_data_end = chunk_data_start + chunk_length
        chunk_crc_end = chunk_data_end + 4
        if chunk_crc_end > len(content):
            return False
        if not seen_ihdr:
            if chunk_type != b"IHDR" or chunk_length != 13:
                return False
            seen_ihdr = True
        offset = chunk_crc_end
        if chunk_type == b"IEND":
            return offset == len(content) and seen_ihdr
    return False


def _is_valid_jpeg(content: bytes) -> bool:
    if len(content) < 4 or content[:2] != b"\xff\xd8":
        return False

    offset = 2
    seen_sof = False
    seen_sos = False
    while offset < len(content):
        if content[offset] != 0xFF:
            return False
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        if offset >= len(content):
            return False

        marker = content[offset]
        offset += 1
        if marker == 0xD9:
            return seen_sof and seen_sos and offset == len(content)
        if marker in {0x01, *range(0xD0, 0xD8)}:
            continue
        if offset + 2 > len(content):
            return False

        segment_length = int.from_bytes(content[offset : offset + 2], "big")
        if segment_length < 2:
            return False
        offset += 2
        segment_end = offset + segment_length - 2
        if segment_end > len(content):
            return False

        if marker in {
            0xC0, 0xC1, 0xC2, 0xC3,
            0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB,
            0xCD, 0xCE, 0xCF,
        }:
            seen_sof = True

        if marker == 0xDA:
            seen_sos = True
            offset = segment_end
            while offset + 1 < len(content):
                if content[offset] != 0xFF:
                    offset += 1
                    continue
                next_byte = content[offset + 1]
                if next_byte == 0x00 or 0xD0 <= next_byte <= 0xD7:
                    offset += 2
                    continue
                break
            continue

        offset = segment_end
    return False


def detect_image_extension(content: bytes) -> str:
    if _is_valid_png(content):
        return "png"
    if _is_valid_jpeg(content):
        return "jpg"
    raise HTTPException(status_code=400, detail="上传内容不是合法图片")


def sanitize_image_upload(
    *,
    filename: str,
    content: bytes,
    content_type: str | None,
    max_bytes: int,
) -> SanitizedImage:
    extension = validate_image_filename(filename)
    canonical_extension = _canonical_extension(extension)
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    if len(content) > max_bytes:
        raise HTTPException(status_code=400, detail=f"文件过大（最大 {max_bytes // (1024 * 1024)}MB）")

    declared_type = _normalize_declared_content_type(content_type)
    actual_extension = detect_image_extension(content)
    if actual_extension != canonical_extension:
        raise HTTPException(status_code=400, detail="文件扩展名与图片真实类型不匹配")
    if declared_type and declared_type not in _ALLOWED_CONTENT_TYPES[actual_extension]:
        raise HTTPException(status_code=400, detail="文件声明类型与图片真实类型不匹配")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                if image.width * image.height > 16_000_000:
                    raise ValueError("Image dimensions exceed limit")
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
                clean = ImageOps.exif_transpose(image).convert("RGB" if actual_extension == "jpg" else "RGBA")
                clean.info.clear()
                output = BytesIO()
                clean.save(output, format="JPEG" if actual_extension == "jpg" else "PNG")
                encoded = output.getvalue()
                if len(encoded) > max_bytes:
                    raise ValueError("Re-encoded image exceeds limit")
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(status_code=400, detail="图片损坏、尺寸过大或无法安全解码") from exc

    return SanitizedImage(
        content=encoded,
        extension=actual_extension,
        media_type=_CANONICAL_EXTENSION_TO_MIME[actual_extension],
    )
