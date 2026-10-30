"""Make one small call to each model provider and report status, latency and token usage.

Muse Spark Open on Baseten answers a one-word text prompt, and Muse Image on Meta draws one
garment, so neither call sends personal data. Meta charges about $0.01 for the image. The
script prints no API key or image bytes, and it saves nothing.

    uv run --frozen --env-file .env python -m scripts.smoke_models
"""

from __future__ import annotations

import http.client
import json
import os
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.app.meta_images import IMAGE_MODEL, ImageRequestError, MuseImageClient, error_detail
from backend.app.planner import BASETEN_URL, SPARK_MODEL

TIMEOUT_S = 60.0
TEXT_PROMPT = "Reply with one word: ready"
IMAGE_PROMPT = "A plain white cotton t-shirt on a wooden hanger, studio product photo"


def check_baseten(api_key: str, base_url: str, model: str) -> dict[str, Any]:
    """One chat completion without retries, so the first failure is the one reported. It
    passes only with a non-empty reply that the model finished."""
    messages = [{"role": "user", "content": TEXT_PROMPT}]
    request = Request(
        f"{base_url.rstrip('/')}/chat/completions",
        # The model reasons before it replies, and its reasoning counts against max_tokens.
        data=json.dumps({"model": model, "messages": messages, "max_tokens": 256}).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urlopen(request, timeout=TIMEOUT_S) as response:
            payload = json.load(response)
        choice = payload["choices"][0]
        reply = str(choice["message"].get("content") or "").strip()
        finished = choice.get("finish_reason") == "stop" and reply
        outcome = {
            "status": "ok" if finished else "failed",
            "finish_reason": choice.get("finish_reason"),
            "reply": reply[:80],
            "usage": payload.get("usage") or {},
        }
        if not finished:
            outcome["error"] = "The reply is empty or was cut off"
    except HTTPError as error:
        detail = error_detail(error, api_key)
        outcome = {"status": "failed", "error": f"HTTP {error.code}{detail}"}
    except (OSError, http.client.HTTPException) as error:
        reason = getattr(error, "reason", error)
        outcome = {"status": "failed", "error": f"{type(error).__name__}: {reason}"}
    except (ValueError, LookupError, AttributeError, TypeError):
        outcome = {"status": "failed", "error": "The response was not a chat completion"}
    latency_s = round(time.monotonic() - started, 2)
    return {"provider": "baseten", "model": model, **outcome, "latency_s": latency_s}


def check_meta(api_key: str, model: str) -> dict[str, Any]:
    """One image through the client the feasibility test uses, with its retries."""
    result: dict[str, Any] = {"provider": "meta", "model": model}
    try:
        image = MuseImageClient(api_key, model).generate(IMAGE_PROMPT)
    except ImageRequestError as error:
        return result | {"status": "failed", "attempts": error.attempts, "error": str(error)}
    return result | {
        "status": "ok",
        "attempts": image.attempts,
        "latency_s": image.latency_s,
        "image": f"{image.mime_type}, {len(image.image)} bytes",
        "usage": image.usage,
    }


def main() -> None:
    keys = {name: os.environ.get(name, "") for name in ("BASETEN_API_KEY", "MODEL_API_KEY")}
    missing = [name for name, value in keys.items() if not value]
    if missing:
        raise SystemExit(f"Set {', '.join(missing)} in .env; no provider was called")
    results = [
        check_baseten(
            keys["BASETEN_API_KEY"],
            os.environ.get("BASETEN_BASE_URL") or BASETEN_URL,
            os.environ.get("BASETEN_MODEL") or SPARK_MODEL,
        ),
        check_meta(keys["MODEL_API_KEY"], os.environ.get("META_IMAGE_MODEL") or IMAGE_MODEL),
    ]
    for result in results:
        print(json.dumps(result))
    if any(result["status"] != "ok" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
