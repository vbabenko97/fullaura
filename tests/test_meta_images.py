"""Failure handling of the Muse Image client, without network calls."""

import base64
import http.client
import importlib.util
import io
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from backend.app.meta_images import (
    ImageRequestError,
    InputImage,
    MuseImageClient,
    strip_jpeg_metadata,
)

KEY = "secret-test-key"
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 "
OK = {"data": [{"b64_json": base64.b64encode(WEBP).decode()}], "usage": {"total_tokens": 7}}
PROGRESSIVE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4R"
    "DgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQU"
    "FBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wgARCAACAAMDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAA"
    "AAb/xAAVAQEBAAAAAAAAAAAAAAAAAAAGB//aAAwDAQACEAMQAAABlxRAn//EABQQAQAAAAAAAAAAAAAAAAAAAAD/"
    "2gAIAQEAAQUCf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQMBAT8Bf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/"
    "2gAIAQIBAT8Bf//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEABj8Cf//EABQQAQAAAAAAAAAAAAAAAAAAAAD/"
    "2gAIAQEAAT8hf//aAAwDAQACAAMAAAAQB//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQMBAT8Qf//EABQRAQAA"
    "AAAAAAAAAAAAAAAAAAD/2gAIAQIBAT8Qf//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAT8Qf//Z"
)


def segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


def jpeg(*segments: bytes, tail: bytes = b"") -> bytes:
    """A JPEG skeleton: start, segments, a scan of fake pixels, end, and anything after it."""
    scan_header = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
    return b"\xff\xd8" + b"".join(segments) + scan_header + b"pixels\xff\xd9" + tail


def exif(orientation: int) -> bytes:
    """An Exif segment with one orientation tag, followed by fake GPS data."""
    entry = b"\x01\x12\x00\x03\x00\x00\x00\x01" + orientation.to_bytes(2, "big") + b"\x00\x00"
    tiff = b"MM\x00\x2a\x00\x00\x00\x08\x00\x01" + entry + b"\x00\x00\x00\x00GPS 00.00N 00.00E"
    return segment(0xE1, b"Exif\x00\x00" + tiff)


def http_error(code: int, body: bytes = b"{}") -> HTTPError:
    return HTTPError("https://api.meta.ai/v1/images/edits", code, "error", {}, io.BytesIO(body))


def client(*outcomes: Any) -> tuple[MuseImageClient, list[Request], list[float]]:
    """A client whose transport returns or raises each outcome in turn."""
    sent: list[Request] = []
    slept: list[float] = []
    queue = list(outcomes)

    def post(request: Request, timeout_s: float) -> dict[str, Any]:
        sent.append(request)
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return MuseImageClient(KEY, "muse-image-1.0", post=post, sleep=slept.append), sent, slept


def test_retries_a_rate_limit_then_returns_the_image_and_usage() -> None:
    images, sent, slept = client(http_error(429), OK)
    result = images.edit("Swap the shirt", [InputImage(jpeg(), "image/jpeg")])
    assert (result.image, result.mime_type, result.extension) == (WEBP, "image/webp", "webp")
    assert (result.attempts, result.usage, len(sent), slept) == (2, {"total_tokens": 7}, 2, [2])
    body = json.loads(sent[0].data)
    assert body["response_format"] == "b64_json"
    assert body["images"][0]["image_url"].startswith("data:image/jpeg;base64,")


def test_sends_jpeg_pixels_without_location_or_other_metadata() -> None:
    jfif = segment(0xE0, b"JFIF\x00\x01\x02\x00\x00H\x00H\x00\x00")
    color_profile = segment(0xE2, b"ICC_PROFILE\x00\x01\x01profile")
    tables = segment(0xDB, b"\x00tables")
    iptc = segment(0xED, b"Photoshop 3.0\x00IPTC")
    comment = segment(0xFE, b"comment")
    photo = jpeg(jfif, exif(1), iptc, color_profile, comment, tables, tail=b"\xff\xd8gain map")
    images, sent, _ = client(OK)
    images.edit("Swap the shirt", [InputImage(photo, "image/jpeg")])
    data_uri = json.loads(sent[0].data)["images"][0]["image_url"]
    assert base64.b64decode(data_uri.split(",", 1)[1]) == jpeg(jfif, color_profile, tables)


