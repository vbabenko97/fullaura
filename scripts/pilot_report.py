"""Report the closed beta's spending ledger and per-invite counts, without personal data.

Reads the pilot store with the operator's own Azure CLI sign-in, which needs the Storage Blob
Data Reader role or higher on the account. Prints money in US dollars from the ledger, which
holds reservations at list prices (docs/BETA.md#costs), not the providers' invoices.

    uv run --frozen python -m scripts.pilot_report \\
        --store-url https://<account>.blob.core.windows.net/pilot \\
        --invites data/private/beta/invite-hashes.txt
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import httpx2

from backend.app.pilot import Budget
from backend.app.store import STORAGE_RESOURCE, AzureBlobStore, DirectoryStore, Store


def cli_token(_: httpx2.Client) -> tuple[str, float]:
    result = subprocess.run(
        ["az", "account", "get-access-token", "--resource", STORAGE_RESOURCE, "--output", "json"],
        capture_output=True,
        check=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    return payload["accessToken"], float(payload["expires_on"])


def open_store(url: str) -> Store:
    if url.startswith("file://"):
        return DirectoryStore(Path(url.removeprefix("file://")))
    account_url, container = url.rstrip("/").rsplit("/", 1)
    return AzureBlobStore(account_url, container, token=cli_token)


def report(store: Store, invite_hashes: list[str]) -> dict[str, object]:
    ledger = store.read("ledger.json")
    data = ledger.data if ledger else {"spent": {}, "reserved": {}, "running": {}}
    invites = []
    for digest in invite_hashes:
        invite_id = digest[:16]
        record = store.read(f"invites/{invite_id}.json")
        values = record.data if record else {}
        invites.append(
            {
                "invite": invite_id[:6],
                "age_confirmed": bool(values.get("age_confirmed_at")),
                "photo_consent": bool(values.get("photo_consent_at")),
                "plans": values.get("plan_requests", 0),
                "try_ons": values.get("preview_requests", 0),
                "feedback": len(values.get("feedback", [])),
            }
        )
    return {
        "spent_usd": {b.value: data["spent"].get(b.value, 0) / 1_000_000 for b in Budget},
        "reserved_usd": {b.value: data["reserved"].get(b.value, 0) / 1_000_000 for b in Budget},
        "running": len(data["running"]),
        "invites": invites,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store-url", required=True)
    parser.add_argument("--invites", type=Path, help="the invite-hashes.txt file")
    args = parser.parse_args()
    hashes = []
    if args.invites:
        hashes = [h for h in args.invites.read_text(encoding="utf-8").strip().split(",") if h]
    print(json.dumps(report(open_store(args.store_url), hashes), indent=2))


if __name__ == "__main__":
    main()
