"""Request-scoped photo cleanup without model calls or filesystem storage."""

import asyncio
from collections.abc import Sequence

import pytest

from backend.app.meta_images import ImageRequestError, ImageResult, InputImage
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


def jpeg(*segments: bytes) -> bytearray:
    scan_header = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
    return bytearray(b"\xff\xd8" + b"".join(segments) + scan_header + b"pixels\xff\xd9")


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
