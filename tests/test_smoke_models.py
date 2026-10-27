"""The model smoke check reports failures without secrets, without network calls."""

import io
import json
from collections.abc import Callable
from urllib.request import Request

import pytest

from scripts import smoke_models
from tests.test_meta_images import KEY, http_error


def fake_urlopen(outcome: object, sent: list[Request]) -> Callable[[Request, float], io.BytesIO]:
    """A urlopen that records each request and returns or raises the outcome."""

    def urlopen(request: Request, timeout: float) -> io.BytesIO:
        sent.append(request)
        if isinstance(outcome, BaseException):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode())

    return urlopen


def test_calls_no_provider_when_a_key_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Request] = []
    monkeypatch.setattr(smoke_models, "urlopen", fake_urlopen({}, sent))
    monkeypatch.setenv("MODEL_API_KEY", KEY)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="Set BASETEN_API_KEY in .env"):
        smoke_models.main()
    assert sent == []


def test_reports_a_rejected_request_without_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"error": {"type": "authentication_error", "message": "invalid key"}})
    sent: list[Request] = []
    monkeypatch.setattr(smoke_models, "urlopen", fake_urlopen(http_error(401, body.encode()), sent))
    result = smoke_models.check_baseten(KEY, smoke_models.BASETEN_URL, smoke_models.BASETEN_MODEL)
    assert result["status"] == "failed"
    assert result["error"] == "HTTP 401 (authentication_error): invalid key"
    assert KEY not in json.dumps(result)
    assert sent[0].get_header("Authorization") == f"Bearer {KEY}"


def test_reports_the_reply_and_token_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11}
    reply = {"choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}]}
    sent: list[Request] = []
    monkeypatch.setattr(smoke_models, "urlopen", fake_urlopen(reply | {"usage": usage}, sent))
    result = smoke_models.check_baseten(KEY, "https://example.test/v1/", "a-model")
    assert (result["status"], result["reply"], result["usage"]) == ("ok", "ready", usage)
    assert sent[0].full_url == "https://example.test/v1/chat/completions"
    assert json.loads(sent[0].data)["model"] == "a-model"
