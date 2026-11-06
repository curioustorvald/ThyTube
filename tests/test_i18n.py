"""The frontend's language files must cover every string key the code asks for."""

import json
import re
from pathlib import Path

WEB = Path(__file__).parent.parent / "web"
LANG = WEB / "lang"
DEFAULT = "en"
PLURAL_FORMS = {"zero", "one", "two", "few", "many", "other"}

# t('a.b') / tr('a.b') with a literal key. Keys built at run time (t('state.' + s)) aren't checked here.
CALL = re.compile(r"\btr?\(\s*'([A-Za-z0-9_.]*[A-Za-z0-9_])'")
# Keys passed around as data, e.g. MODES = [['channel', 'watch.modeChannel', …]] → t(label).
DATA_KEY = re.compile(r"'((?:nav|watch|player|status|filter|manage|common)\.[A-Za-z0-9_.]*[A-Za-z0-9_])'")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def load(code: str) -> dict:
    return json.loads((LANG / f"{code}.json").read_text(encoding="utf-8"))


def flatten(table: dict, prefix: str = "") -> dict[str, str | dict]:
    """dotted key → string, or plural dict."""
    out: dict[str, str | dict] = {}
    for k, v in table.items():
        if k == "_meta":
            continue
        key = f"{prefix}{k}"
        if isinstance(v, dict) and not (v.keys() <= PLURAL_FORMS and "other" in v):
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


def used_keys() -> set[str]:
    keys: set[str] = set()
    for p in (WEB / "js").rglob("*.js"):
        src = p.read_text(encoding="utf-8")
        keys |= set(CALL.findall(src)) | set(DATA_KEY.findall(src))
    return keys


def placeholders(v: str | dict) -> set[str]:
    texts = v.values() if isinstance(v, dict) else [v]
    return {m for s in texts for m in PLACEHOLDER.findall(s)}


def test_default_language_has_every_key_the_code_uses() -> None:
    strings = flatten(load(DEFAULT))
    missing = {k for k in used_keys() if k not in strings and not any(s.startswith(k + ".") for s in strings)}
    assert not missing, f"keys used in web/js but missing from lang/{DEFAULT}.json: {sorted(missing)}"


def test_dynamic_key_families_are_complete() -> None:
    strings = flatten(load(DEFAULT))
    for state in (
        "cached",
        "queued",
        "downloading",
        "evicted",
        "filtered",
        "unavailable",
        "failed",
        "deferred_space",
    ):
        assert f"state.{state}" in strings
    for unit in ("year", "month", "week", "day", "hour", "minute"):
        assert f"time.past.{unit}" in strings and f"time.future.{unit}" in strings
    for st in ("ok", "degraded", "blocked", "updating"):
        assert (
            f"health.{st}" in strings
            and f"status.title.{st}" in strings
            and f"status.explain.{st}" in strings
        )
    for bg in ("bgNone", "bgLight", "bgSolid"):
        assert f"player.{bg}" in strings
    for mode in ("auto", "light", "dark"):
        assert f"theme.{mode}" in strings and f"theme.{mode}Now" in strings


def test_values_are_strings_or_plurals() -> None:
    for code_file in LANG.glob("*.json"):
        data = json.loads(code_file.read_text(encoding="utf-8"))
        assert data.get("_meta", {}).get("locale"), f"{code_file.name}: _meta.locale is required"
        for k, v in flatten(data).items():
            if isinstance(v, dict):
                assert set(v) <= PLURAL_FORMS and "other" in v, f"{code_file.name}: {k}"
                assert all(isinstance(s, str) for s in v.values()), f"{code_file.name}: {k}"
            else:
                assert isinstance(v, str), f"{code_file.name}: {k}"


def test_translations_match_the_default() -> None:
    base = flatten(load(DEFAULT))
    for f in LANG.glob("*.json"):
        if f.stem == DEFAULT:
            continue
        other = flatten(json.loads(f.read_text(encoding="utf-8")))
        unknown = set(other) - set(base)
        assert not unknown, f"{f.name} has keys that {DEFAULT}.json lacks: {sorted(unknown)}"
        for k, v in other.items():
            assert placeholders(v) <= placeholders(base[k]), f"{f.name}: {k} uses unknown placeholders"


def test_every_language_file_is_registered() -> None:
    src = (WEB / "js" / "i18n.js").read_text(encoding="utf-8")
    registered = set(
        re.findall(r"^\s+(\w+): '", re.search(r"LANGUAGES = \{(.*?)\};", src, re.S).group(1), re.M)
    )
    on_disk = {f.stem for f in LANG.glob("*.json")}
    assert registered == on_disk


def test_default_language_never_says_you() -> None:
    # House style: the user is thou/thee/thy/thine, never you/your.
    for k, v in flatten(load(DEFAULT)).items():
        for s in v.values() if isinstance(v, dict) else [v]:
            assert not re.search(r"\byou(r|rs|rself)?\b", s, re.I), f"{k}: {s}"


def test_shipped_translations_are_complete() -> None:
    # Missing keys would fall back to English mid-page; the shipped files translate everything.
    base = set(flatten(load(DEFAULT)))
    for f in LANG.glob("*.json"):
        missing = base - set(flatten(json.loads(f.read_text(encoding="utf-8"))))
        assert not missing, f"{f.name} lacks {sorted(missing)}"


def test_language_names_match_files() -> None:
    src = (WEB / "js" / "i18n.js").read_text(encoding="utf-8")
    for code, name in re.findall(r"^\s+(\w+): '([^']+)',", src, re.M):
        assert load(code)["_meta"]["name"] == name, code


def test_css_fonts_exist() -> None:
    css = (WEB / "css" / "app.css").read_text(encoding="utf-8")
    urls = re.findall(r'url\("\.\./(vendor/fonts/[^"]+)"\)', css)
    assert urls, "no vendored fonts referenced"
    for u in urls:
        assert (WEB / u).is_file(), u
    i18n = (WEB / "js" / "i18n.js").read_text(encoding="utf-8")
    assert set(re.findall(r"'(vendor/fonts/[^']+)'", i18n)) == set(urls), "preload list and @font-face differ"
