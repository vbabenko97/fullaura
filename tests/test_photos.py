"""Request-scoped photo cleanup without model calls or filesystem storage."""

import asyncio
import base64
import io
from collections.abc import Sequence
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from backend.app.meta_images import ImageRequestError, ImageResult, InputImage, MuseImageClient
from backend.app.photos import (
    MAX_PHOTO_BYTES,
    PHOTO_MIME_TYPE,
    PhotoInputError,
    PhotoProcessingError,
    PreviewImage,
    PreviewResponse,
    create_preview,
)

PREVIEW = b"\x89PNG\r\n\x1a\npreview"


def segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


def jpeg(*segments: bytes, pixels: bytes = b"pixels") -> bytearray:
    scan_header = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
    return bytearray(b"\xff\xd8" + b"".join(segments) + scan_header + pixels + b"\xff\xd9")


class FakeEditor:
    def __init__(self, result: ImageResult | BaseException) -> None:
        self.result = result
        self.prompt: str | None = None
        self.images: list[InputImage] = []

    def edit(
        self, prompt: str, images: Sequence[InputImage], *, size: str | None = None
    ) -> ImageResult:
        self.prompt = prompt
        self.images = list(images)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FailingEditor:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure

    def edit(
        self, prompt: str, images: Sequence[InputImage], *, size: str | None = None
    ) -> ImageResult:
        raise self.failure


def _traceback_locals(error: BaseException) -> list[dict[str, object]]:
    pending = [error]
    visited: set[int] = set()
    locals_by_frame: list[dict[str, object]] = []
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        traceback = current.__traceback__
        while traceback is not None:
            if not traceback.tb_frame.f_code.co_name.startswith("test_"):
                locals_by_frame.append(traceback.tb_frame.f_locals)
            traceback = traceback.tb_next
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
    return locals_by_frame


def _contains_marker(value: object, markers: tuple[bytes, ...], visited: set[int]) -> bool:
    if isinstance(value, (bytes, bytearray)):
        return any(marker in value for marker in markers)
    if isinstance(value, str):
        return any(marker.decode("ascii") in value for marker in markers)
    if id(value) in visited:
        return False
    visited.add(id(value))
    if isinstance(value, Request):
        return _contains_marker(value.data or b"", markers, visited)
    if isinstance(value, InputImage):
        return _contains_marker(value.data, markers, visited)
    if isinstance(value, ImageResult):
        return _contains_marker(value.image, markers, visited)
    if isinstance(value, dict):
        return any(
            _contains_marker(key, markers, visited) or _contains_marker(item, markers, visited)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_marker(item, markers, visited) for item in value)
    return False


def _assert_tracebacks_discard(error: BaseException, *markers: bytes) -> None:
    needles = tuple({marker for value in markers for marker in (value, base64.b64encode(value))})
    for frame_locals in _traceback_locals(error):
        assert not _contains_marker(frame_locals, needles, set())


def result(image: bytes = PREVIEW, mime_type: str = "image/png") -> ImageResult:
    return ImageResult(image, mime_type, 1, 0.1, {}, None)


def test_create_preview_strips_metadata_and_discards_inputs_after_success() -> None:
    tiff = b"MM\x00*\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00private location"
    private = segment(0xE1, b"Exif\x00\x00" + tiff)
    portrait = jpeg(private)
    full_body = jpeg(private)
    editor = FakeEditor(result())

    preview = create_preview("Use the selected look", portrait, full_body, editor)

    assert editor.prompt == "Use the selected look"
    assert [image.mime_type for image in editor.images] == [PHOTO_MIME_TYPE, PHOTO_MIME_TYPE]
    assert all(b"private location" not in image.data for image in editor.images)
    assert portrait == bytearray() and full_body == bytearray()
    assert preview.data == PREVIEW
    preview.discard()
    assert preview.data == bytearray()


@pytest.mark.parametrize(
    "failure",
    [ImageRequestError("provider error", 1), TimeoutError("provider timeout")],
    ids=["provider-error", "timeout"],
)
def test_provider_failure_and_timeout_discard_inputs(failure: BaseException) -> None:
    portrait = jpeg()
    full_body = jpeg()

    with pytest.raises(type(failure)):
        create_preview("Use the selected look", portrait, full_body, FakeEditor(failure))

    assert portrait == bytearray() and full_body == bytearray()


