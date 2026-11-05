"""Install yt-dlp into a fresh versioned venv, canary-test it, and swap the ``current`` symlink.

    ytdlp/venv-YYYYmmdd-HHMMSS/   one venv per install (the last ``keep_venvs`` are kept)
    ytdlp/current -> venv-…       atomic symlink; the app runs current/bin/yt-dlp

If the new version fails the canary while the old one passes, the old one stays (automatic rollback).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import db
from ..config import Config
from ..health import run_canary
from .runner import Runner

log = logging.getLogger(__name__)

Installer = Callable[[Config, Path], None]
Canary = Callable[[Config, Path], dict[str, Any]]


def pip_installer(cfg: Config, venv: Path) -> None:
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=300)
    pip = [str(venv / "bin" / "python"), "-m", "pip", "install", "--quiet", "--upgrade"]
    subprocess.run([*pip, "pip"], check=True, timeout=600, capture_output=True)
    pkgs = ["yt-dlp[default]", *cfg.ytdlp.extra_pip_packages]
    if cfg.ytdlp.channel == "nightly":
        pip.append("--pre")
    out = subprocess.run([*pip, *pkgs], timeout=900, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"pip install failed: {out.stderr.strip()[-1500:]}")


def default_canary(cfg: Config, binary: Path) -> dict[str, Any]:
    return run_canary(cfg, Runner(cfg, binary=binary))


def binary_of(venv: Path) -> Path:
    return venv / "bin" / "yt-dlp"


def version_of(binary: Path) -> str | None:
    if not binary.exists():
        return None
    try:
        argv = [sys.executable, str(binary)] if str(binary).endswith(".py") else [str(binary)]
        out = subprocess.run([*argv, "--version"], capture_output=True, text=True, timeout=60)
        return out.stdout.strip().splitlines()[-1] if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def venvs(cfg: Config) -> list[Path]:
    if not cfg.ytdlp_dir.exists():
        return []
    return sorted(p for p in cfg.ytdlp_dir.glob("venv-*") if p.is_dir())


def current_venv(cfg: Config) -> Path | None:
    link = cfg.ytdlp_dir / "current"
    if not link.is_symlink():
        return None
    return (cfg.ytdlp_dir / os.readlink(link)).resolve()


def swap(cfg: Config, venv: Path) -> None:
    tmp = cfg.ytdlp_dir / f".current.{os.getpid()}"
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(venv.name)
    os.replace(tmp, cfg.ytdlp_dir / "current")


def prune(cfg: Config) -> list[str]:
    keep = max(cfg.ytdlp.keep_venvs, 2)
    cur = current_venv(cfg)
    all_v = venvs(cfg)
    removed = []
    for v in all_v[:-keep]:
        if cur is not None and v.resolve() == cur:
            continue
        shutil.rmtree(v, ignore_errors=True)
        removed.append(v.name)
    return removed


def _record(cfg: Config, result: dict[str, Any]) -> None:
    try:
        conn = db.open_db(cfg.db_path)
    except Exception:  # noqa: BLE001
        log.exception("could not record update result")
        return
    try:
        with db.tx(conn):
            info = db.kv_get(conn, "ytdlp") or {}
            history = info.get("history", [])
            history.insert(0, result)
            info.update(
                {
                    "version": result.get("active_version") or info.get("version"),
                    "last_update": result,
                    "history": history[:20],
                }
            )
            db.kv_set(conn, "ytdlp", info)
            level = {"installed": "info", "unchanged": "info", "kept": "warn"}.get(result["outcome"], "error")
            db.add_event(conn, level, "ytdlp_update", result["message"], result)
            if result["outcome"] in ("installed", "kept", "rolled_back"):
                db.enqueue_command(conn, "probe", {"reason": "after yt-dlp update"})
    finally:
        conn.close()


def _set_updating(cfg: Config, on: bool) -> None:
    try:
        conn = db.open_db(cfg.db_path)
        if on:
            db.kv_set(conn, "ytdlp_updating", {"started": time.time()})
        else:
            db.kv_delete(conn, "ytdlp_updating")
        conn.close()
    except Exception:  # noqa: BLE001
        log.exception("could not set updating flag")


def update(
    cfg: Config,
    *,
    force: bool = False,
    installer: Installer = pip_installer,
    canary: Canary = default_canary,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    lock_path = cfg.run_dir / "update-ytdlp.lock"
    import fcntl

    lock = open(lock_path, "w")  # noqa: SIM115
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return {"outcome": "busy", "message": "another update is running"}
    _set_updating(cfg, True)
    try:
        result = _update(cfg, force, installer, canary)
    except Exception as e:  # noqa: BLE001
        log.exception("update failed")
        result = {"outcome": "error", "message": f"update failed: {e}"}
    finally:
        _set_updating(cfg, False)
        lock.close()
    result["at"] = db.now_iso()
    _record(cfg, result)
    return result


def _update(cfg: Config, force: bool, installer: Installer, canary: Canary) -> dict[str, Any]:
    old_venv = current_venv(cfg)
    old_version = version_of(binary_of(old_venv)) if old_venv else None
    new_venv = cfg.ytdlp_dir / f"venv-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    n = 2
    base_name = new_venv.name
    while new_venv.exists():
        new_venv = new_venv.with_name(f"{base_name}-{n}")
        n += 1
    try:
        installer(cfg, new_venv)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(new_venv, ignore_errors=True)
        return {"outcome": "error", "message": f"install failed: {e}", "active_version": old_version}
    new_version = version_of(binary_of(new_venv))
    if new_version is None:
        shutil.rmtree(new_venv, ignore_errors=True)
        return {"outcome": "error", "message": "new yt-dlp does not run", "active_version": old_version}

    if old_venv is not None and new_version == old_version and not force:
        shutil.rmtree(new_venv, ignore_errors=True)
        return {
            "outcome": "unchanged",
            "message": f"yt-dlp {old_version} is already the latest",
            "active_version": old_version,
            "new_version": new_version,
        }

    new_probe = canary(cfg, binary_of(new_venv))
    base = {"new_version": new_version, "old_version": old_version, "new_canary": _brief(new_probe)}
    if old_venv is None:
        swap(cfg, new_venv)
        prune(cfg)
        return {
            **base,
            "outcome": "installed",
            "active_version": new_version,
            "message": f"Installed yt-dlp {new_version}" + ("" if new_probe["ok"] else " (canary failed)"),
        }
    if new_probe["ok"] or force:
        swap(cfg, new_venv)
        removed = prune(cfg)
        return {
            **base,
            "outcome": "installed",
            "active_version": new_version,
            "pruned": removed,
            "message": f"Updated yt-dlp {old_version} → {new_version}",
        }
    old_probe = canary(cfg, binary_of(old_venv))
    base["old_canary"] = _brief(old_probe)
    if old_probe["ok"]:
        shutil.rmtree(new_venv, ignore_errors=True)
        return {
            **base,
            "outcome": "kept",
            "active_version": old_version,
            "message": f"yt-dlp {new_version} failed the canary; keeping {old_version}",
        }
    swap(cfg, new_venv)
    removed = prune(cfg)
    return {
        **base,
        "outcome": "installed",
        "active_version": new_version,
        "pruned": removed,
        "message": f"Updated yt-dlp {old_version} → {new_version} (both fail the canary)",
    }


def _brief(probe: dict[str, Any]) -> dict[str, Any]:
    return {k: probe.get(k) for k in ("ok", "stage", "reason", "message")}


def rollback(cfg: Config) -> dict[str, Any]:
    cur = current_venv(cfg)
    older = [v for v in venvs(cfg) if cur is None or v.resolve() < cur]
    if not older:
        result = {"ok": False, "outcome": "error", "message": "no previous yt-dlp version to roll back to"}
    else:
        target = older[-1]
        swap(cfg, target)
        version = version_of(binary_of(target))
        result = {
            "ok": True,
            "outcome": "rolled_back",
            "active_version": version,
            "message": f"Rolled back yt-dlp to {version} ({target.name})",
        }
    result["at"] = db.now_iso()
    _record(cfg, result)
    return result


def refresh_version(cfg: Config) -> str | None:
    """Record the version of the active binary (worker start-up)."""
    return version_of(cfg.ytdlp_binary)
