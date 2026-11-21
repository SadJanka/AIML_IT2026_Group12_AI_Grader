import ast
import re
from pathlib import Path

import pytest

from app.i18n import translate
from app.i18n_lv import LV
from app.models import STATUS_LABELS

ROOT = Path(__file__).resolve().parent.parent / "app"
_LITERAL = r"""("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')"""
_TEMPLATE_CALL = re.compile(r"\b_\(\s*" + _LITERAL)
_PY_CALL = re.compile(r"\b(?:N_\(|tr\(request,|translate\(|LLMError\(|LLMBadRequest\()\s*(" + _LITERAL + r"(?:\s+" + _LITERAL + r")*)")
_EXPORT_CALL = re.compile(r"\bt\(\s*" + _LITERAL)


def _literals(pattern, text):
    return {ast.literal_eval("(" + (m[0] if isinstance(m, tuple) else m) + ")") for m in pattern.findall(text)}


def _strings():
    found = set(STATUS_LABELS.values())
    for f in (ROOT / "templates").glob("*.html"):
        found |= _literals(_TEMPLATE_CALL, f.read_text(encoding="utf-8"))
    for f in ROOT.rglob("*.py"):
        if not f.name.startswith("i18n"):
            found |= _literals(_PY_CALL, f.read_text(encoding="utf-8"))
    found |= _literals(_EXPORT_CALL, (ROOT / "services" / "export.py").read_text(encoding="utf-8"))
    return found


def test_every_ui_string_has_latvian_translation():
    missing = sorted(s for s in _strings() if s not in LV)
    assert not missing, "Missing translations in app/i18n_lv.py:\n" + "\n".join(missing)


def test_no_unused_translations():
    unused = sorted(set(LV) - _strings())
    assert not unused, "Unused translations in app/i18n_lv.py:\n" + "\n".join(unused)


def test_placeholders_match():
    for en, lv in LV.items():
        assert set(re.findall(r"{\w+}", en)) == set(re.findall(r"{\w+}", lv)), en


def test_translate_with_params():
    assert translate("Students added: {n}.", "en", n=3) == "Students added: 3."
    assert translate("Students added: {n}.", "lv", n=3) == "Pievienoti skolēni: 3."


@pytest.fixture
def lang_client(client):
    yield client
    client.cookies.clear()


def test_language_switch(lang_client):
    r = lang_client.get("/lang/en?next=/", follow_redirects=False)
    assert r.status_code == 303 and r.cookies.get("lang") == "en"
    page = lang_client.get("/").text
    assert "Courses" in page and 'lang="en"' in page
    lang_client.get("/lang/lv?next=/")
    assert "Kursi" in lang_client.get("/").text


def test_language_switch_blocks_external_redirect(client):
    r = client.get("/lang/en?next=//evil.example", follow_redirects=False)
    assert r.headers["location"] == "/"


def test_all_pages_render_in_english(lang_client):
    lang_client.cookies.set("lang", "en")
    for url in ["/", "/courses/1", "/courses/1/assignments/new", "/assignments/1", "/assignments/1/edit",
                "/submissions/1", "/courses/1/results", "/courses/1/stats"]:
        r = lang_client.get(url)
        assert r.status_code == 200, url
        assert "Kursi" not in r.text, url
