"""Settings: defaults, environment overrides, and the contract with .env.example."""

import support  # noqa: F401
from retailpilot.config.config import REPO_ROOT, Settings
from support import ROOT


def _env_example_keys() -> list[str]:
    keys = []
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.append(line.split("=", 1)[0].strip())
    return keys


def test_defaults_without_env_file(monkeypatch):
    for key in ("MONGO_URI", "GEMINI_MODEL", "STALE_HOURS", "AMAZON_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)
    assert s.mongo_uri == "mongodb://localhost:27018"
    assert s.gemini_model == "gemini-2.5-flash"
    assert s.stale_hours == 48.0
    assert s.base_url == "https://www.amazon.in"


def test_environment_variables_override_defaults(monkeypatch):
    monkeypatch.setenv("MONGO_URI", "mongodb://example-host:27017")
    monkeypatch.setenv("CRAWL_MAX_PRODUCTS", "7")
    monkeypatch.setenv("DJANGO_DEBUG", "0")
    s = Settings(_env_file=None)
    assert s.mongo_uri == "mongodb://example-host:27017"
    assert s.max_products_per_category == 7
    assert s.django_debug is False


def test_cities_file_is_found_from_the_repo_root():
    s = Settings(_env_file=None)
    assert REPO_ROOT == ROOT
    assert s.cities_file.exists()


def test_every_env_example_key_is_read_by_settings():
    known = set()
    for name, field in Settings.model_fields.items():
        known.add(name.upper())
        if field.validation_alias:
            known.add(str(field.validation_alias).upper())
    unknown = [key for key in _env_example_keys() if key not in known]
    assert unknown == [], f".env.example sets keys that Settings never reads: {unknown}"
