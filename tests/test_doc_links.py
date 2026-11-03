"""Links between the project's documents lead to a file and a heading that exist."""

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").glob("*.md"))]
FENCE = re.compile(r"^```.*?^```\s*$", re.M | re.S)
HEADING = re.compile(r"^#{1,6} +(.+?)\s*$", re.M)
LINK = re.compile(r"\]\(([^)\s]+)\)")


def anchors(path: Path) -> set[str]:
    """The anchors GitHub gives the file's headings; a repeated heading gets a number."""
    seen: Counter[str] = Counter()
    found = set()
    for heading in HEADING.findall(FENCE.sub("", path.read_text(encoding="utf-8"))):
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).replace("`", "")
        slug = re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")
        found.add(f"{slug}-{seen[slug]}" if seen[slug] else slug)
        seen[slug] += 1
    return found


def test_links_between_documents_resolve() -> None:
    broken = []
    for document in DOCUMENTS:
        for target in LINK.findall(FENCE.sub("", document.read_text(encoding="utf-8"))):
            if target.startswith(("http://", "https://")):
                continue
            file, _, anchor = target.partition("#")
            linked = document.parent / file if file else document
            if not linked.exists():
                broken.append(f"{document.name}: {target}")
            elif anchor and linked.suffix == ".md" and anchor not in anchors(linked):
                broken.append(f"{document.name}: {target}")
    assert not broken, f"Links without a target: {broken}"
