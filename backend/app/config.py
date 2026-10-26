"""Runtime settings. Model credentials are not used by the initial scaffold."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = "development"
    web_dist: Path = PROJECT_ROOT / "frontend" / "dist"

    def __post_init__(self) -> None:
        if self.environment not in {"development", "production"}:
            raise ValueError("LOOKPILOT_ENV must be development or production")

    @classmethod
    def from_env(cls) -> Settings:
        web_dist = os.getenv("LOOKPILOT_WEB_DIST")
        return cls(
            environment=os.getenv("LOOKPILOT_ENV", "development"),
            web_dist=Path(web_dist).resolve() if web_dist else cls().web_dist,
        )