def test_strips_all_metadata_app_segments_without_changing_allowed_image_data() -> None:
    jfif = segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    xmp = segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00metadata")
    iptc = segment(0xED, b"Photoshop 3.0\x00IPTC")
    color_profile = segment(0xE2, b"ICC_PROFILE\x00\x01\x01profile")
    tables = segment(0xDB, b"\x00tables")
    comment = segment(0xFE, b"comment")

    assert strip_jpeg_metadata(
        jpeg(jfif, xmp, iptc, color_profile, comment, tables, tail=b"\xff\xd8gain map")
    ) == jpeg(jfif, color_profile, tables)


def _sos_positions(data: bytes) -> list[int]:
    return [index for index in range(len(data) - 1) if data[index : index + 2] == b"\xff\xda"]


def test_strips_markers_between_progressive_scans_and_preserves_decodability() -> None:
    assert len(_sos_positions(PROGRESSIVE_JPEG)) > 1
    first_scan_end = _sos_positions(PROGRESSIVE_JPEG)[1]
    private = segment(0xFE, b"private-between-scans") + segment(
        0xE1, b"http://ns.adobe.com/xap/1.0/\x00private-xmp"
    )
    photo = PROGRESSIVE_JPEG[:first_scan_end] + private + PROGRESSIVE_JPEG[first_scan_end:]
    images, sent, _ = client(OK)
    images.edit("Swap the shirt", [InputImage(photo, "image/jpeg")])
    sanitized = base64.b64decode(
        json.loads(sent[0].data)["images"][0]["image_url"].split(",", 1)[1]
    )
    assert sanitized == PROGRESSIVE_JPEG
    assert b"private-between-scans" not in sanitized
    assert b"private-xmp" not in sanitized
    if importlib.util.find_spec("PIL"):
        from PIL import Image

        with Image.open(io.BytesIO(sanitized)) as decoded:
            decoded.load()
            assert decoded.size == (3, 2)


def test_sanitizes_app0_and_app14_payloads() -> None:
    private_app0 = segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00private-app0")
    private_app14 = segment(0xEE, b"Adobe123456\x00private-app14")
    photo = PROGRESSIVE_JPEG[:2] + private_app0 + private_app14 + PROGRESSIVE_JPEG[2:]
    images, sent, _ = client(OK)
    images.edit("Swap the shirt", [InputImage(photo, "image/jpeg")])
    sanitized = base64.b64decode(
        json.loads(sent[0].data)["images"][0]["image_url"].split(",", 1)[1]
    )
    expected = (
        PROGRESSIVE_JPEG[:2]
        + segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
        + segment(0xEE, b"Adobe\x00d\x00\x00\x00\x00\x00")
        + PROGRESSIVE_JPEG[2:]
    )
    assert sanitized == expected
    assert b"private-app0" not in sanitized
    assert b"private-app14" not in sanitized


def test_drops_unrecognized_app0_and_app14_segments() -> None:
    photo = (
        PROGRESSIVE_JPEG[:2]
        + segment(0xE0, b"private-unknown-app0")
        + segment(0xEE, b"private-unknown-app14")
        + PROGRESSIVE_JPEG[2:]
    )
    images, sent, _ = client(OK)
    images.edit("Swap the shirt", [InputImage(photo, "image/jpeg")])
    sanitized = base64.b64decode(
        json.loads(sent[0].data)["images"][0]["image_url"].split(",", 1)[1]
    )
    assert sanitized == PROGRESSIVE_JPEG


