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
JPEG_SEGMENT_MARKERS = frozenset({*range(0xC0, 0xC8), *range(0xC9, 0xD0), 0xCC, *range(0xDB, 0xE0)})


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


def image_mime_type(data: bytes | bytearray) -> str | None:
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
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        raise ValueError("Malformed JPEG Exif metadata")
    order = "big" if tiff[:2] == b"MM" else "little"
    if int.from_bytes(tiff[2:4], order) != 42:
        raise ValueError("Malformed JPEG Exif metadata")
    ifd = int.from_bytes(tiff[4:8], order)
    if ifd < 8 or ifd + 2 > len(tiff):
        raise ValueError("Malformed JPEG Exif metadata")
    entry_count = int.from_bytes(tiff[ifd : ifd + 2], order)
    entries_end = ifd + 2 + 12 * entry_count
    if entries_end + 4 > len(tiff):
        raise ValueError("Malformed JPEG Exif metadata")
    for entry in range(entry_count):
        start = ifd + 2 + 12 * entry
        tag = int.from_bytes(tiff[start : start + 2], order)
        if tag == 0x0112:
            field_type = int.from_bytes(tiff[start + 2 : start + 4], order)
            value_count = int.from_bytes(tiff[start + 4 : start + 8], order)
            if field_type != 3 or value_count != 1:
                raise ValueError("Malformed JPEG Exif orientation")
            return int.from_bytes(tiff[start + 8 : start + 10], order)
    return None


