"""Failure handling of the Muse Image client, without network calls."""

import base64
import http.client
import io
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from backend.app.meta_images import ImageRequestError, InputImage, MuseImageClient

KEY = "secret-test-key"
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 "
OK = {"data": [{"b64_json": base64.b64encode(WEBP).decode()}], "usage": {"total_tokens": 7}}


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
    result = images.edit("Swap the shirt", [InputImage(b"jpeg", "image/jpeg")])
    assert (result.image, result.mime_type, result.extension) == (WEBP, "image/webp", "webp")
    assert (result.attempts, result.usage, len(sent), slept) == (2, {"total_tokens": 7}, 2, [2])
    body = json.loads(sent[0].data)
    assert body["response_format"] == "b64_json"
    assert body["images"][0]["image_url"].startswith("data:image/jpeg;base64,")


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
    body = json.dumps({"error": {"type": "invalid_request_error", "message": "bad size"}})
    images, sent, _ = client(http_error(400, body.encode()))
    with pytest.raises(ImageRequestError, match="HTTP 400 \\(invalid_request_error\\)") as failure:
        images.generate("A navy sweater")
    assert len(sent) == 1
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