def test_provider_error_traceback_does_not_retain_stripped_photo() -> None:
    marker = b"private-photo-pixel-marker"
    portrait = jpeg(pixels=marker)
    full_body = jpeg()

    with pytest.raises(ImageRequestError) as failure:
        create_preview(
            "Use the selected look",
            portrait,
            full_body,
            FailingEditor(ImageRequestError("provider error", 1)),
        )

    assert portrait == bytearray() and full_body == bytearray()
    _assert_tracebacks_discard(failure.value, marker)


def test_invalid_input_discards_both_photos_before_provider_call() -> None:
    portrait = jpeg()
    full_body = bytearray(b"not a JPEG")
    editor = FakeEditor(result())

    with pytest.raises(PhotoInputError, match="JPEG"):
        create_preview("Use the selected look", portrait, full_body, editor)

    assert portrait == bytearray() and full_body == bytearray()
    assert editor.images == []


def test_oversized_input_is_rejected_and_discarded() -> None:
    portrait = bytearray(MAX_PHOTO_BYTES + 1)
    full_body = jpeg()

    with pytest.raises(PhotoInputError, match="10 MiB"):
        create_preview("Use the selected look", portrait, full_body, FakeEditor(result()))

    assert portrait == bytearray() and full_body == bytearray()


def test_invalid_provider_image_discards_inputs() -> None:
    portrait = jpeg()
    full_body = jpeg()

    with pytest.raises(PhotoProcessingError, match="invalid preview"):
        create_preview(
            "Use the selected look",
            portrait,
            full_body,
            FakeEditor(result(b"invalid", "image/png")),
        )

    assert portrait == bytearray() and full_body == bytearray()


def test_invalid_provider_result_is_not_retained_in_traceback() -> None:
    marker = b"private-provider-preview-marker"
    portrait = jpeg()
    full_body = jpeg()

    with pytest.raises(PhotoProcessingError) as failure:
        create_preview(
            "Use the selected look",
            portrait,
            full_body,
            FakeEditor(result(PREVIEW + marker, "image/jpeg")),
        )

    assert portrait == bytearray() and full_body == bytearray()
    _assert_tracebacks_discard(failure.value, marker)


def test_failed_provider_request_clears_request_body_and_traceback_copies() -> None:
    marker = b"private-request-photo-marker"
    requests: list[Request] = []
    body_was_present: list[bool] = []

    def post(request: Request, timeout_s: float) -> dict[str, object]:
        requests.append(request)
        body_was_present.append(request.data is not None)
        raise HTTPError(
            "https://api.meta.ai/v1/images/edits",
            400,
            "bad request",
            {},
            io.BytesIO(b"{}"),
        )

    editor = MuseImageClient("secret-key", "muse-image-1.0", post=post, max_attempts=1)
    portrait = jpeg(pixels=marker)
    full_body = jpeg()

    with pytest.raises(ImageRequestError) as failure:
        create_preview("Use the selected look", portrait, full_body, editor)

    assert body_was_present == [True]
    assert requests[0].data is None
    assert portrait == bytearray() and full_body == bytearray()
    _assert_tracebacks_discard(failure.value, marker)


@pytest.mark.parametrize("send_failure", [False, True], ids=["success", "send-failure"])
def test_preview_response_discards_image_after_send_or_send_failure(send_failure: bool) -> None:
    preview = PreviewImage(bytearray(PREVIEW), "image/png")
    response = PreviewResponse(preview)
    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)
        if send_failure and message["type"] == "http.response.body":
            raise OSError("client disconnected")

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    if send_failure:
        with pytest.raises(OSError, match="client disconnected"):
            asyncio.run(response({}, receive, send))
    else:
        asyncio.run(response({}, receive, send))

    assert preview.data == bytearray()
    assert sent[0]["type"] == "http.response.start"
    assert sent[1]["body"] == PREVIEW
    assert (b"cache-control", b"no-store") in sent[0]["headers"]
