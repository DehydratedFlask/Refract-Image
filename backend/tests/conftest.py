from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TOKEN = "test-token"


@pytest.fixture()
def isolated_dirs(tmp_path, monkeypatch):
    """Point every Refract directory at a temp path so tests never touch real data."""
    data = tmp_path / "data"
    monkeypatch.setenv("REFRACT_DATA_DIR", str(data))
    monkeypatch.setenv("REFRACT_MODELS_DIR", str(data / "models"))
    monkeypatch.setenv("REFRACT_JOBS_DIR", str(data / "jobs"))
    monkeypatch.setenv("REFRACT_OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("REFRACT_HF_CACHE", str(data / "hf"))
    monkeypatch.setenv("REFRACT_PROJECTS_DIR", str(data / "projects"))
    monkeypatch.setenv("REFRACT_LIBRARY_DB", str(data / "library.db"))
    return tmp_path


@pytest.fixture()
def client(isolated_dirs):
    from fastapi.testclient import TestClient

    from refract_backend.app import create_app

    app = create_app(token=TOKEN, force_mock=True)
    with TestClient(app) as test_client:
        test_client.headers.update({"X-Refract-Token": TOKEN})
        yield test_client


@pytest.fixture()
def token() -> str:
    return TOKEN
