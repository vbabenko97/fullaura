"""Check the deployment boundary between the API and frontend assets."""

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import PREVIEW_DISABLED_DETAIL, create_app
from backend.app.meta_images import ImageResult, InputImage

PREVIEW = b"\x89PNG\r\n\x1a\npreview"


def jpeg() -> bytes:
    return b"\xff\xd8\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00pixels\xff\xd9"


class FakePreviewEditor:
    def __init__(self) -> None:
        self.prompt: str | None = None
        self.images: list[InputImage] = []

    async def edit_async(
        self,
        prompt: str,
        images: Sequence[InputImage],
        *,
        deadline: float,
    ) -> ImageResult:
        self.prompt = prompt
        self.images = list(images)
        return ImageResult(PREVIEW, "image/png", 1, 0.1, {}, None)


def test_api_stays_available_without_frontend_or_provider_credentials(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(web_dist=tmp_path))) as client:
        assert client.get("/api/health").json() == {"status": "ok", "service": "fullaura"}
        assert client.get("/api/ready").status_code == 200
        assert client.get("/").status_code == 503


def test_development_catalog_route_returns_catalog_facts_without_provider_credentials() -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.get("/api/catalog")

    assert response.status_code == 200
    assert response.json()["id"] == "fullaura-catalog-2026-10-28"
    assert response.json()["synthetic"] is False
    assert len(response.json()["products"]) == 15


@pytest.mark.parametrize("environment", ["development", "production"])
def test_preview_route_is_hard_disabled_without_reading_an_upload_body(
    tmp_path: Path, environment: str
) -> None:
    if environment == "production":
        (tmp_path / "index.html").write_text("<html>FullAura</html>", encoding="utf-8")
    settings = Settings(environment=environment, web_dist=tmp_path)
    with TestClient(create_app(settings)) as client:
        status = client.get("/api/previews/status")
        response = client.post(
            "/api/previews",
            content=b"{not valid JSON",
            headers={"Content-Type": "application/json"},
        )
        if environment == "development":
            operation = client.get("/api/openapi.json").json()["paths"]["/api/previews"]["post"]

    assert status.json() == {
        "enabled": False,
        "detail": (
            PREVIEW_DISABLED_DETAIL
            if environment == "development"
            else "Photo previews are available only in development"
        ),
    }
    assert response.status_code == 503
    assert response.json() == {"detail": status.json()["detail"]}
    assert response.headers["cache-control"] == "no-store"
    if environment == "development":
        assert operation["requestBody"]["required"] is True
        assert "multipart/form-data" in operation["requestBody"]["content"]


def test_enabled_preview_route_returns_image_and_discloses_no_upload_data() -> None:
    editor = FakePreviewEditor()
    settings = Settings(t02_previews_passed=True)
    with TestClient(create_app(settings, image_editor=editor)) as client:
        status = client.get("/api/previews/status")
        response = client.post(
            "/api/previews",
            files={
                "portrait": ("portrait.jpg", jpeg(), "image/jpeg"),
                "full_body": ("full-body.jpg", jpeg(), "image/jpeg"),
            },
            data={"prompt": "Use the selected look"},
        )

    assert status.json() == {"enabled": True, "detail": None}
    assert response.status_code == 200
    assert response.content == PREVIEW
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert editor.prompt == "Use the selected look"
    assert len(editor.images) == 2


def test_preview_route_cancels_provider_at_the_shared_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WaitingEditor:
        cancelled = False

        async def edit_async(
            self,
            prompt: str,
            images: Sequence[InputImage],
            *,
            deadline: float,
        ) -> ImageResult:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    editor = WaitingEditor()
    monkeypatch.setattr("backend.app.main.PREVIEW_DEADLINE_S", 0.02)
    with TestClient(create_app(Settings(t02_previews_passed=True), image_editor=editor)) as client:
        response = client.post(
            "/api/previews",
            files={
                "portrait": ("portrait.jpg", jpeg(), "image/jpeg"),
                "full_body": ("full-body.jpg", jpeg(), "image/jpeg"),
            },
            data={"prompt": "Use the selected look"},
        )

    assert response.status_code == 504
    assert response.json() == {"detail": "Preview generation took too long"}
    assert editor.cancelled


@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete", "options"])
def test_unknown_api_routes_never_return_the_frontend(tmp_path: Path, method: str) -> None:
    (tmp_path / "index.html").write_text("<html>FullAura</html>", encoding="utf-8")
    with TestClient(create_app(Settings(web_dist=tmp_path))) as client:
        response = getattr(client, method)("/api/unknown")
        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}


def test_production_requires_a_frontend_build(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Build the frontend"):
        create_app(Settings(environment="production", web_dist=tmp_path))


def test_production_serves_assets_and_reports_missing_entrypoint(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text("<html>FullAura</html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("export {};", encoding="utf-8")
    with TestClient(create_app(Settings(environment="production", web_dist=tmp_path))) as client:
        assert client.get("/").headers["content-type"].startswith("text/html")
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/api/docs").status_code == 404
        assert client.get("/api/catalog").status_code == 404
        index.unlink()
        assert client.get("/api/ready").status_code == 503


def test_production_demo_requires_explicit_opt_in_and_keeps_previews_off(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html>FullAura</html>", encoding="utf-8")
    settings = Settings(
        environment="production",
        public_demo_enabled=True,
        web_dist=tmp_path,
    )
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        session = client.get("/api/session")
        assert session.status_code == 200
        assert session.json()["enabled"] is True
        assert "secure" in session.headers["set-cookie"].lower()
        assert client.get("/api/catalog").status_code == 200
        assert client.get("/api/previews/status").json() == {
            "enabled": False,
            "detail": "Photo previews are available only in development",
        }
        assert client.post("/api/session/confirm-age", json={"over_18": True}).status_code == 200
        assert client.post("/api/plans", json={}).status_code == 422
