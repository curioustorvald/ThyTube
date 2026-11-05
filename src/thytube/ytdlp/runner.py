"""The single place where yt-dlp argument lists are built and executed.

yt-dlp always runs as a subprocess from its own venv (never imported). Argument lists are
plain argv lists; nothing goes through a shell.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config

STDERR_TAIL_BYTES = 4096
PROGRESS_PREFIX = "[thytube-progress]"
PROGRESS_TEMPLATE = (
    "download:" + PROGRESS_PREFIX + " %(progress.downloaded_bytes)s %(progress.total_bytes)s "
    "%(progress.total_bytes_estimate)s %(progress.speed)s %(progress.eta)s %(info.format_id)s"
)


def format_selector(max_height: int) -> str:
    h = int(max_height)
    return (
        f"bv*[vcodec^=avc1][height<={h}]+ba[acodec^=mp4a]/b[vcodec^=avc1][height<={h}]/bv*[height<={h}]+ba/b"
    )


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def channel_url(channel_id: str, tab: str = "videos") -> str:
    return f"https://www.youtube.com/channel/{channel_id}/{tab}"


@dataclass
class RunResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr_tail: str
    timed_out: bool = False
    cancelled: bool = False
    elapsed: float = 0.0
    lines: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled


def _tail(text: str, limit: int = STDERR_TAIL_BYTES) -> str:
    data = text.encode("utf-8", "replace")
    if len(data) <= limit:
        return text
    return data[-limit:].decode("utf-8", "replace")


class Runner:
    def __init__(self, cfg: Config, binary: str | Path | None = None) -> None:
        self.cfg = cfg
        self.binary = Path(binary) if binary else cfg.ytdlp_binary

    # ---- argv building --------------------------------------------------

    def _exe(self) -> list[str]:
        b = str(self.binary)
        if b.endswith(".py"):
            return [sys.executable, b]
        return [b]

    def base(self, *, interactive: bool = False) -> list[str]:
        y = self.cfg.ytdlp
        argv = [
            *self._exe(),
            "--ignore-config",
            "--no-playlist",
            "--js-runtimes",
            y.js_runtime,
            "--cache-dir",
            str(self.cfg.ytdlp_cache_dir),
            "--color",
            "never",
            "--retries",
            str(y.retries),
        ]
        if not interactive and y.sleep_requests > 0:
            argv += ["--sleep-requests", str(y.sleep_requests)]
        if y.cookies:
            argv += ["--cookies", y.cookies]
        return argv

    def version_argv(self) -> list[str]:
        return [*self._exe(), "--version"]

    def metadata_argv(
        self, video_id: str, max_height: int | None = None, *, interactive: bool = False
    ) -> list[str]:
        # Selecting our format here makes -J report `requested_formats` (and sizes) for exactly what
        # the later --load-info-json download will fetch.
        h = max_height or self.cfg.ytdlp.max_height
        return [*self.base(interactive=interactive), "-f", format_selector(h), "-J", video_url(video_id)]

    def resolve_argv(self, url: str) -> list[str]:
        # Flat so that channel URLs return quickly; a video URL still yields full metadata.
        return [*self.base(interactive=True), "-J", "--flat-playlist", "--playlist-end", "1", url]

    def flat_listing_argv(self, url: str, limit: int, *, interactive: bool = False) -> list[str]:
        argv = self.base(interactive=interactive)
        argv.remove("--no-playlist")
        return [*argv, "--flat-playlist", "-J", "--playlist-end", str(limit), url]

    def search_argv(self, query: str, n: int = 20, kind: str = "video") -> list[str]:
        argv = self.base(interactive=True)
        argv.remove("--no-playlist")
        if kind == "channel":
            # Channel-type results via YouTube's search filter (sp=EgIQAg%3D%3D = type:channel).
            from urllib.parse import quote_plus

            url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp=EgIQAg%253D%253D"
            return [*argv, "--flat-playlist", "-J", "--playlist-end", str(n), url]
        return [*argv, "--flat-playlist", "-J", f"ytsearch{n}:{query}"]

    def download_argv(self, info_json: Path, out_dir: Path, max_height: int) -> list[str]:
        y = self.cfg.ytdlp
        argv = [
            *self.base(),
            "--load-info-json",
            str(info_json),
            "-f",
            format_selector(max_height),
            "--merge-output-format",
            "mp4",
            "--remux-video",
            "mp4",
            "--postprocessor-args",
            "Merger+ffmpeg:-movflags +faststart",
            "--postprocessor-args",
            "VideoRemuxer+ffmpeg:-movflags +faststart",
            "--write-thumbnail",
            "--convert-thumbnails",
            "jpg",
            "--fragment-retries",
            str(y.fragment_retries),
            "--no-mtime",
            "--newline",
            "--progress-template",
            PROGRESS_TEMPLATE,
            "-o",
            str(out_dir / "video.%(ext)s"),
            "-o",
            "thumbnail:" + str(out_dir / "thumb.%(ext)s"),
        ]
        if y.sleep_interval > 0:
            argv += ["--sleep-interval", str(y.sleep_interval)]
            argv += ["--max-sleep-interval", str(max(y.max_sleep_interval, y.sleep_interval))]
        return argv

    def subtitles_argv(self, info_json: Path, out_dir: Path, langs: list[str]) -> list[str]:
        return [
            *self.base(),
            "--load-info-json",
            str(info_json),
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-format",
            "vtt",
            "--sub-langs",
            ",".join(langs),
            "-o",
            "subtitle:" + str(out_dir / "sub.%(ext)s"),
        ]

    def canary_download_argv(self, info_json: Path, out_dir: Path) -> list[str]:
        """A short ranged download to prove media URLs aren't 403ing."""
        return [
            *self.base(interactive=True),
            "--load-info-json",
            str(info_json),
            "-f",
            format_selector(360),
            "--download-sections",
            "*0-5",
            "--no-mtime",
            "-o",
            str(out_dir / "canary.%(ext)s"),
        ]

    # ---- execution ------------------------------------------------------

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def run(
        self,
        argv: list[str],
        timeout: float,
        *,
        on_line: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
        cwd: Path | None = None,
    ) -> RunResult:
        """Run synchronously. stdout is captured whole; if ``on_line`` is given, stdout lines
        are streamed to it (and not accumulated)."""
        start = time.monotonic()
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=self._env(),
            cwd=str(cwd) if cwd else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        stderr_size = [0]

        def read_stdout() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                if on_line is not None:
                    try:
                        on_line(line.rstrip("\n"))
                    except Exception:  # noqa: BLE001 - a callback bug must not kill the read loop
                        pass
                else:
                    stdout_parts.append(line)

        def read_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_parts.append(line)
                stderr_size[0] += len(line)
                if on_line is not None:
                    try:
                        on_line(line.rstrip("\n"))
                    except Exception:  # noqa: BLE001
                        pass
                # Keep memory bounded on chatty runs.
                if stderr_size[0] > 8 * STDERR_TAIL_BYTES:
                    joined = _tail("".join(stderr_parts), 2 * STDERR_TAIL_BYTES)
                    stderr_parts[:] = [joined]
                    stderr_size[0] = len(joined)

        threads = [
            threading.Thread(target=read_stdout, daemon=True),
            threading.Thread(target=read_stderr, daemon=True),
        ]
        for t in threads:
            t.start()

        timed_out = cancelled = False
        deadline = start + timeout
        while True:
            try:
                proc.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel.is_set():
                cancelled = True
                _kill(proc)
                break
            if time.monotonic() > deadline:
                timed_out = True
                _kill(proc)
                break
        for t in threads:
            t.join(timeout=5)
        return RunResult(
            argv=argv,
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout="".join(stdout_parts),
            stderr_tail=_tail("".join(stderr_parts)),
            timed_out=timed_out,
            cancelled=cancelled,
            elapsed=time.monotonic() - start,
        )

    async def run_async(self, argv: list[str], timeout: float) -> RunResult:
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env=self._env(),
            start_new_session=True,
        )
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, err = await proc.communicate()
        return RunResult(
            argv=argv,
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=out.decode("utf-8", "replace"),
            stderr_tail=_tail(err.decode("utf-8", "replace")),
            timed_out=timed_out,
            elapsed=time.monotonic() - start,
        )


def _kill(proc: subprocess.Popen[str]) -> None:
    """Terminate the whole process group (yt-dlp spawns ffmpeg and node)."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


@dataclass
class Progress:
    downloaded: int = 0
    total: int | None = None
    speed: float | None = None
    eta: int | None = None
    format_id: str = ""


def parse_progress(line: str) -> Progress | None:
    if not line.startswith(PROGRESS_PREFIX):
        return None
    parts = line[len(PROGRESS_PREFIX) :].split()
    if len(parts) < 5:
        return None

    def num(s: str) -> float | None:
        try:
            return float(s)
        except ValueError:
            return None

    downloaded = num(parts[0])
    total = num(parts[1]) or num(parts[2])
    speed = num(parts[3])
    eta = num(parts[4])
    return Progress(
        downloaded=int(downloaded or 0),
        total=int(total) if total else None,
        speed=speed,
        eta=int(eta) if eta is not None else None,
        format_id=parts[5] if len(parts) > 5 else "",
    )
