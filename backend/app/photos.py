"""Request-scoped photo processing for previews that remain gated on T-02."""

from __future__ import annotations

import asyncio
import time
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from backend.app.meta_images import (
    ImageResult,
    InputImage,
    image_mime_type,
    strip_jpeg_metadata,
)

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PREVIEW_BYTES = 16 * 1024 * 1024
MAX_PREVIEW_PROMPT_BYTES = 3000
MAX_PREVIEW_REQUEST_BYTES = MAX_PHOTO_BYTES * 2 + 64 * 1024
PREVIEW_DEADLINE_S = 120.0
PHOTO_MIME_TYPE = "image/jpeg"
PREVIEW_FIELDS = {b"portrait", b"full_body", b"prompt"}


class ImageEditor(Protocol):
    def edit(
        self, prompt: str, images: Sequence[InputImage], *, size: str | None = None
    ) -> ImageResult: ...


class AsyncImageEditor(Protocol):
    async def edit_async(
        self,
        prompt: str,
        images: Sequence[InputImage],
        *,
        deadline: float,
    ) -> ImageResult: ...


class PhotoInputError(ValueError):
    """An uploaded photo cannot be processed safely."""


class PhotoTooLargeError(PhotoInputError):
    """The preview request exceeded its in-memory size limit."""


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


async def create_preview_async(
    prompt: str,
    portrait: bytearray,
    full_body: bytearray,
    editor: AsyncImageEditor,
    *,
    deadline: float,
) -> PreviewImage:
    """Edit two upright JPEGs and discard their buffers on return or cancellation."""
    prepared: list[InputImage] = []
    cleaned: bytes | None = None
    result: ImageResult | None = None
    actual_mime_type: str | None = None
    try:
        if not prompt.strip() or len(prompt.encode("utf-8")) > MAX_PREVIEW_PROMPT_BYTES:
            raise PhotoInputError(
                "A preview prompt is required and must be no longer than 3000 bytes"
            )
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

        result = await editor.edit_async(prompt, prepared, deadline=deadline)
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


