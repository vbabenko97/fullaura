"""Verify a built LookPilot server over HTTP; uses no model credentials."""

from __future__ import annotations

import json
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def check(base_url: str) -> None:
    deadline = time.monotonic() + 30
    while True:
        try:
            with urlopen(f"{base_url}/api/ready", timeout=2) as response:
                if json.load(response) == {"status": "ready"}:
                    break
        except (URLError, ConnectionError, TimeoutError):
            # Docker can bind its host port before Uvicorn accepts connections.
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Server did not become ready within 30 seconds")
        time.sleep(0.5)

    with urlopen(f"{base_url}/api/health", timeout=5) as response:
        if json.load(response) != {"status": "ok", "service": "lookpilot"}:
            raise RuntimeError("Unexpected health response")
    with urlopen(f"{base_url}/", timeout=5) as response:
        html = response.read().decode("utf-8")
        if response.headers.get_content_type() != "text/html" or "LookPilot" not in html:
            raise RuntimeError("Frontend entry point is missing")
    assets = re.findall(r'(?:src|href)="(/assets/[^"<>]+)"', html)
    if not assets:
        raise RuntimeError("No frontend assets found in the entry point")
    for asset in assets:
        with urlopen(f"{base_url}{asset}", timeout=5) as response:
            if response.status != 200 or not response.read():
                raise RuntimeError(f"Frontend asset failed: {asset}")
    try:
        urlopen(f"{base_url}/api/unknown", timeout=5).close()
    except HTTPError as error:
        if error.code != 404 or json.load(error) != {"detail": "Not Found"}:
            raise RuntimeError("Unknown API route did not return a JSON 404") from error
    else:
        raise RuntimeError("Unknown API route unexpectedly succeeded")
    print(f"HTTP smoke check passed: health, readiness, frontend, {len(assets)} assets, API 404")


if __name__ == "__main__":
    check((sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/"))
