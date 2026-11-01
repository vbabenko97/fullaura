"""Create invite codes for the closed beta.

Writes the codes and their links to an ignored file for the operator, and the codes' SHA-256
digests, which are all the server needs, to a second file. Prints neither the codes nor the links.

    uv run --frozen python -m scripts.pilot_invites --count 10 \\
        --base-url https://fullaura-demo.blackbay-aaeee2c5.francecentral.azurecontainerapps.io
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

from backend.app.config import PROJECT_ROOT
from backend.app.pilot import invite_hash, new_invite_code

DEFAULT_FOLDER = PROJECT_ROOT / "data" / "private" / "beta"


def create(count: int, base_url: str, folder: Path) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    invites, hashes = folder / "invites.csv", folder / "invite-hashes.txt"
    # The digests of an earlier batch are what the app and the pilot report know, so neither file
    # may be replaced by a new batch.
    for existing in (invites, hashes):
        if existing.exists():
            raise SystemExit(f"{existing} exists; move both files away before creating new invites")
    codes = [new_invite_code() for _ in range(count)]
    # Created readable by the operator alone, so the codes are never open to others.
    descriptor = os.open(invites, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with open(descriptor, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["invite", "code", "link", "given_to"])
        for number, code in enumerate(codes, start=1):
            writer.writerow([number, code, f"{base_url.rstrip('/')}/#invite={code}", ""])
    hashes.write_text(",".join(invite_hash(code) for code in codes) + "\n", encoding="utf-8")
    return invites, hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--folder", type=Path, default=DEFAULT_FOLDER)
    args = parser.parse_args()
    invites, hashes = create(args.count, args.base_url, args.folder)
    print(f"Wrote {args.count} invites to {invites} and their digests to {hashes}")


if __name__ == "__main__":
    main()