def _jpeg_marker_segment(marker: int, payload: bytes) -> bytes:
    return b"\xff" + bytes([marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


def strip_jpeg_metadata(data: bytes | bytearray) -> bytes:
    """Keep JPEG coding data and recognized display metadata; remove other metadata.

    JPEG markers can occur between scans, so each entropy-coded scan is copied only up
    to its next real marker. Stuffed bytes and restart markers remain part of the scan.
    """
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("Not a JPEG image")
    kept = bytearray(b"\xff\xd8")
    position = 2
    saw_scan = False
    icc_chunks: dict[int, tuple[int, bytes]] = {}
    icc_insert_at: int | None = None
    while position < len(data):
        if data[position] != 0xFF:
            raise ValueError("Malformed JPEG image")
        marker_start = position
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position == len(data):
            raise ValueError("Malformed JPEG image")
        marker = data[position]
        position += 1
        if marker == 0xD9:
            if not saw_scan:
                raise ValueError("Malformed JPEG image")
            # ICC chunks are kept only as a complete, consistently numbered set.
            if icc_chunks:
                count = next(iter(icc_chunks.values()))[0]
                if len(icc_chunks) == count and set(icc_chunks) == set(range(1, count + 1)):
                    chunks = b"".join(icc_chunks[index][1] for index in range(1, count + 1))
                    kept[icc_insert_at or 0 : icc_insert_at or 0] = chunks
            kept.extend(data[marker_start:position])
            return bytes(kept)
        if marker == 0x00 or marker in range(0xD0, 0xD8) or marker == 0xD8:
            raise ValueError("Malformed JPEG image")
        if marker == 0x01:
            kept.extend(data[marker_start:position])
            continue
        if position + 2 > len(data):
            raise ValueError("Malformed JPEG image")
        length = int.from_bytes(data[position : position + 2], "big")
        end = position + length
        if length < 2 or end > len(data):
            raise ValueError("Malformed JPEG image")
        segment = data[marker_start:end]
        payload = data[position + 2 : end]
        position = end
        if marker == 0xDA:
            if not payload or len(payload) != 4 + 2 * payload[0] or payload[0] == 0:
                raise ValueError("Malformed JPEG image")
            kept.extend(segment)
            saw_scan = True
            scan_start = position
            while position < len(data):
                if data[position] != 0xFF:
                    position += 1
                    continue
                marker_pos = position
                position += 1
                while position < len(data) and data[position] == 0xFF:
                    position += 1
                if position == len(data):
                    raise ValueError("Malformed JPEG image")
                code = data[position]
                position += 1
                if code == 0x00 or 0xD0 <= code <= 0xD7:
                    continue
                kept.extend(data[scan_start:marker_pos])
                position = marker_pos
                break
            else:
                raise ValueError("Malformed JPEG image")
            continue
        if marker == 0xE1:
            orientation = _exif_orientation(_jpeg_marker_segment(marker, payload))
            if orientation not in (None, 1):
                raise ValueError("Save the photo upright first; its rotation lives in metadata")
        if marker == 0xE0:
            # Keep the recognized JFIF header, but discard its thumbnail and any tail.
            if (
                payload.startswith(b"JFIF\x00")
                and len(payload) >= 14
                and payload[5] == 1
                and payload[7] <= 2
                and int.from_bytes(payload[8:10], "big") > 0
                and int.from_bytes(payload[10:12], "big") > 0
            ):
                kept.extend(_jpeg_marker_segment(marker, payload[:12] + b"\x00\x00"))
        elif marker == 0xEE and payload.startswith(b"Adobe"):
            # Keep only its color transform; canonicalize version and flags.
            if len(payload) >= 12 and payload[11] in (0, 1, 2):
                adobe = b"Adobe\x00\x64\x00\x00\x00\x00" + payload[11:12]
                kept.extend(_jpeg_marker_segment(marker, adobe))
        elif marker == 0xE2 and payload.startswith(b"ICC_PROFILE\x00"):
            # Keep a complete profile set; malformed chunks are discarded.
            if len(payload) >= 14:
                sequence, count = payload[12], payload[13]
                if count and 1 <= sequence <= count:
                    previous = icc_chunks.get(sequence)
                    consistent = all(existing[0] == count for existing in icc_chunks.values())
                    if previous is None and consistent:
                        if icc_insert_at is None:
                            icc_insert_at = len(kept)
                        icc_chunks[sequence] = (count, segment)
        elif 0xE0 <= marker <= 0xEF or marker == 0xFE:
            continue
        elif marker in JPEG_SEGMENT_MARKERS:
            kept.extend(segment)
        else:
            raise ValueError("Unsupported JPEG marker")
    raise ValueError("Malformed JPEG image")


def _post_json(request: Request, timeout_s: float) -> dict[str, Any]:
    with urlopen(request, timeout=timeout_s) as response:
        return json.load(response)


def _error_detail(error: HTTPError, api_key: str) -> str:
    """The provider's error type, code and a short message; never the request body, and
    never the API key, even when the provider repeats it in the message."""
    try:
        info = json.loads(error.read())
        info = info.get("error", info)
        labels = ", ".join(str(info[key]) for key in ("type", "code") if info.get(key))
        message = str(info.get("message", ""))
    except (ValueError, AttributeError, OSError):
        return ""
    if api_key:
        message = message.replace(api_key, "[redacted]")
    message = message[:200]
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
        items: list[dict[str, str]] = []
        fields: dict[str, Any] = {}
        data = b""
        encoded = ""
        image: InputImage | None = None
        try:
            for image in images:
                data = image.data
                if image.mime_type == "image/jpeg":
                    data = strip_jpeg_metadata(data)  # location and device never leave the machine
                encoded = base64.b64encode(data).decode()
                items.append({"image_url": f"data:{image.mime_type};base64,{encoded}"})
            fields = {"prompt": prompt, "images": items}
            return self._call("images/edits", fields, size)
        finally:
            items.clear()
            fields.clear()
            image = None
            data = b""
            encoded = ""
            images = ()

    def _call(self, path: str, fields: dict[str, Any], size: str | None) -> ImageResult:
        # b64_json keeps signed image URLs out of results and logs.
        body: dict[str, Any] = {}
        request: Request | None = None
        payload: dict[str, Any] | None = None
        try:
            body = {"model": self.model, "n": 1, "response_format": "b64_json", **fields}
            if size:
                body["size"] = size
            request = Request(
                f"{API_URL}/{path}",
                data=json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            started = time.monotonic()
            for attempt in range(1, self.max_attempts + 1):
                try:
                    payload = self.post(request, self.timeout_s)
                except HTTPError as error:
                    failure = f"HTTP {error.code}{_error_detail(error, self.api_key)}"
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
            raise ImageRequestError(
                f"Gave up after {self.max_attempts} attempts: {failure}", attempt
            )
        finally:
            if request is not None:
                request.data = None
                request.headers.clear()
                request.unredirected_hdrs.clear()
            body.clear()
            fields.clear()
            if payload is not None:
                payload.clear()
            payload = None

    @staticmethod
    def _result(payload: dict[str, Any], attempts: int, latency_s: float) -> ImageResult:
        data: Any = None
        first: dict[str, Any] | None = None
        image: bytes | None = None
        try:
            data = payload.get("data") or []
            if not data or not data[0].get("b64_json"):
                raise ImageRequestError("The response contained no image", attempts)
            first = data[0]
            image = base64.b64decode(first["b64_json"])
            mime_type = image_mime_type(image)
            if mime_type is None:
                raise ImageRequestError("The response image is not PNG, JPEG or WebP", attempts)
            return ImageResult(
                image=image,
                mime_type=mime_type,
                attempts=attempts,
                latency_s=round(latency_s, 2),
                usage=payload.get("usage") or {},
                revised_prompt=first.get("revised_prompt"),
            )
        finally:
            if first is not None:
                first.clear()
            if isinstance(data, list):
                data.clear()
            payload.clear()
            image = None
