"""Run records identify the code, environment and command, survive failures and hold no secrets."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.run_provenance import LOCK_FILES, REDACTED, recorded

SECRET = "sk-test-0123456789"


def git(repo: Path, *args: str) -> str:
    identity = ("-c", "user.name=Test", "-c", "user.email=test@example.com")
    done = subprocess.run(["git", *identity, *args], cwd=repo, check=True, capture_output=True)
    return done.stdout.decode()


def make_repo(path: Path) -> Path:
    """A repository with one commit: the lock files, an app module and a .gitignore."""
    path.mkdir()
    git(path, "init", "-q")
    (path / "uv.lock").write_text("version = 1\n")
    (path / "pyproject.toml").write_text('[project]\nname = "example"\n')
    (path / ".python-version").write_text("3.12\n")
    (path / ".gitignore").write_text(".env\ndata/private/\nruns/\n")
    (path / "app.py").write_text("ANSWER = 1\n")
    git(path, "add", ".")
    git(path, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "start")
    return path


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The user's Git configuration must not change what is ignored or how diffs look.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return make_repo(tmp_path / "repo")


def run_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "run"
    folder.mkdir()
    return folder


def read_record(folder: Path) -> dict:
    return json.loads((folder / "run.json").read_text(encoding="utf-8"))


def test_a_clean_tree_records_its_revision_and_an_empty_snapshot(
    repo: Path, tmp_path: Path
) -> None:
    folder = run_dir(tmp_path)
    record = {"model": "example"}
    with recorded(folder, record, repo):
        assert read_record(folder)["status"] == "running"

    saved = read_record(folder)
    assert saved["status"] == "succeeded" and "error" not in saved
    assert saved["model"] == "example"
    assert saved["git_sha"] == git(repo, "rev-parse", "HEAD").strip()
    assert saved["git_dirty"] is False
    assert (folder / "source.diff").read_bytes() == b""
    assert saved["source_diff_sha256"] == hashlib.sha256(b"").hexdigest()
    assert saved["lock_file_sha256"] == {
        name: hashlib.sha256((repo / name).read_bytes()).hexdigest() for name in LOCK_FILES
    }
    assert saved["python_version"] and saved["platform"]
    assert saved["argv"] == sys.argv


def test_the_snapshot_restores_changed_and_untracked_files_but_skips_ignored_ones(
    repo: Path, tmp_path: Path
) -> None:
    (repo / "app.py").write_text("ANSWER = 2\n")
    (repo / "helpers").mkdir()
    (repo / "helpers" / "new.py").write_text("NEW = True\n")
    (repo / "logo.bin").write_bytes(b"\x00\x01\x02binary")
    (repo / ".env").write_text("PRIVATE=env-file-value\n")
    (repo / "data" / "private").mkdir(parents=True)
    (repo / "data" / "private" / "photo.txt").write_text("private-photo-value\n")
    folder = run_dir(tmp_path)

    with recorded(folder, {}, repo):
        pass

    saved = read_record(folder)
    snapshot = (folder / "source.diff").read_bytes()
    assert saved["git_dirty"] is True
    assert saved["source_diff_sha256"] == hashlib.sha256(snapshot).hexdigest()
    assert b"env-file-value" not in snapshot and b"private-photo-value" not in snapshot
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    git(clone, "apply", str(folder / "source.diff"))
    for name in ("app.py", "helpers/new.py", "logo.bin"):
        assert (clone / name).read_bytes() == (repo / name).read_bytes()
    assert not (clone / ".env").exists() and not (clone / "data").exists()


def test_secrets_stay_out_of_a_failed_run_record_and_its_snapshot(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = "ghp-other-token-value"
    connection = "AccountKey=example-storage-key"
    monkeypatch.setenv("BASETEN_API_KEY", SECRET)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setenv("EXAMPLE_TOKEN", other)
    monkeypatch.setenv("EXAMPLE_CONNECTION_STRING", connection)
    monkeypatch.setattr(sys, "argv", ["evaluate", "--key", SECRET, f"--token={other}"])
    (repo / "app.py").write_text(f'KEY = "{SECRET}"\n')
    (repo / "notes.py").write_text(f'TOKEN = "{other}"\n')
    folder = run_dir(tmp_path)

    with pytest.raises(RuntimeError, match=SECRET), recorded(folder, {}, repo):
        raise RuntimeError(f"provider rejected {SECRET} via {connection}")

    text = (folder / "run.json").read_text(encoding="utf-8")
    snapshot = (folder / "source.diff").read_text(encoding="utf-8")
    for value in (SECRET, other, connection):
        assert value not in text and value not in snapshot
    saved = json.loads(text)
    assert saved["status"] == "failed"
    assert saved["error"] == {
        "type": "RuntimeError",
        "message": f"provider rejected {REDACTED} via {REDACTED}",
    }
    assert saved["argv"] == ["evaluate", "--key", REDACTED, f"--token={REDACTED}"]
    assert saved["provider_keys_set"] == {"BASETEN_API_KEY": True, "MODEL_API_KEY": False}
    assert f'KEY = "{REDACTED}"' in snapshot and f'TOKEN = "{REDACTED}"' in snapshot


def test_each_line_of_a_multi_line_secret_stays_out_of_the_snapshot(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = ["-----BEGIN TEST KEY-----", "c2VjcmV0LWxpbmUtb25l", "c2VjcmV0LWxpbmUtdHdv"]
    monkeypatch.setenv("EXAMPLE_PRIVATE_KEY", "\n".join(lines) + "\n")
    (repo / "key.pem").write_text("\n".join(lines) + "\n")
    folder = run_dir(tmp_path)

    with recorded(folder, {}, repo):
        pass

    snapshot = (folder / "source.diff").read_text(encoding="utf-8")
    assert "key.pem" in snapshot and all(line not in snapshot for line in lines)
    assert snapshot.count(f"+{REDACTED}") == len(lines)


def test_a_run_that_cannot_read_its_revision_still_leaves_a_failed_record(
    tmp_path: Path,
) -> None:
    folder = run_dir(tmp_path)
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()

    with pytest.raises(subprocess.CalledProcessError), recorded(folder, {"model": "x"}, not_a_repo):
        pass

    saved = read_record(folder)
    assert saved["status"] == "failed" and saved["error"]["type"] == "CalledProcessError"
    assert saved["model"] == "x"
