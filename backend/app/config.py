"""Runtime settings, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = "development"
    web_dist: Path = PROJECT_ROOT / "frontend" / "dist"
    catalog_file: Path = PROJECT_ROOT / "backend" / "data" / "catalog.json"
    # Muse Spark on Baseten; empty values fall back to the planner's defaults.
    baseten_api_key: str = field(default="", repr=False)
    baseten_base_url: str = ""
    baseten_model: str = ""
    # T-02 must pass before the development preview route accepts photos.
    t02_previews_passed: bool = False
    model_api_key: str = field(default="", repr=False)
    meta_image_model: str = "muse-image-1.0"

    def __post_init__(self) -> None:
        if self.environment not in {"development", "production"}:
            raise ValueError("FULLAURA_ENV must be development or production")

    @classmethod
    def from_env(cls) -> Settings:
        web_dist = os.getenv("FULLAURA_WEB_DIST")
        return cls(
            environment=os.getenv("FULLAURA_ENV", "development"),
            web_dist=Path(web_dist).resolve() if web_dist else cls().web_dist,
            baseten_api_key=os.getenv("BASETEN_API_KEY", ""),
            baseten_base_url=os.getenv("BASETEN_BASE_URL", ""),
            baseten_model=os.getenv("BASETEN_MODEL", ""),
            t02_previews_passed=os.getenv("FULLAURA_T02_PREVIEWS_PASSED", "").lower() == "true",
            model_api_key=os.getenv("MODEL_API_KEY", ""),
            meta_image_model=os.getenv("META_IMAGE_MODEL", "muse-image-1.0"),
        )
