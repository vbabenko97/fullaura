"""Check the deployment boundary between the API and frontend assets."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


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
        response = client.post(
            "/api/previews",
            content=b"{not valid JSON",
            headers={"Content-Type": "application/json"},
        )
        if environment == "development":
            operation = client.get("/api/openapi.json").json()["paths"]["/api/previews"]["post"]

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Photo previews are unavailable pending the T-02 feasibility result"
    }
    assert response.headers["cache-control"] == "no-store"
    if environment == "development":
        assert "requestBody" not in operation
        assert "503" in operation["responses"]


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
