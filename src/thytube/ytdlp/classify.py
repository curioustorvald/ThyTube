"""Classify a yt-dlp run from its exit code + stderr.

Classes:
  ok         success
  transient  network trouble, 5xx, a single 429 → retry with backoff
  video      this video can't be had (private/removed/members/geo/age, or not live yet)
  systemic   YouTube is blocking us or yt-dlp is broken → feeds the health state machine
  unknown    anything else (treated as transient, logged with the stderr tail)

When a new failure shows up in the wild, add a sample to tests/fixtures/ytdlp_stderr/ and a
pattern below. Order matters: the first match wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .runner import RunResult

OK = "ok"
TRANSIENT = "transient"
VIDEO = "video"
SYSTEMIC = "systemic"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Classification:
    cls: str
    reason: str
    message: str = ""

    @property
    def retryable(self) -> bool:
        return self.cls in (TRANSIENT, UNKNOWN)


# (class, reason, pattern) — matched case-insensitively against the stderr tail.
PATTERNS: list[tuple[str, str, str]] = [
    # --- not live yet: re-queue at the scheduled start --------------------------------------
    (
        VIDEO,
        "upcoming",
        r"(this live event will begin|premieres? in|premiere will begin|scheduled to start|live event will begin in a few moments)",
    ),
    # --- age gate must come before the bot check (both say "Sign in to confirm") -------------
    (VIDEO, "age_restricted", r"sign in to confirm your age|age[- ]restricted|inappropriate for some users"),
    # --- systemic: YouTube blocking us / yt-dlp broken ----------------------------------------
    (
        SYSTEMIC,
        "bot_check",
        r"sign in to confirm you(?:['’]re| are) not a bot|confirm you(?:['’]re| are) not a bot",
    ),
    (SYSTEMIC, "po_token", r"po[ _-]?token|gvs po token|proof of origin"),
    (
        SYSTEMIC,
        "js_challenge",
        r"nsig extraction failed|n challenge solving failed|signature extraction failed|unable to extract (?:nsig|sig|player)|no supported javascript runtime|failed to (?:solve|decrypt).{0,40}(?:signature|challenge)|jsc?[_ ]?(?:challenge|runtime)",
    ),
    (
        SYSTEMIC,
        "player_response",
        r"unable to extract (?:initial player response|yt initial data|ytcfg)|failed to extract any player response",
    ),
    (SYSTEMIC, "forbidden", r"http error 403|403: forbidden|got error: 403|fragment \d+ not found.*403"),
    (
        SYSTEMIC,
        "format_unavailable",
        r"requested format is not available|no video formats found|only images are available",
    ),
    (
        SYSTEMIC,
        "sign_in_required",
        r"sign in to confirm|this content isn['’]t available|the following content is not available on this app|please sign in",
    ),
    # --- video-specific -----------------------------------------------------------------------
    (VIDEO, "private", r"private video|this video is private"),
    (
        VIDEO,
        "members_only",
        r"members[- ]only|join this channel to get access|available to this channel['’]s members",
    ),
    (VIDEO, "copyright", r"copyright (?:claim|grounds)|due to a copyright"),
    (
        VIDEO,
        "geo_blocked",
        r"not (?:made this video )?available in your country|geo[- ]?restrict|blocked it in your country",
    ),
    (
        VIDEO,
        "live_only",
        r"this live stream recording is not available|live stream offline|is offline|recording is not available",
    ),
    (VIDEO, "payment_required", r"requires payment|purchase this video|rental"),
    (
        VIDEO,
        "removed",
        r"has been removed|account associated with this video has been terminated|violating youtube['’]s|no longer available|video unavailable|this video does not exist|does not exist|incomplete youtube id|is not a valid url",
    ),
    # --- transient ----------------------------------------------------------------------------
    (TRANSIENT, "rate_limited", r"http error 429|too many requests|429:"),
    (
        TRANSIENT,
        "server_error",
        r"http error 5\d\d|50[0-4]: |internal server error|service unavailable|bad gateway",
    ),
    (
        TRANSIENT,
        "network",
        r"temporary failure in name resolution|name or service not known|connection (?:reset|refused|aborted)|network is unreachable|timed out|timeout|remote end closed connection|incompleteread|urlopen error|ssl:|eof occurred in violation|unable to download (?:webpage|api page|json metadata)|giving up after \d+ retries|did not get any data blocks",
    ),
    (TRANSIENT, "disk_full", r"no space left on device"),
]

_COMPILED = [(c, r, re.compile(p, re.IGNORECASE)) for c, r, p in PATTERNS]
_ERROR_LINE = re.compile(r"^ERROR:\s*(.*)$", re.MULTILINE)


def error_message(stderr: str) -> str:
    """The most relevant human-readable line: the last ERROR: line, else the last non-empty line."""
    errors = _ERROR_LINE.findall(stderr)
    if errors:
        return errors[-1].strip()[:500]
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    return lines[-1][:500] if lines else ""


def classify_text(returncode: int, stderr: str, *, timed_out: bool = False) -> Classification:
    if timed_out:
        return Classification(TRANSIENT, "timeout", "yt-dlp timed out")
    if returncode == 0:
        return Classification(OK, "ok")
    msg = error_message(stderr)
    # Prefer matching the ERROR: lines; fall back to the whole tail (warnings carry nsig/PO-token hints).
    error_text = "\n".join(_ERROR_LINE.findall(stderr))
    found: tuple[str, str] | None = None
    for text in (error_text, stderr):
        if text and found is None:
            found = _first_match(text)
    if found is None:
        return Classification(UNKNOWN, "unknown", msg or f"yt-dlp exited with code {returncode}")
    cls, reason = found
    if cls == SYSTEMIC:
        # A generic systemic ERROR ("Requested format is not available") is usually explained by an
        # earlier WARNING (nsig, PO token…); report the more specific cause.
        for c, r, rx in _COMPILED:
            if r == reason:
                break
            if c == SYSTEMIC and rx.search(stderr):
                reason = r
                break
    return Classification(cls, reason, msg)


def _first_match(text: str) -> tuple[str, str] | None:
    for cls, reason, rx in _COMPILED:
        if rx.search(text):
            return cls, reason
    return None


def classify(result: RunResult) -> Classification:
    if result.cancelled:
        return Classification(TRANSIENT, "cancelled", "cancelled")
    return classify_text(result.returncode, result.stderr_tail, timed_out=result.timed_out)
