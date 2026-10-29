"""Keep scratch files, copies and agent reports out of the repository (AGENTS.md)."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# A new root entry needs a reason in the commit that adds it.
ROOT_ENTRIES = {
    ".hallmark",
    ".dockerignore",
    ".editorconfig",
    ".env.example",
    ".github",
    ".gitignore",
    ".python-version",
    "AGENTS.md",
    "Dockerfile",
    "LICENSE",
    "README.md",
    "backend",
    "docs",
    "frontend",
    "infra",
    "pyproject.toml",
    "scripts",
    "tests",
    "uv.lock",
}
# Checked against every folder and file name in a path.
LEFTOVER = re.compile(
    r"(?i)( "  # spaces, as in Finder copies such as "App 2.tsx"
    r"|^(scratch|tmp|temp|debug)([_.-]|$)"
    r"|[_-](old|backup)(\.|$)"
    r"|\.(bak|orig|rej|swp|old)$|~$)"
)
# Session reports and plans written as Markdown outside .github/ (the AGENTS.md patterns).
REPORT = re.compile(r"(?i)^(.*summary.*|.*report.*|plan.*|.*notes.*|todo.*)\.md$")
DOC_NAME = re.compile(r"[A-Z][A-Z0-9_]*\.md")


@pytest.fixture(scope="module")
def paths() -> list[str]:
    """Tracked files plus new files that Git does not ignore."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("needs a Git checkout")
    return [path for path in result.stdout.split("\0") if path]


def test_root_holds_only_known_entries(paths: list[str]) -> None:
    unexpected = sorted({path.split("/")[0] for path in paths} - ROOT_ENTRIES)
    assert not unexpected, f"Move these out of the root, or list them in ROOT_ENTRIES: {unexpected}"


def test_no_copies_scratch_files_or_agent_reports(paths: list[str]) -> None:
    leftovers = [
        path
        for path in paths
        if any(LEFTOVER.search(part) for part in path.split("/"))
        or (not path.startswith(".github/") and REPORT.match(path.rsplit("/", 1)[-1]))
    ]
    assert not leftovers, f"Delete these, or keep them in tmp/: {leftovers}"


def test_docs_use_uppercase_names(paths: list[str]) -> None:
    docs = [path.removeprefix("docs/") for path in paths if path.startswith("docs/")]
    misnamed = [
        name
        for name in docs
        if "/" not in name and name.endswith(".md") and not DOC_NAME.fullmatch(name)
    ]
    assert not misnamed, f"Name Markdown files in docs/ like SPEC.md: {misnamed}"
