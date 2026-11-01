"""Serve the built app with scripted providers, for the browser end-to-end run.

The planner's model is replaced by a deterministic stand-in that picks the cheapest catalog
outfits the request allows, and the image model by one that returns a plain generated PNG. No
provider is called, and every check, quota and token rule of the real server still applies.
Nothing this server returns is model output. Design My Look is on unless `--design-mode off`,
which serves the page the closed beta shows.

    uv run --frozen python -m scripts.e2e_server --port 8765
"""

from __future__ import annotations

import argparse
import itertools
import json
import struct
import time
import zlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import uvicorn

from backend.app.concepts import CONCEPT_PROMPT, MATCHING_PROMPT
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.meta_images import ImageResult, InputImage
from backend.app.pilot import invite_hash
from backend.app.planner import SparkClient
from backend.app.shopper import Find, FindOutcome, FindRequest, FindResult, FindSlot, Scope
from backend.app.shops import ShopProduct

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
    # A plan uses one size of a product in one color; owned items have no sizes.
    garment = {item["id"]: (item["title"], item["brand"], item["color"]) for item in candidates}

    def one_size_each(item_ids: set[str]) -> bool:
        return len(item_ids) == len({garment.get(item_id, item_id) for item_id in item_ids})

    replacement = request["replacement"]
    if replacement:
        looks = request["current_plan"]
        for look in looks:
            look["items"] = {slot: item["id"] for slot, item in look["items"].items()}
        target = next(look for look in looks if look["id"] == replacement["look_id"])
        current = target["items"].pop(replacement["slot"])
        others = set(target["items"].values())
        kept = {item for look in looks for item in look["items"].values()}
        options = by_slot.get(replacement["slot"], [])
        target["items"][replacement["slot"]] = next(
            (
                item
                for item in options
                if item != current and item not in others and one_size_each(kept | {item})
            ),
            current,
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
    planned = {lock["item_id"] for lock in request["locks"]}
    for number in range(1, 4):
        look_id = f"look_{number}"
        kept = locks.get(look_id, {})
        for top, bottom, shoe in combos:
            items = {"top": top, "bottom": bottom, **({"footwear": shoe} if shoe else {})}
            items |= kept
            signature = tuple(sorted(items.items()))
            cost = sum(price.get(item, 0) for item in set(items.values()))
            if signature in used or cost > budget or not one_size_each(planned | {*items.values()}):
                continue
            used.add(signature)
            planned |= {*items.values()}
            looks.append({"id": look_id, "items": items})
            break
    return {"looks": looks}


# A fixed Design My Look concept: its shoes and accessory have no close product in the catalog,
# so the browser run sees a close match, an alternative and no match.
_CONCEPT = {
    "name": "Scripted black and white",
    "why": "A fixed concept for the browser run, not model output.",
    "items": [
        {"slot": "top", "kind": "t-shirt", "color": "black", "color_family": "black"},
        {"slot": "bottom", "kind": "trousers", "color": "black", "color_family": "black"},
        {"slot": "footwear", "kind": "loafers", "color": "white", "color_family": "white"},
        {"slot": "accessory", "kind": "bag", "color": "black", "color_family": "black"},
    ],
}


def _match(request: dict[str, Any]) -> dict[str, Any]:
    """The cheapest candidate of each concept item's category with the item's color in its
    name; none for the accessory."""
    matches = []
    for item in request["concept"]:
        wanted = {"top": ("top", "knitwear"), "bottom": ("bottom",), "footwear": ("footwear",)}
        options = [
            c
            for c in request["candidates"]
            if c["category"] in wanted.get(item["slot"], ())
            and item["color"] in (c["color"] or "").lower()
        ]
        chosen = min(options, key=lambda c: (c["price_eur"], c["id"]), default=None)
        matches.append({"item": item["item"], "candidate_id": chosen["id"] if chosen else None})
    return {"matches": matches}


class ScriptedSpark(SparkClient):
    def __init__(self) -> None:
        super().__init__("scripted", model="scripted-e2e-planner")

    def complete(self, messages: Sequence[dict[str, str]], **_: Any) -> tuple[str, dict, int]:
        request = json.loads(messages[1]["content"])
        if messages[0]["content"] == CONCEPT_PROMPT:
            reply: dict[str, Any] = _CONCEPT
        elif messages[0]["content"] == MATCHING_PROMPT:
            reply = _match(request)
        else:
            reply = _choose(request)
        return json.dumps(reply), {"prompt_tokens": 1000, "completion_tokens": 300}, 1


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


def _product(name: str, cents: int, *, checked: bool = False) -> ShopProduct:
    return ShopProduct(
        f"https://shop.synthetic.example/p/{name.lower().replace(' ', '-')}",
        "Synthetic Shop",
        name,
        cents,
        "navy",
        "100% cotton",
        checked,
    )


def scripted_shop_agent(request: FindRequest, on_step: Callable[[str], None]) -> FindResult:
    """Fixed synthetic products for each scope; no shop or model is called, and a request that
    says "nothing" finds nothing."""
    on_step("Looking in the shops for: scripted")
    time.sleep(0.3)
    on_step("Read the shops' pages: 3 on sale within your budget")
    if "nothing" in request.request.lower():
        return FindResult(FindOutcome.NO_MATCH, message="Nothing scripted suits this request.")
    top = Find(_product("Synthetic Shirt", 4000), FindSlot.TOP, "A synthetic reason")
    jacket = Find(_product("Synthetic Jacket", 9000, checked=True), FindSlot.OUTER_LAYER, "Warm")
    bottom = Find(_product("Synthetic Trousers", 5000), FindSlot.BOTTOM, "Plain")
    shoes = Find(_product("Synthetic Shoes", 6000), FindSlot.FOOTWEAR, "Closed")
    finds = {
        Scope.ITEM: [jacket],
        Scope.UPPER_BODY: [top, jacket],
        Scope.LOOK: [top, bottom, shoes],
    }[request.scope]
    return FindResult(FindOutcome.SUCCESS, finds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--store", type=Path, help="a folder that keeps counts across restarts; memory if unset"
    )
    parser.add_argument(
        "--design-mode", choices=("on", "off"), default="on", help="FULLAURA_DESIGN_MODE"
    )
    args = parser.parse_args()
    settings = Settings(
        session_secret="e2e-session-secret-not-for-production-use",
        invite_hashes=frozenset({invite_hash(E2E_INVITE)}),
        store_url=f"file://{args.store.resolve()}" if args.store else "memory://",
        # Every browser test and viewport shares the one invite; the quota rule still applies.
        plan_requests_per_invite=48,
        design_mode=args.design_mode == "on",
    )
    app = create_app(
        settings,
        spark=ScriptedSpark(),
        image_editor=ScriptedEditor(),
        shop_agent=scripted_shop_agent,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
