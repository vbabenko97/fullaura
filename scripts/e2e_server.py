"""Serve the built app with scripted providers, for the browser end-to-end run.

The planner's model is replaced by a deterministic stand-in that picks the cheapest catalog
outfits the request allows, and the image model by one that returns a plain generated PNG. No
provider is called, and every check, quota and token rule of the real server still applies.
Nothing this server returns is model output.

    uv run --frozen python -m scripts.e2e_server --port 8765
"""

from __future__ import annotations

import argparse
import itertools
import json
import struct
import zlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import uvicorn

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.meta_images import ImageResult, InputImage
from backend.app.pilot import invite_hash
from backend.app.planner import SparkClient

E2E_INVITE = "e2e-invite-code-not-for-production"
_SLOTS_FOR = {"top": ("top", "knitwear"), "bottom": ("bottom",), "footwear": ("footwear",)}


def _choose(request: dict[str, Any]) -> dict[str, Any]:
    """Three distinct looks of a top, a bottom and, when there is one, shoes: the cheapest
    combinations within the budget that keep every lock, or one changed position for a
    replacement."""
    candidates = request["candidates"]
    price = {item["id"]: item["price_eur"] for item in candidates}
    by_slot = {
        slot: sorted(
            (item["id"] for item in candidates if item["category"] in categories),
            key=lambda item_id: (price[item_id], item_id),
        )
        for slot, categories in _SLOTS_FOR.items()
    }
    for owned in request["owned_items"]:
        for slot, categories in _SLOTS_FOR.items():
            if owned["category"] in categories:
                by_slot[slot].insert(0, owned["id"])
                price[owned["id"]] = 0
    replacement = request["replacement"]
    if replacement:
        looks = request["current_plan"]
        for look in looks:
            look["items"] = {slot: item["id"] for slot, item in look["items"].items()}
            if look["id"] == replacement["look_id"]:
                current = look["items"][replacement["slot"]]
                others = set(look["items"].values())
                options = by_slot.get(replacement["slot"], [])
                look["items"][replacement["slot"]] = next(
                    (item for item in options if item != current and item not in others), current
                )
        return {"looks": looks}

    locks: dict[str, dict[str, str]] = {}
    for lock in request["locks"]:
        locks.setdefault(lock["look_id"], {})[lock["slot"]] = lock["item_id"]
    budget = request["budget"]["amount_eur"]
    shoes = by_slot["footwear"] or [None]
    combos = sorted(
        itertools.product(by_slot["top"], by_slot["bottom"], shoes),
        key=lambda combo: (sum(price[item] for item in combo if item), combo[0], combo[1]),
    )
    looks, used = [], set()
    for number in range(1, 4):
        look_id = f"look_{number}"
        kept = locks.get(look_id, {})
        for top, bottom, shoe in combos:
            items = {"top": top, "bottom": bottom, **({"footwear": shoe} if shoe else {})}
            items |= kept
            signature = tuple(sorted(items.items()))
            cost = sum(price.get(item, 0) for item in set(items.values()))
            if signature in used or cost > budget:
                continue
            used.add(signature)
            looks.append({"id": look_id, "items": items})
            break
    return {"looks": looks}


class ScriptedSpark(SparkClient):
    def __init__(self) -> None:
        super().__init__("scripted", model="scripted-e2e-planner")

    def complete(self, messages: Sequence[dict[str, str]], **_: Any) -> tuple[str, dict, int]:
        plan = _choose(json.loads(messages[1]["content"]))
        return json.dumps(plan), {"prompt_tokens": 1000, "completion_tokens": 300}, 1


def _png(width: int = 96, height: int = 144) -> bytes:
    rows = b"".join(b"\x00" + bytes((32, 59, 49)) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


class ScriptedEditor:
    async def edit_async(
        self, prompt: str, images: Sequence[InputImage], *, deadline: float
    ) -> ImageResult:
        return ImageResult(_png(), "image/png", 1, 0.0, {}, None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--store", type=Path, help="a folder that keeps counts across restarts; memory if unset"
    )
    args = parser.parse_args()
    settings = Settings(
        session_secret="e2e-session-secret-not-for-production-use",
        invite_hashes=frozenset({invite_hash(E2E_INVITE)}),
        store_url=f"file://{args.store.resolve()}" if args.store else "memory://",
        # Every browser test and viewport shares the one invite; the quota rule still applies.
        plan_requests_per_invite=16,
    )
    app = create_app(settings, spark=ScriptedSpark(), image_editor=ScriptedEditor())
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
