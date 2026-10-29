"""Request-scoped photo processing for previews that remain gated on T-02."""

from __future__ import annotations

import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from backend.app.meta_images import ImageResult, InputImage, image_mime_type, strip_jpeg_metadata

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PREVIEW_BYTES = 16 * 1024 * 1024
PHOTO_MIME_TYPE = "image/jpeg"


class ImageEditor(Protocol):
    def edit(
        self, prompt: str, images: Sequence[InputImage], *, size: str | None = None
    ) -> ImageResult: ...


class PhotoInputError(ValueError):
    """An uploaded photo cannot be processed safely."""


class PhotoProcessingError(RuntimeError):
    """The provider did not return a usable preview image."""


@dataclass(slots=True)
class PreviewImage:
    data: bytearray
    mime_type: str

    def discard(self) -> None:
        self.data.clear()


def _clear_exception_frames(error: BaseException) -> None:
    pending = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if current.__traceback__ is not None:
            traceback.clear_frames(current.__traceback__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)


def create_preview(
    prompt: str,
    portrait: bytearray,
    full_body: bytearray,
    editor: ImageEditor,
) -> PreviewImage:
    """Edit two upright JPEGs and release every input buffer when the provider returns."""
    prepared: list[InputImage] = []
    cleaned: bytes | None = None
    result: ImageResult | None = None
    actual_mime_type: str | None = None
    try:
        if not prompt.strip():
            raise PhotoInputError("A preview prompt is required")
        for photo in (portrait, full_body):
            if not photo or len(photo) > MAX_PHOTO_BYTES:
                raise PhotoInputError("Each photo must be a non-empty JPEG no larger than 10 MiB")
            if image_mime_type(photo) != PHOTO_MIME_TYPE:
                raise PhotoInputError("Photos must be JPEG images")
            try:
                cleaned = strip_jpeg_metadata(photo)
            except ValueError as error:
                raise PhotoInputError(str(error)) from None
            prepared.append(InputImage(cleaned, PHOTO_MIME_TYPE))

        result = editor.edit(prompt, prepared)
        actual_mime_type = image_mime_type(result.image)
        if (
            not result.image
            or len(result.image) > MAX_PREVIEW_BYTES
            or actual_mime_type != result.mime_type
        ):
            raise PhotoProcessingError("The provider returned an invalid preview image")
        return PreviewImage(bytearray(result.image), result.mime_type)
    except BaseException as error:
        _clear_exception_frames(error)
        raise
    finally:
        portrait.clear()
        full_body.clear()
        prepared.clear()
        cleaned = None
        result = None
        actual_mime_type = None


class PreviewResponse(Response):
    """Send an in-memory preview without caching it, then discard its buffer."""

    def __init__(self, preview: PreviewImage) -> None:
        super().__init__(content=b"", status_code=200, media_type=preview.mime_type)
        self.preview = preview
        self.headers["Content-Length"] = str(len(preview.data))
        self.headers["Cache-Control"] = "no-store"
        self.headers["X-Content-Type-Options"] = "nosniff"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await send({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
            await send({"type": "http.response.body", "body": bytes(self.preview.data)})
        finally:
            self.preview.discard()
