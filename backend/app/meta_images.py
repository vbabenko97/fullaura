"""Muse Image client with explicit timeouts, bounded retries and usage accounting.

Errors and results never carry the API key or image bytes, so callers can log them. JPEG
inputs lose their metadata, such as GPS location, before they are sent.
"""

from __future__ import annotations

import base64
import http.client
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API_URL = "https://api.meta.ai/v1"
# Rate limits, timeouts and server errors are worth another attempt; other errors are not.
RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class ImageRequestError(RuntimeError):
    def __init__(self, message: str, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts


@dataclass(frozen=True, slots=True)
class InputImage:
    data: bytes
    mime_type: str  # for example "image/jpeg"


@dataclass(frozen=True, slots=True)
class ImageResult:
    image: bytes
    mime_type: str
    attempts: int
    latency_s: float
    usage: dict[str, Any]
    revised_prompt: str | None

    @property
    def extension(self) -> str:
        return self.mime_type.removeprefix("image/")


def image_mime_type(data: bytes) -> str | None:
    """PNG, JPEG or WebP from the file's first bytes, which the API cannot mislabel."""
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _exif_orientation(segment: bytes) -> int | None:
    """The orientation tag of an APP1 Exif segment, or None when it has none."""
    body = segment[4:]
    if not body.startswith(b"Exif\x00\x00"):
        return None
    tiff = body[6:]
    order = "big" if tiff[:2] == b"MM" else "little"
    ifd = int.from_bytes(tiff[4:8], order)
    for entry in range(int.from_bytes(tiff[ifd : ifd + 2], order)):
        start = ifd + 2 + 12 * entry
        if int.from_bytes(tiff[start : start + 2], order) == 0x0112:
            return int.from_bytes(tiff[start + 8 : start + 10], order)
    return None


def strip_jpeg_metadata(data: bytes) -> bytes:
    """Keep the pixels, color profile and coding tables of a JPEG; drop Exif (with GPS
    location and device), XMP, IPTC, comments and images appended after the main one."""
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("Not a JPEG image")
    kept = bytearray(b"\xff\xd8")
    position = 2
    while position + 4 <= len(data) and data[position] == 0xFF:
        marker = data[position + 1]
        if marker == 0xDA:  # start of scan: image data runs to the first end-of-image marker
            end = data.find(b"\xff\xd9", position)
            if end < 0:
                break
            return bytes(kept + data[position : end + 2])
        length = int.from_bytes(data[position + 2 : position + 4], "big")
        segment = data[position : position + 2 + length]
        if marker == 0xE1 and _exif_orientation(segment) not in (None, 1):
            raise ValueError("Save the photo upright first; its rotation lives in metadata")
        is_app = 0xE0 <= marker <= 0xEF
        keep_app = marker in (0xE0, 0xEE) or (
            marker == 0xE2 and segment[4:16] == b"ICC_PROFILE\x00"
        )
        if (not is_app and marker != 0xFE) or keep_app:
            kept += segment
        position += 2 + length
    raise ValueError("Malformed JPEG image")


def _post_json(request: Request, timeout_s: float) -> dict[str, Any]:
    with urlopen(request, timeout=timeout_s) as response:
        return json.load(response)


def _error_detail(error: HTTPError) -> str:
    """The provider's error type, code and a short message; never the request body."""
    try:
        info = json.loads(error.read())
        info = info.get("error", info)
        labels = ", ".join(str(info[key]) for key in ("type", "code") if info.get(key))
        message = str(info.get("message", ""))[:200]
    except (ValueError, AttributeError, OSError):
        return ""
    return f" ({labels}): {message}" if labels else f": {message}" if message else ""


@dataclass(slots=True)
class MuseImageClient:
    api_key: str = field(repr=False)
    model: str
    # urllib applies this to each network operation (connect, send, each read), not to the
    # whole call, so a server that keeps sending slowly can take longer.
    timeout_s: float = 120.0
    max_attempts: int = 3
    post: Callable[[Request, float], dict[str, Any]] = _post_json
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError("MODEL_API_KEY is not set")
        if not self.model:
            raise ValueError("No image model is set")
        if "contributor" in self.model:
            raise ValueError("Use a Standard-mode image model for personal photographs (D-07)")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

    def generate(self, prompt: str, *, size: str | None = None) -> ImageResult:
        return self._call("images/generations", {"prompt": prompt}, size)

    def edit(
        self, prompt: str, images: Sequence[InputImage], *, size: str | None = None
    ) -> ImageResult:
        if not images:
            raise ValueError("An edit needs at least one input image")
        items = []
        for image in images:
            data = image.data
            if image.mime_type == "image/jpeg":
                data = strip_jpeg_metadata(data)  # location and device never leave the machine
            encoded = base64.b64encode(data).decode()
            items.append({"image_url": f"data:{image.mime_type};base64,{encoded}"})
        return self._call("images/edits", {"prompt": prompt, "images": items}, size)

    def _call(self, path: str, fields: dict[str, Any], size: str | None) -> ImageResult:
        # b64_json keeps signed image URLs out of results and logs.
        body = {"model": self.model, "n": 1, "response_format": "b64_json", **fields}
        if size:
            body["size"] = size
        request = Request(
            f"{API_URL}/{path}",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        for attempt in range(1, self.max_attempts + 1):
            try:
                payload = self.post(request, self.timeout_s)
            except HTTPError as error:
                failure = f"HTTP {error.code}{_error_detail(error)}"
                if error.code not in RETRY_STATUS:
                    raise ImageRequestError(failure, attempt) from None
            except (OSError, http.client.HTTPException) as error:
                # Timeouts, refused connections, and resets while the reply is read,
                # which urllib raises without wrapping them in URLError.
                failure = f"{type(error).__name__}: {getattr(error, 'reason', error)}"
            except ValueError:
                raise ImageRequestError("The response was not valid JSON", attempt) from None
            else:
                return self._result(payload, attempt, time.monotonic() - started)
            if attempt < self.max_attempts:
                self.sleep(min(2**attempt, 20))
        raise ImageRequestError(f"Gave up after {self.max_attempts} attempts: {failure}", attempt)

    @staticmethod
    def _result(payload: dict[str, Any], attempts: int, latency_s: float) -> ImageResult:
        data = payload.get("data") or []
        if not data or not data[0].get("b64_json"):
            raise ImageRequestError("The response contained no image", attempts)
        image = base64.b64decode(data[0]["b64_json"])
        mime_type = image_mime_type(image)
        if mime_type is None:
            raise ImageRequestError("The response image is not PNG, JPEG or WebP", attempts)
        return ImageResult(
            image=image,
            mime_type=mime_type,
            attempts=attempts,
            latency_s=round(latency_s, 2),
            usage=payload.get("usage") or {},
            revised_prompt=data[0].get("revised_prompt"),
        )
