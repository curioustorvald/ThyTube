import json
from pathlib import Path

import pytest

from thytube import config as config_mod
from thytube import db
from thytube.ytdlp import classify
from thytube.ytdlp.runner import Runner, format_selector, parse_progress


def test_config_defaults_and_validation(tmp_path: Path) -> None:
    c = config_mod.from_dict({"paths": {"origin": str(tmp_path), "pool": str(tmp_path / "p")}})
    assert c.pool.max_bytes == 1_000_000_000_000
    assert c.ytdlp_binary == tmp_path / "ytdlp" / "current" / "bin" / "yt-dlp"
    with pytest.raises(config_mod.ConfigError, match="unknown config key"):
        config_mod.from_dict({"paths": {"origin": "x", "pool": "y", "nope": 1}})
    with pytest.raises(config_mod.ConfigError, match="integer"):
        config_mod.from_dict({"paths": {"origin": "x", "pool": "y"}, "pool": {"max_bytes": "lots"}})
    with pytest.raises(config_mod.ConfigError, match="origin"):
        config_mod.from_dict({})


def test_example_config_loads() -> None:
    example = Path(__file__).parent.parent / "config.example.toml"
    c = config_mod.load(example)
    assert c.ytdlp.js_runtime == "node"


def test_migrate_is_idempotent(conn) -> None:
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 1
    assert db.migrate(conn) == []
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_kv_events_commands(conn) -> None:
    db.kv_set(conn, "x", {"a": 1})
    assert db.kv_get(conn, "x") == {"a": 1}
    assert db.kv_get(conn, "missing", 5) == 5
    db.add_event(conn, "info", "test", "hello", {"k": 1})
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    cid = db.enqueue_command(conn, "probe", {"why": "test"})
    rows = db.take_commands(conn)
    assert [r["id"] for r in rows] == [cid]
    assert db.take_commands(conn) == []
    db.finish_command(conn, cid, {"ok": True})
    assert json.loads(conn.execute("SELECT result_json FROM commands").fetchone()[0]) == {"ok": True}


def test_runner_argv(cfg) -> None:
    r = Runner(cfg)
    argv = r.metadata_argv("abcdefghijk")
    assert "--ignore-config" in argv and "--no-playlist" in argv
    assert argv[argv.index("--js-runtimes") + 1] == "node"
    assert "shell" not in argv
    dl = r.download_argv(Path("/x/info.json"), Path("/x"), 720)
    assert dl[dl.index("-f") + 1] == format_selector(720)
    assert "[height<=720]" in format_selector(720)
    assert "--no-playlist" not in r.flat_listing_argv("https://www.youtube.com/channel/UCx/videos", 5)


def test_runner_runs_fake(cfg, scenario) -> None:
    r = Runner(cfg)
    res = r.run(r.metadata_argv("abcdefghijk"), timeout=60)
    assert res.ok
    info = json.loads(res.stdout)
    assert info["id"] == "abcdefghijk"

    scenario({"videos": {"abcdefghijk": {"mode": "botcheck"}}})
    res = r.run(r.metadata_argv("abcdefghijk"), timeout=60)
    assert not res.ok
    k = classify.classify(res)
    assert (k.cls, k.reason) == (classify.SYSTEMIC, "bot_check")


def test_runner_timeout(cfg, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_YTDLP_MODE", "hang")
    r = Runner(cfg)
    res = r.run(r.metadata_argv("abcdefghijk"), timeout=1)
    assert res.timed_out and not res.ok
    assert classify.classify(res).cls == classify.TRANSIENT


def test_parse_progress() -> None:
    p = parse_progress("[thytube-progress] 100 NA 1000 2048.5 3 137")
    assert p is not None and p.downloaded == 100 and p.total == 1000 and p.eta == 3
    assert parse_progress("[download] 5%") is None
