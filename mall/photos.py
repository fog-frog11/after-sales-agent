"""验证售后照片大小和图像类型，生成用于核验重试的文件摘要。"""

import base64
import binascii
import hashlib
import struct
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

from app.tickets.service_requests import ServiceError


MAX_PHOTOS = 3
MAX_PHOTO_BYTES = 1_000_000
MAX_PHOTO_JSON = (4 * ((MAX_PHOTO_BYTES + 2) // 3) * MAX_PHOTOS) + 8192
PHOTO_ACTIONS = frozenset({"evidence", "provide_evidence", "dispute", "review", "revise", "reject",
                           "report_delivery_problem", "request_delivery_review"})


class PhotoInput(BaseModel):
    """接收浏览器上传的一张 JPEG 或 PNG。"""

    mime: Literal["image/jpeg", "image/png"]
    data: Annotated[str, StringConstraints(min_length=1, max_length=4 * ((MAX_PHOTO_BYTES + 2) // 3))]


def jpeg_size(image: bytes) -> tuple[int, int]:
    """读取 JPEG 扫描前的帧尺寸，拒绝异常段和过大图片。"""
    if not (image.startswith(b"\xff\xd8\xff") and image.endswith(b"\xff\xd9")):
        return 0, 0
    pos = 2
    while pos + 4 <= len(image) and image[pos] == 255:
        pos += 1
        if image[pos] == 255:
            continue
        marker = image[pos]
        size = int.from_bytes(image[pos + 1:pos + 3], "big")
        if size < 2 or pos + 1 + size > len(image) or marker == 218:
            break
        if marker in (192, 193, 194) and size >= 8:
            return struct.unpack(">HH", image[pos + 4:pos + 8])
        pos += 1 + size
    return 0, 0


def decode_photos(photos: list[PhotoInput]) -> list[tuple[str, bytes]]:
    """校验图片编码、格式和大小，统一拒绝混入的文本或向量图。"""
    decoded = []
    for photo in photos:
        try:
            image = base64.b64decode(photo.data, validate=True)
        except (ValueError, binascii.Error):
            raise ServiceError(400, "照片编码有误，请重新选择照片。") from None
        valid_size = 24 <= len(image) <= MAX_PHOTO_BYTES
        if photo.mime == "image/png":
            valid_type = (image.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
                          and image.endswith(b"\x00\x00\x00\x00IEND\xaeB\x60\x82"))
            if valid_type and valid_size:
                width, height = struct.unpack(">II", image[16:24])
                valid_type = 0 < width <= 4096 and 0 < height <= 4096
        else:
            width, height = jpeg_size(image) if valid_size else (0, 0)
            valid_type = 0 < width <= 4096 and 0 < height <= 4096
        if not (valid_size and valid_type):
            raise ServiceError(400, "请上传有效的 JPEG 或 PNG 照片，每张最大 1 MB。")
        decoded.append((photo.mime, image))
    return decoded


def fingerprints(photos: list[tuple[str, bytes]]) -> list[str]:
    """比较申请和操作重试的照片顺序、类型及内容。"""
    return [mime + ":" + hashlib.sha256(image).hexdigest() for mime, image in photos]

