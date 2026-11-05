"""Load and validate the TOML configuration.

The file is located via ``--config`` or the ``THYTUBE_CONFIG`` environment variable.
Every key has a default here; ``config.example.toml`` documents them.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent
# src/thytube -> repo root (works for editable installs and the deployed app/ tree)
REPO_ROOT = PACKAGE_DIR.parent.parent


class ConfigError(ValueError):
    pass


@dataclass
class PathsConfig:
    origin: str = ""
    pool: str = ""
    web: str = ""  # static frontend; "" = <repo>/web


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8470
    # In development FastAPI serves /media and the static frontend itself; in production Apache does.
    serve_media: bool = False
    serve_static: bool = False


@dataclass
class PoolConfig:
    max_bytes: int = 1_000_000_000_000
    min_free_bytes: int = 50_000_000_000
    max_pinned_ratio: float = 0.5
    reservation_factor: float = 1.15
    # Fallback size estimate when yt-dlp gives none: duration × ceiling bitrate (bits/s) for the height.
    bitrate_ceiling: dict[str, int] = field(
        default_factory=lambda: {"360": 1_200_000, "480": 2_000_000, "720": 4_000_000, "1080": 8_000_000}
    )


@dataclass
class EvictionConfig:
    grace_days: int = 14


@dataclass
class YtdlpConfig:
    binary: str = ""  # "" = <origin>/ytdlp/current/bin/yt-dlp
    channel: str = "stable"  # stable | nightly
    js_runtime: str = "node"
    cookies: str = ""
    max_height: int = 1080
    sleep_requests: float = 1.0
    sleep_interval: int = 5
    max_sleep_interval: int = 20
    retries: int = 5
    fragment_retries: int = 10
    concurrency: int = 1
    max_attempts: int = 5
    metadata_timeout_s: int = 180
    download_timeout_s: int = 4 * 3600
    search_timeout_s: int = 45
    keep_venvs: int = 3
    extra_pip_packages: list[str] = field(default_factory=list)  # e.g. a PO-token provider plugin


@dataclass
class DiscoveryConfig:
    interval_minutes: int = 120
    jitter_minutes: int = 15
    flat_listing_limit: int = 50
    # "" disables RSS (always use yt-dlp flat listings); used by tests and offline dev.
    rss_url_template: str = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    http_timeout_s: int = 30


@dataclass
class SubtitlesConfig:
    langs: list[str] = field(default_factory=lambda: ["en", "ko"])
    auto: bool = True
    auto_translated: bool = False
    retry_minutes: int = 60
    max_attempts: int = 6


@dataclass
class HealthConfig:
    degraded_after: int = 2
    blocked_after: int = 4
    canary_video: str = "jNQXAC9IVRw"
    probe_min_backoff_minutes: int = 15
    probe_max_backoff_hours: int = 6
    update_on_block_after_hours: int = 6


@dataclass
class WatchConfig:
    ratio: float = 0.5
    max_seconds: int = 600


@dataclass
class Config:
    paths: PathsConfig = field(default_factory=PathsConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    pool: PoolConfig = field(default_factory=PoolConfig)
    eviction: EvictionConfig = field(default_factory=EvictionConfig)
    ytdlp: YtdlpConfig = field(default_factory=YtdlpConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    subtitles: SubtitlesConfig = field(default_factory=SubtitlesConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    source: str = ""

    # ---- derived paths -------------------------------------------------
    @property
    def origin(self) -> Path:
        return Path(self.paths.origin)

    @property
    def pool_dir(self) -> Path:
        return Path(self.paths.pool)

    @property
    def media_dir(self) -> Path:
        return self.pool_dir / "media"

    @property
    def partial_dir(self) -> Path:
        return self.pool_dir / ".partial"

    @property
    def channels_dir(self) -> Path:
        return self.pool_dir / "channels"

    @property
    def db_path(self) -> Path:
        return self.origin / "data" / "thytube.sqlite3"

    @property
    def ytdlp_dir(self) -> Path:
        return self.origin / "ytdlp"

    @property
    def ytdlp_cache_dir(self) -> Path:
        return self.ytdlp_dir / "cache"

    @property
    def run_dir(self) -> Path:
        return self.origin / "run"

    @property
    def logs_dir(self) -> Path:
        return self.origin / "logs"

    @property
    def web_dir(self) -> Path:
        return Path(self.paths.web) if self.paths.web else REPO_ROOT / "web"

    @property
    def ytdlp_binary(self) -> Path:
        if self.ytdlp.binary:
            return Path(self.ytdlp.binary)
        return self.ytdlp_dir / "current" / "bin" / "yt-dlp"

    def ensure_dirs(self) -> None:
        for d in (self.origin / "data", self.run_dir, self.logs_dir, self.ytdlp_cache_dir):
            d.mkdir(parents=True, exist_ok=True)

    def ensure_pool_dirs(self) -> None:
        for d in (self.media_dir, self.partial_dir, self.channels_dir):
            d.mkdir(parents=True, exist_ok=True)


def _build(cls: type, data: dict[str, Any], where: str) -> Any:
    known = {f.name: f for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in known:
            raise ConfigError(f"unknown config key [{where}] {key}")
        f = known[key]
        default = getattr(cls(), key)
        if is_dataclass(default):
            if not isinstance(value, dict):
                raise ConfigError(f"[{key}] must be a table")
            kwargs[key] = _build(type(default), value, key)
            continue
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ConfigError(f"{where}.{key} must be true/false")
        elif isinstance(default, int):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"{where}.{key} must be an integer")
        elif isinstance(default, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f"{where}.{key} must be a number")
            value = float(value)
        elif isinstance(default, str):
            if not isinstance(value, str):
                raise ConfigError(f"{where}.{key} must be a string")
        elif isinstance(default, list):
            if not isinstance(value, list):
                raise ConfigError(f"{where}.{key} must be a list")
        elif isinstance(default, dict) and not isinstance(value, dict):
            raise ConfigError(f"{where}.{key} must be a table")
        kwargs[f.name] = value
    return cls(**kwargs)


def validate(cfg: Config) -> None:
    if not cfg.paths.origin:
        raise ConfigError("paths.origin is required")
    if not cfg.paths.pool:
        raise ConfigError("paths.pool is required")
    if cfg.ytdlp.channel not in ("stable", "nightly"):
        raise ConfigError("ytdlp.channel must be 'stable' or 'nightly'")
    if cfg.ytdlp.concurrency < 1:
        raise ConfigError("ytdlp.concurrency must be ≥ 1")
    if not 0 <= cfg.pool.max_pinned_ratio <= 1:
        raise ConfigError("pool.max_pinned_ratio must be between 0 and 1")
    if cfg.pool.max_bytes <= 0:
        raise ConfigError("pool.max_bytes must be positive")
    if cfg.health.blocked_after < cfg.health.degraded_after:
        raise ConfigError("health.blocked_after must be ≥ health.degraded_after")
    if not 0 < cfg.watch.ratio <= 1:
        raise ConfigError("watch.ratio must be in (0, 1]")


def load(path: str | os.PathLike[str] | None = None) -> Config:
    path = path or os.environ.get("THYTUBE_CONFIG")
    if not path:
        raise ConfigError("no config file: pass --config or set THYTUBE_CONFIG")
    p = Path(path)
    try:
        data = tomllib.loads(p.read_text())
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {p}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{p}: {e}") from None
    cfg = from_dict(data, base=p.resolve().parent)
    cfg.source = str(p)
    return cfg


def from_dict(data: dict[str, Any], base: Path | None = None) -> Config:
    """Relative paths are resolved against ``base`` (the config file's directory), never against
    the current directory: services and cron jobs start in arbitrary places, and a path such as
    "var/lib/app" would otherwise silently create a second app tree under whatever the cwd is."""
    cfg: Config = _build(Config, data, "root")
    root = base or Path.cwd()

    def resolve(v: str) -> str:
        p = Path(v).expanduser()
        return str(p if p.is_absolute() else (root / p).resolve())

    cfg.paths.origin = resolve(cfg.paths.origin) if cfg.paths.origin else ""
    cfg.paths.pool = resolve(cfg.paths.pool) if cfg.paths.pool else ""
    if cfg.paths.web:
        cfg.paths.web = resolve(cfg.paths.web)
    if cfg.ytdlp.binary:
        cfg.ytdlp.binary = resolve(cfg.ytdlp.binary)
    if cfg.ytdlp.cookies:
        cfg.ytdlp.cookies = resolve(cfg.ytdlp.cookies)
    validate(cfg)
    return cfg
