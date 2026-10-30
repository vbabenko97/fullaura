"""Check the deployment boundary between the API and frontend assets."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import PROJECT_ROOT, Settings
from backend.app.main import create_app
from backend.app.pilot import invite_hash

REAL_CATALOG_ID = json.loads(Settings().catalog_file.read_text(encoding="utf-8"))["id"]


def test_api_stays_available_without_frontend_or_provider_credentials(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(web_dist=tmp_path))) as client:
        assert client.get("/api/health").json() == {"status": "ok", "service": "fullaura"}
        assert client.get("/api/ready").status_code == 200
        assert client.get("/").status_code == 503


def test_uvicorn_default_logging_keeps_planner_usage_records() -> None:
    # A separate process, because Uvicorn's logging setup changes global state.
    script = (
        "import logging, uvicorn\n"
        "uvicorn.Config('backend.app.main:app').load()\n"
        "logging.getLogger('backend.app.planner').info('plan outcome=success')\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert "plan outcome=success" in run.stderr


def test_the_real_catalog_loads_for_an_invited_session_without_provider_credentials() -> None:
    code = "development-invite"
    settings = Settings(invite_hashes=frozenset({invite_hash(code)}))
    with TestClient(create_app(settings)) as client:
        client.post("/api/session/invite", json={"code": code})
        response = client.get("/api/catalog")

    assert response.status_code == 200
    assert response.json()["id"] == REAL_CATALOG_ID
    assert response.json()["synthetic"] is False


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
