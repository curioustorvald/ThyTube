from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from thytube import config as config_mod
from thytube.db import open_db

FAKE = Path(__file__).parent / "fake_ytdlp.py"


@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("fake-samples")


@pytest.fixture
def scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_dir: Path):
    """Write/update the fake yt-dlp scenario: ``scenario({"videos": {...}})``."""
    path = tmp_path / "scenario.json"
    path.write_text("{}")
    log = tmp_path / "ytdlp-calls.jsonl"
    monkeypatch.setenv("FAKE_YTDLP_SCENARIO", str(path))
    monkeypatch.setenv("FAKE_YTDLP_LOG", str(log))
    monkeypatch.setenv("FAKE_YTDLP_SAMPLE_DIR", str(sample_dir))
    monkeypatch.setenv("FAKE_YTDLP_DURATION", "3")
    monkeypatch.delenv("FAKE_YTDLP_MODE", raising=False)

    def set_scenario(data: dict[str, Any] | None = None) -> Path:
        if data is not None:
            path.write_text(json.dumps(data))
        return path

    set_scenario.log = log  # type: ignore[attr-defined]

    def calls() -> list[list[str]]:
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    set_scenario.calls = calls  # type: ignore[attr-defined]
    return set_scenario


@pytest.fixture
def cfg(tmp_path: Path, scenario) -> config_mod.Config:
    c = config_mod.from_dict(
        {
            "paths": {"origin": str(tmp_path / "origin"), "pool": str(tmp_path / "pool")},
            "server": {"serve_media": True, "serve_static": True},
            "pool": {"max_bytes": 10_000_000, "min_free_bytes": 0},
            "ytdlp": {
                "binary": str(FAKE),
                "sleep_requests": 0.0,
                "sleep_interval": 0,
                "max_sleep_interval": 0,
                "metadata_timeout_s": 60,
            },
            "discovery": {"rss_url_template": ""},
            "health": {"probe_min_backoff_minutes": 15},
        }
    )
    c.ensure_dirs()
    c.ensure_pool_dirs()
    return c


@pytest.fixture
def conn(cfg: config_mod.Config):
    c = open_db(cfg.db_path)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never hit YouTube."""
    import urllib.request

    def blocked(*a: Any, **k: Any) -> Any:
        raise AssertionError("network access in tests")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    os.environ.pop("THYTUBE_CONFIG", None)
