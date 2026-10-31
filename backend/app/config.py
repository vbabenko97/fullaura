"""Runtime settings, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from backend.app.meta_images import IMAGE_MODEL

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = "development"
    # The closed beta: invited sessions can plan and try looks on. Development is always open.
    beta_enabled: bool = False
    web_dist: Path = PROJECT_ROOT / "frontend" / "dist"
    catalog_file: Path = PROJECT_ROOT / "backend" / "data" / "catalog.json"
    # Signs session cookies; development uses a random secret per process when it is empty.
    session_secret: str = field(default="", repr=False)
    invite_hashes: frozenset[str] = frozenset()  # SHA-256 hex digests of the invite codes
    # Where quotas, spending and feedback live: memory://, file:///absolute/folder, or
    # https://<account>.blob.core.windows.net/<container> with the app's managed identity.
    store_url: str = "memory://"
    plan_requests_per_invite: int = 8
    preview_requests_per_invite: int = 12
    planning_budget_usd: float = 8.0
    image_budget_usd: float = 3.0
    pilot_ends_at: datetime | None = None
    # When the scheduled job deletes every pilot record; the beta closes at that moment.
    records_purge_at: datetime | None = None
    # Muse Spark on Baseten; empty values fall back to the planner's defaults.
    baseten_api_key: str = field(default="", repr=False)
    baseten_base_url: str = ""
    baseten_model: str = ""
    model_api_key: str = field(default="", repr=False)
    meta_image_model: str = IMAGE_MODEL
    # The experimental Design My Look mode (backend/app/concepts.py); off unless set.
    design_mode: bool = False

    def __post_init__(self) -> None:
        if self.environment not in {"development", "production"}:
            raise ValueError("FULLAURA_ENV must be development or production")
        if self.pilot_ends_at is not None and self.pilot_ends_at.tzinfo is None:
            raise ValueError("FULLAURA_PILOT_ENDS_AT needs a time zone, such as +01:00")
        if self.records_purge_at is not None:
            if self.records_purge_at.tzinfo is None:
                raise ValueError("FULLAURA_RECORDS_PURGE_AT needs a time zone, such as +01:00")
            if self.pilot_ends_at is None or self.records_purge_at <= self.pilot_ends_at:
                raise ValueError("FULLAURA_RECORDS_PURGE_AT must come after FULLAURA_PILOT_ENDS_AT")

    @property
    def beta_open(self) -> bool:
        return self.environment == "development" or self.beta_enabled

    @classmethod
    def from_env(cls) -> Settings:
        web_dist = os.getenv("FULLAURA_WEB_DIST")
        ends_at = os.getenv("FULLAURA_PILOT_ENDS_AT", "")
        purge_at = os.getenv("FULLAURA_RECORDS_PURGE_AT", "")
        hashes = os.getenv("FULLAURA_INVITE_HASHES", "")
        defaults = cls()
        return cls(
            environment=os.getenv("FULLAURA_ENV", "development"),
            beta_enabled=os.getenv("FULLAURA_BETA_ENABLED", "").lower() == "true",
            web_dist=Path(web_dist).resolve() if web_dist else defaults.web_dist,
            session_secret=os.getenv("FULLAURA_SESSION_SECRET", ""),
            invite_hashes=frozenset(h.strip().lower() for h in hashes.split(",") if h.strip()),
            store_url=os.getenv("FULLAURA_STORE_URL", defaults.store_url),
            plan_requests_per_invite=int(
                os.getenv("FULLAURA_PLAN_REQUESTS_PER_INVITE", defaults.plan_requests_per_invite)
            ),
            preview_requests_per_invite=int(
                os.getenv(
                    "FULLAURA_PREVIEW_REQUESTS_PER_INVITE", defaults.preview_requests_per_invite
                )
            ),
            planning_budget_usd=float(
                os.getenv("FULLAURA_PLANNING_BUDGET_USD", defaults.planning_budget_usd)
            ),
            image_budget_usd=float(
                os.getenv("FULLAURA_IMAGE_BUDGET_USD", defaults.image_budget_usd)
            ),
            pilot_ends_at=datetime.fromisoformat(ends_at) if ends_at else None,
            records_purge_at=datetime.fromisoformat(purge_at) if purge_at else None,
            baseten_api_key=os.getenv("BASETEN_API_KEY", ""),
            baseten_base_url=os.getenv("BASETEN_BASE_URL", ""),
            baseten_model=os.getenv("BASETEN_MODEL", ""),
            model_api_key=os.getenv("MODEL_API_KEY", ""),
            meta_image_model=os.getenv("META_IMAGE_MODEL", IMAGE_MODEL),
            design_mode=os.getenv("FULLAURA_DESIGN_MODE", "").lower() == "true",
        )
