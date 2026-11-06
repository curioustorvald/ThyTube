"""The PWA shell list must stay in sync with the frontend files."""

import json
import re
from pathlib import Path

WEB = Path(__file__).parent.parent / "web"


def shell_list() -> set[str]:
    src = (WEB / "sw.js").read_text()
    block = re.search(r"const SHELL = \[(.*?)\];", src, re.S).group(1)
    return set(re.findall(r"'([^']+)'", block))


def test_service_worker_precaches_every_shell_file() -> None:
    on_disk = {
        p.relative_to(WEB).as_posix()
        for pattern in ("js/**/*.js", "css/*.css", "vendor/*.js", "img/*.png", "lang/*.json")
        for p in WEB.glob(pattern)
    } | {"index.html", "manifest.webmanifest", "icon.svg"}
    listed = shell_list() - {"./"}
    assert listed == on_disk, f"missing: {on_disk - listed}, stale: {listed - on_disk}"


def test_manifest_icons_exist() -> None:
    m = json.loads((WEB / "manifest.webmanifest").read_text())
    assert m["display"] == "standalone" and m["start_url"]
    for icon in m["icons"]:
        assert (WEB / icon["src"]).is_file(), icon["src"]
    assert any(i["purpose"] == "maskable" for i in m["icons"])


def test_build_stamp_is_replaceable() -> None:
    # deploy/remote-install.sh rewrites this exact line.
    assert re.search(r"^const BUILD = '[^']*';", (WEB / "sw.js").read_text(), re.M)


def test_dev_server_serves_manifest_type(cfg) -> None:
    from fastapi.testclient import TestClient

    from thytube.web.app import create_app

    r = TestClient(create_app(cfg)).get("/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