def test_preserves_stuffed_bytes_and_restart_markers_inside_scan() -> None:
    photo = jpeg(tail=b"")
    scan_end = photo.index(b"pixels") + len(b"pixels")
    photo = photo[:scan_end] + b"\xff\x00\xff\xd0" + photo[scan_end:]
    result = strip_jpeg_metadata(photo)
    assert b"pixels\xff\x00\xff\xd0\xff\xd9" in result


@pytest.mark.parametrize("orientation", range(2, 9))
def test_refuses_a_jpeg_that_relies_on_rotation_metadata(orientation: int) -> None:
    images, sent, _ = client(OK)
    with pytest.raises(ValueError, match="upright"):
        images.edit("Swap the shirt", [InputImage(jpeg(exif(orientation)), "image/jpeg")])
    assert sent == []


def test_refuses_exif_orientation_after_marker_fill_bytes() -> None:
    images, sent, _ = client(OK)
    filled_exif = b"\xff\xff" + exif(6)
    with pytest.raises(ValueError, match="upright"):
        images.edit("Swap the shirt", [InputImage(jpeg(filled_exif), "image/jpeg")])
    assert sent == []


def test_refuses_truncated_exif_before_reading_an_unbounded_entry_count() -> None:
    images, sent, _ = client(OK)
    photo = jpeg(segment(0xE1, b"Exif\x00\x00MM"))

    with pytest.raises(ValueError, match="Malformed JPEG Exif"):
        images.edit("Swap the shirt", [InputImage(photo, "image/jpeg")])

    assert sent == []


def test_retries_a_connection_reset_while_reading_the_reply() -> None:
    reset = http.client.RemoteDisconnected("Remote end closed connection")
    images, sent, _ = client(reset, http.client.IncompleteRead(b""), OK)
    assert images.generate("A navy sweater").attempts == 3
    assert len(sent) == 3


def test_stops_after_the_attempt_limit_without_a_final_sleep() -> None:
    images, sent, slept = client(http_error(503), URLError("reset"), TimeoutError())
    with pytest.raises(ImageRequestError, match="Gave up after 3 attempts") as failure:
        images.generate("A navy sweater")
    assert (failure.value.attempts, len(sent), slept) == (3, 3, [2, 4])


def test_does_not_retry_a_rejected_request_and_keeps_secrets_out() -> None:
    message = f"bad size for key {KEY}"  # some providers repeat the key in their errors
    body = json.dumps({"error": {"type": "invalid_request_error", "message": message}})
    images, sent, _ = client(http_error(400, body.encode()))
    with pytest.raises(ImageRequestError, match="HTTP 400 \\(invalid_request_error\\)") as failure:
        images.generate("A navy sweater")
    assert len(sent) == 1
    assert str(failure.value).endswith("bad size for key [redacted]")
    assert KEY not in str(failure.value)
    assert KEY not in repr(images)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"data": []}, "no image"),
        ({"data": [{"b64_json": base64.b64encode(b"not an image").decode()}]}, "not PNG"),
    ],
)
def test_a_response_without_a_usable_image_is_an_error(
    payload: dict[str, Any], message: str
) -> None:
    images, _, _ = client(payload)
    with pytest.raises(ImageRequestError, match=message):
        images.generate("A navy sweater")


def test_a_reply_that_is_not_json_is_an_error() -> None:
    images, sent, _ = client(json.JSONDecodeError("Expecting value", "<html>", 0))
    with pytest.raises(ImageRequestError, match="not valid JSON"):
        images.generate("A navy sweater")
    assert len(sent) == 1


@pytest.mark.parametrize(
    ("model", "message"),
    [("", "No image model"), ("muse-spark-1.3-contributor", "Standard-mode")],
)
def test_refuses_contributor_or_missing_models(model: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        MuseImageClient(KEY, model)


def test_requires_an_api_key() -> None:
    with pytest.raises(ValueError, match="MODEL_API_KEY"):
        MuseImageClient("", "muse-image-1.0")