async def read_preview_uploads(request: Request) -> tuple[str, bytearray, bytearray]:
    """Parse bounded multipart data into memory without creating upload files."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
            if declared_length < 0:
                raise ValueError
            if declared_length > MAX_PREVIEW_REQUEST_BYTES:
                raise PhotoTooLargeError("The preview request must be no larger than 20 MiB")
        except ValueError as error:
            if isinstance(error, PhotoTooLargeError):
                raise
            raise PhotoInputError("Invalid preview request size") from None

    content_type, options = parse_options_header(request.headers.get("content-type", "").encode())
    boundary = options.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary:
        raise PhotoInputError("Send portrait, full_body and prompt as multipart form data")

    headers: dict[bytes, bytes] = {}
    header_field = bytearray()
    header_value = bytearray()
    values: dict[bytes, bytearray | str] = {}
    current_name: bytes | None = None
    current_part: bytearray | None = None
    current_is_file = False

    def part_begin() -> None:
        nonlocal current_name, current_part, current_is_file
        headers.clear()
        current_name = None
        current_part = None
        current_is_file = False

    def header_field_data(data: bytes, start: int, end: int) -> None:
        header_field.extend(data[start:end])

    def header_value_data(data: bytes, start: int, end: int) -> None:
        header_value.extend(data[start:end])

    def header_end() -> None:
        name = bytes(header_field).strip().lower()
        if not name or name in headers:
            raise PhotoInputError("Invalid multipart headers")
        headers[name] = bytes(header_value).strip()
        header_field.clear()
        header_value.clear()

    def headers_finished() -> None:
        nonlocal current_name, current_part, current_is_file
        disposition, options = parse_options_header(headers.get(b"content-disposition", b""))
        name = options.get(b"name")
        if disposition != b"form-data" or name not in PREVIEW_FIELDS or name in values:
            raise PhotoInputError(
                "The preview request must contain portrait, full_body and prompt once"
            )
        current_name = name
        current_is_file = name in {b"portrait", b"full_body"}
        content_type = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        if current_is_file and content_type != PHOTO_MIME_TYPE.encode():
            raise PhotoInputError("Photos must be JPEG images")
        if not current_is_file and content_type not in {b"", b"text/plain"}:
            raise PhotoInputError("The preview prompt must be plain text")
        current_part = bytearray()

    def part_data(data: bytes, start: int, end: int) -> None:
        if current_name is None or current_part is None:
            raise PhotoInputError("Invalid multipart form data")
        current_part.extend(data[start:end])
        maximum = MAX_PHOTO_BYTES if current_is_file else MAX_PREVIEW_PROMPT_BYTES
        if len(current_part) > maximum:
            if current_is_file:
                raise PhotoTooLargeError("Each photo must be no larger than 10 MiB")
            raise PhotoInputError("The preview prompt must be no longer than 3000 bytes")

    def part_end() -> None:
        nonlocal current_name, current_part
        if current_name is None or current_part is None:
            raise PhotoInputError("Invalid multipart form data")
        if current_is_file:
            values[current_name] = current_part
        else:
            try:
                values[current_name] = current_part.decode("utf-8")
            except UnicodeDecodeError:
                raise PhotoInputError("The preview prompt must be UTF-8 text") from None
        current_name = None
        current_part = None

    parser = MultipartParser(
        boundary,
        callbacks={
            "on_part_begin": part_begin,
            "on_header_field": header_field_data,
            "on_header_value": header_value_data,
            "on_header_end": header_end,
            "on_headers_finished": headers_finished,
            "on_part_data": part_data,
            "on_part_end": part_end,
        },
        max_size=MAX_PREVIEW_REQUEST_BYTES,
        max_header_count=16,
        max_header_size=4096,
    )
    received = 0
    try:
        try:
            async for chunk in request.stream():
                received += len(chunk)
                if received > MAX_PREVIEW_REQUEST_BYTES:
                    raise PhotoTooLargeError("The preview request must be no larger than 20 MiB")
                parser.write(chunk)
            parser.finalize()
        except MultipartParseError:
            raise PhotoInputError("Invalid multipart form data") from None
        if set(values) != PREVIEW_FIELDS:
            raise PhotoInputError("The preview request must contain portrait, full_body and prompt")
        prompt = values[b"prompt"]
        portrait = values[b"portrait"]
        full_body = values[b"full_body"]
        if (
            not isinstance(prompt, str)
            or not isinstance(portrait, bytearray)
            or not isinstance(full_body, bytearray)
        ):
            raise PhotoInputError("Invalid preview request fields")
        values.clear()
        return prompt, portrait, full_body
    except BaseException as error:
        _clear_exception_frames(error)
        raise
    finally:
        for value in values.values():
            if isinstance(value, bytearray):
                value.clear()
        values.clear()
        headers.clear()
        header_field.clear()
        header_value.clear()
        if current_part is not None:
            current_part.clear()


async def wait_for_preview_or_disconnect(
    request: Request,
    prompt: str,
    portrait: bytearray,
    full_body: bytearray,
    editor: AsyncImageEditor,
    *,
    deadline: float,
) -> PreviewImage | None:
    """Cancel provider work when the caller disconnects or the shared deadline expires."""

    async def wait_for_disconnect() -> None:
        while not await request.is_disconnected():
            await asyncio.sleep(0.05)

    preview_task = asyncio.create_task(
        create_preview_async(prompt, portrait, full_body, editor, deadline=deadline)
    )
    disconnect_task = asyncio.create_task(wait_for_disconnect())
    tasks = (preview_task, disconnect_task)
    try:
        remaining_s = max(0.0, deadline - time.monotonic())
        done, _ = await asyncio.wait(
            tasks,
            timeout=remaining_s,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if disconnect_task in done:
            return None
        if preview_task in done:
            return preview_task.result()
        raise TimeoutError("Preview generation took too long")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


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
