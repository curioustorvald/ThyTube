"""Subscription rules: title regexes (applied at discovery) and metadata rules (duration, shorts,
streams; applied when full metadata is known)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

STREAM_STATES = {"is_live", "was_live", "post_live", "is_upcoming"}


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""


def validate_regex(pattern: str | None, case_sensitive: bool = False) -> str | None:
    """Return an error message, or None when the pattern is valid (or empty)."""
    if not pattern:
        return None
    try:
        re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
    except re.error as e:
        return f"{e.msg} at position {e.pos}" if e.pos is not None else e.msg
    return None


def _get(rules: Mapping[str, Any], key: str) -> Any:
    try:
        return rules[key]
    except (KeyError, IndexError):
        return None


def title_verdict(rules: Mapping[str, Any], title: str | None) -> Verdict:
    title = title or ""
    flags = 0 if _get(rules, "case_sensitive") else re.IGNORECASE
    inc = _get(rules, "include_regex")
    exc = _get(rules, "exclude_regex")
    if inc and not re.search(inc, title, flags):
        return Verdict(False, "title: doesn't match include pattern")
    if exc and re.search(exc, title, flags):
        return Verdict(False, "title: matches exclude pattern")
    return Verdict(True)


def is_short(info: Mapping[str, Any]) -> bool:
    if info.get("media_type") == "short":
        return True
    url = str(info.get("webpage_url") or info.get("url") or "")
    if "/shorts/" in url:
        return True
    duration = info.get("duration") or 0
    w, h = info.get("width") or 0, info.get("height") or 0
    return 0 < duration <= 180 and h > w > 0


def is_stream(info: Mapping[str, Any]) -> bool:
    return info.get("live_status") in STREAM_STATES or bool(info.get("was_live"))


def duration_verdict(rules: Mapping[str, Any], duration: float | None) -> Verdict:
    if duration is None:
        return Verdict(True)
    lo, hi = _get(rules, "min_duration_s"), _get(rules, "max_duration_s")
    if lo is not None and duration < lo:
        return Verdict(False, f"duration: shorter than {_fmt(lo)}")
    if hi is not None and duration > hi:
        return Verdict(False, f"duration: longer than {_fmt(hi)}")
    return Verdict(True)


def metadata_verdict(rules: Mapping[str, Any], info: Mapping[str, Any]) -> Verdict:
    v = duration_verdict(rules, info.get("duration"))
    if not v.ok:
        return v
    if is_short(info) and not _get(rules, "include_shorts"):
        return Verdict(False, "shorts excluded")
    if is_stream(info) and not _get(rules, "include_streams"):
        return Verdict(False, "streams excluded")
    return Verdict(True)


def listing_verdict(rules: Mapping[str, Any], entry: Mapping[str, Any]) -> Verdict:
    """Everything that can be decided from a flat listing / RSS entry."""
    v = title_verdict(rules, entry.get("title"))
    if not v.ok:
        return v
    v = duration_verdict(rules, entry.get("duration"))
    if not v.ok:
        return v
    if entry.get("is_short") and not _get(rules, "include_shorts"):
        return Verdict(False, "shorts excluded")
    if entry.get("live_status") in STREAM_STATES and not _get(rules, "include_streams"):
        return Verdict(False, "streams excluded")
    return Verdict(True)


def _fmt(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
