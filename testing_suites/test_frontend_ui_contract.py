"""Checks that the templates, stylesheet and scripts agree with each other.

A renamed element ID, a missing CSS class or a broken static path breaks the UI silently, so these
tests catch it before a user does.
"""

import re

import pytest
from django.template.loader import get_template

import support
from support import FRONTEND

WEB = FRONTEND / "web"
TEMPLATES = WEB / "templates" / "web"
STATIC = WEB / "static" / "web"
TEMPLATE_NAMES = ["base.html", "_assistant.html", "landing.html", "customer.html", "retailer.html"]
SCRIPT_NAMES = ["assistant.js", "customer.js", "retailer.js"]
ID_CALL = re.compile(r'(?:\bel|getElementById)\(\s*"([A-Za-z0-9_-]+)"\s*\)')


def _read(path):
    return path.read_text(encoding="utf-8")


def _template_text():
    return "\n".join(_read(TEMPLATES / name) for name in TEMPLATE_NAMES)


def _template_ids() -> set[str]:
    """IDs written as id="..." plus those created by json_script (which also sets an element id)."""
    text = _template_text()
    return set(re.findall(r'id="([^"]+)"', text)) | set(re.findall(r'json_script:"([^"]+)"', text))


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_template_compiles(name):
    assert get_template(f"web/{name}") is not None


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_static_files_referenced_by_templates_exist(name):
    missing = [
        ref for ref in re.findall(r"\{%\s*static\s+'([^']+)'\s*%\}", _read(TEMPLATES / name))
        if not (WEB / "static" / ref).exists()
    ]
    assert missing == [], f"{name} references missing static files: {missing}"


@pytest.mark.parametrize("script", SCRIPT_NAMES)
def test_every_element_a_script_looks_up_exists_in_the_templates(script):
    wanted = set(ID_CALL.findall(_read(STATIC / "js" / script)))
    missing = sorted(wanted - _template_ids())
    assert missing == [], f"{script} looks up IDs that no template defines: {missing}"


def test_every_css_class_used_in_templates_and_scripts_is_defined():
    css = _read(STATIC / "css" / "site.css")
    sources = [_read(TEMPLATES / name) for name in TEMPLATE_NAMES]
    sources += [_read(STATIC / "js" / name) for name in SCRIPT_NAMES]
    tokens = set()
    for text in sources:
        for value in re.findall(r'class="([^"]+)"', text):
            if "{" in value or "$" in value:
                continue  # template or JS expressions decide the class at runtime
            tokens.update(value.split())
    undefined = sorted(t for t in tokens if f".{t}" not in css)
    assert undefined == [], f"Classes used but not styled in site.css: {undefined}"


def test_the_assistant_widget_is_wired_to_its_script():
    assistant = _read(STATIC / "js" / "assistant.js")
    for hook in ("assistant-config", "rp-fab", "rp-panel", "rp-form", "rp-input", "rp-messages"):
        assert hook in assistant
    assert 'json_script:"assistant-config"' in _read(TEMPLATES / "_assistant.html")
