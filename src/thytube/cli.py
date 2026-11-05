"""Command-line entry point: ``thytube <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import __version__
from . import config as config_mod


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def cmd_serve(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    import uvicorn

    from .db import open_db
    from .web.app import create_app

    cfg.ensure_dirs()
    open_db(cfg.db_path).close()
    app = create_app(cfg)
    uvicorn.run(
        app,
        host=args.host or cfg.server.host,
        port=args.port or cfg.server.port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
    return 0


def cmd_worker(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    from .worker import Worker

    return Worker(cfg).run()


def cmd_migrate(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    from .db import connect, migrate

    cfg.ensure_dirs()
    conn = connect(cfg.db_path)
    applied = migrate(conn)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    print(f"applied: {', '.join(applied) or 'nothing'}; schema version {version}")
    return 0


def cmd_update_ytdlp(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    from .ytdlp.updater import update

    result = update(cfg, force=args.force)
    print(json.dumps(result, indent=2))
    return 0 if result.get("outcome") in ("installed", "kept", "unchanged") else 1


def cmd_rollback_ytdlp(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    from .ytdlp.updater import rollback

    result = rollback(cfg)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def cmd_probe(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    from .health import run_canary
    from .ytdlp.runner import Runner

    cfg.ensure_dirs()
    result = run_canary(cfg, Runner(cfg))
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


def cmd_reconcile(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    from .db import open_db
    from .pool import Pool

    conn = open_db(cfg.db_path)
    report = Pool(cfg).reconcile(conn)
    print(json.dumps(report, indent=2))
    return 0


def cmd_doctor(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    from .doctor import run_doctor

    return run_doctor(cfg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="thytube", description="Private YouTube caching server")
    parser.add_argument("--config", help="path to config.toml (default: $THYTUBE_CONFIG)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"thytube {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the web/API server")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)
    sub.add_parser("worker", help="run the background worker").set_defaults(func=cmd_worker)
    sub.add_parser("migrate", help="apply database migrations").set_defaults(func=cmd_migrate)
    p = sub.add_parser("update-ytdlp", help="install the latest yt-dlp into a new venv (cron)")
    p.add_argument("--force", action="store_true", help="swap even if the canary fails with the new version")
    p.set_defaults(func=cmd_update_ytdlp)
    sub.add_parser("rollback-ytdlp", help="switch back to the previous yt-dlp venv").set_defaults(
        func=cmd_rollback_ytdlp
    )
    sub.add_parser("probe", help="run the canary probe once").set_defaults(func=cmd_probe)
    sub.add_parser("reconcile", help="reconcile the DB with the pool on disk").set_defaults(
        func=cmd_reconcile
    )
    sub.add_parser("doctor", help="check the installation").set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        cfg = config_mod.load(args.config)
    except config_mod.ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    return int(args.func(cfg, args) or 0)


if __name__ == "__main__":
    sys.exit(main())
