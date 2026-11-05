"""``thytube doctor``: check that the installation is usable."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import db
from .config import Config
from .ytdlp.updater import version_of


def _check(ok: bool, label: str, detail: str = "") -> bool:
    mark = "ok  " if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def _warn(label: str, detail: str = "") -> None:
    print(f"[warn] {label}" + (f" — {detail}" if detail else ""))


def _writable(p: Path) -> bool:
    return p.exists() and os.access(p, os.W_OK)


def run_doctor(cfg: Config) -> int:
    good = True
    print(f"config: {cfg.source or '(dict)'}")
    cfg.ensure_dirs()
    good &= _check(_writable(cfg.origin), "origin is writable", str(cfg.origin))
    try:
        cfg.ensure_pool_dirs()
        pool_ok = _writable(cfg.media_dir) and _writable(cfg.partial_dir)
    except OSError as e:
        pool_ok = False
        _warn("pool", str(e))
    good &= _check(pool_ok, "pool is writable", str(cfg.pool_dir))
    if pool_ok:
        same = os.stat(cfg.media_dir).st_dev == os.stat(cfg.partial_dir).st_dev
        good &= _check(same, ".partial and media are on one filesystem (atomic rename)")
        du = shutil.disk_usage(cfg.pool_dir)
        _check(
            du.free > cfg.pool.min_free_bytes,
            "pool filesystem free space",
            f"{du.free / 1e9:.0f} GB free, min_free_bytes {cfg.pool.min_free_bytes / 1e9:.0f} GB, "
            f"budget {cfg.pool.max_bytes / 1e9:.0f} GB",
        )
    try:
        conn = db.open_db(cfg.db_path)
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        good &= _check(mode == "wal", "database", f"{cfg.db_path}, schema v{v}, journal {mode}")
        conn.close()
    except Exception as e:  # noqa: BLE001
        good &= _check(False, "database", str(e))

    version = version_of(cfg.ytdlp_binary)
    good &= _check(version is not None, "yt-dlp runs", f"{cfg.ytdlp_binary}: {version or 'not found'}")
    if version is None and not cfg.ytdlp.binary:
        print("       run `thytube update-ytdlp` to install it")
    rt = cfg.ytdlp.js_runtime.split(":")[0]
    rt_path = shutil.which(rt)
    if rt_path:
        out = subprocess.run([rt_path, "--version"], capture_output=True, text=True, timeout=30)
        _check(out.returncode == 0, f"JS runtime '{rt}'", out.stdout.strip())
    else:
        good &= _check(False, f"JS runtime '{rt}' on PATH", "yt-dlp needs it for YouTube")
    for tool in ("ffmpeg", "ffprobe"):
        good &= _check(shutil.which(tool) is not None, f"{tool} on PATH")
    if cfg.ytdlp.cookies:
        _check(Path(cfg.ytdlp.cookies).is_file(), "cookies file", cfg.ytdlp.cookies)
        _warn("cookies are enabled", "the account they belong to may get flagged by Google")
    web = cfg.web_dir
    good &= _check((web / "index.html").is_file(), "frontend", str(web))
    good &= _check((web / "vendor" / "preact-htm.js").is_file(), "vendored Preact + htm")
    print("all good" if good else "problems found")
    return 0 if good else 1
