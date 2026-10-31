"""Provenance for the run records of the evaluation scripts.

recorded() adds to a run record what is needed to reproduce or audit the run: the command, the
Git revision, whether the working tree differs from it, a snapshot of that difference in
source.diff, hashes of the lock and environment files, the Python version and platform, and
which provider keys are set. It writes run.json with status "running" before the run's body
starts, then "succeeded", or "failed" with the exception, which it re-raises, so a failed or
interrupted run keeps its record.

run.json and source.diff replace the values of provider keys, and of other environment
variables named like secrets, with a marker. Text replacement cannot find a secret inside a
file that Git treats as binary, because the snapshot stores such files compressed.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

REDACTED = "[REDACTED]"
PROVIDER_KEYS = ("BASETEN_API_KEY", "MODEL_API_KEY")
SECRET_SUFFIXES = ("_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_CONNECTION_STRING")
# Shorter values of variables matched only by their name's suffix, such as a flag set to 1,
# would replace ordinary text throughout the record.
MIN_SECRET_LENGTH = 8
LOCK_FILES = ("uv.lock", "pyproject.toml", ".python-version")
SOURCE_DIFF = "source.diff"
# A patch that git apply accepts whatever the user's Git configuration says about colors,
# path prefixes or external diff tools.
DIFF_OPTIONS = (
    "--binary",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)


def secret_values() -> list[str]:
    """Values to redact, longest first, so that a secret that contains another goes whole."""
    values: set[str] = set()
    for name, value in os.environ.items():
        if value and (
            name in PROVIDER_KEYS
            or (name.upper().endswith(SECRET_SUFFIXES) and len(value) >= MIN_SECRET_LENGTH)
        ):
            values.add(value)
            # A patch starts each line of a multi-line value with "+", so each line goes too.
            lines = (line.strip() for line in value.splitlines())
            values.update(line for line in lines if len(line) >= MIN_SECRET_LENGTH)
    return sorted(values, key=len, reverse=True)


def redact(value: object, secrets: Sequence[str]) -> object:
    """A copy of a JSON value with every secret in its strings replaced by the marker."""
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, REDACTED)
        return value
    if isinstance(value, dict):
        return {redact(key, secrets): redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [redact(item, secrets) for item in value]
    return value


def write_record(run_dir: Path, record: dict[str, Any]) -> None:
    """Write run.json without secrets, replacing any earlier version in one step."""
    text = json.dumps(redact(record, secret_values()), ensure_ascii=False, indent=2) + "\n"
    partial = run_dir / "run.json.partial"
    partial.write_text(text, encoding="utf-8")
    partial.replace(run_dir / "run.json")


def _git(root: Path, *args: str, allowed: tuple[int, ...] = (0,)) -> bytes:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if done.returncode not in allowed:
        raise subprocess.CalledProcessError(done.returncode, done.args, done.stdout, done.stderr)
    return done.stdout


def source_snapshot(root: Path) -> bytes:
    """Tracked changes against HEAD and every untracked file Git does not ignore, as a patch."""
    snapshot = _git(root, "diff", *DIFF_OPTIONS, "HEAD")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    for name in filter(None, untracked):
        # git diff --no-index exits with 1 when the files differ, as they do against /dev/null.
        new_file = ("--no-index", *DIFF_OPTIONS, "--", os.devnull, os.fsdecode(name))
        snapshot += _git(root, "diff", *new_file, allowed=(0, 1))
    return snapshot


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@contextmanager
def recorded(run_dir: Path, record: dict[str, Any], root: Path) -> Iterator[None]:
    """Add provenance to record and write it as running; then write the run's outcome."""
    record["status"] = "running"
    try:
        snapshot = source_snapshot(root)
        stored = snapshot
        for secret in secret_values():
            stored = stored.replace(os.fsencode(secret), REDACTED.encode())
        (run_dir / SOURCE_DIFF).write_bytes(stored)
        record |= {
            "argv": list(sys.argv),
            "git_sha": _git(root, "rev-parse", "HEAD").decode().strip(),
            "git_dirty": bool(snapshot),
            "source_diff": SOURCE_DIFF,
            "source_diff_sha256": _sha256(stored),
            "lock_file_sha256": {
                name: _sha256(path.read_bytes()) if (path := root / name).is_file() else None
                for name in LOCK_FILES
            },
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "provider_keys_set": {name: bool(os.environ.get(name)) for name in PROVIDER_KEYS},
        }
        write_record(run_dir, record)
        yield
    except BaseException as error:
        record["status"] = "failed"
        record["error"] = {"type": type(error).__name__, "message": str(error)}
        write_record(run_dir, record)
        raise
    record["status"] = "succeeded"
    write_record(run_dir, record)
