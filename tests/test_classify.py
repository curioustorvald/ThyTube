from pathlib import Path

import pytest

from thytube.ytdlp import classify as c

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "ytdlp_stderr").glob("*.txt"))


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_fixture(path: Path) -> None:
    expected_cls, expected_reason, _ = path.stem.split("__")
    result = c.classify_text(1, path.read_text())
    assert (result.cls, result.reason) == (expected_cls, expected_reason)
    assert result.message


def test_ok() -> None:
    assert c.classify_text(0, "WARNING: whatever").cls == c.OK


def test_timeout() -> None:
    r = c.classify_text(0, "", timed_out=True)
    assert (r.cls, r.reason) == (c.TRANSIENT, "timeout")


def test_message_is_last_error_line() -> None:
    msg = c.error_message("WARNING: a\nERROR: first\nERROR: second\n")
    assert msg == "second"


def test_unknown_retryable() -> None:
    r = c.classify_text(2, "something odd")
    assert r.cls == c.UNKNOWN and r.retryable
