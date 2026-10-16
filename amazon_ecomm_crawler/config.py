"""Crawler configuration. All settings come from retailpilot.config.config; the cities list is in cities.json."""

import json
from pathlib import Path

from retailpilot.config.config import Settings, settings

__all__ = ["Settings", "settings", "load_cities", "select_cities"]


def load_cities(path: Path | None = None) -> list[dict]:
    """Return the cities array from the config file: [{slug, name, state, pincode}, ...]."""
    path = Path(path) if path else settings.cities_file
    cities = json.loads(path.read_text())["cities"]
    for c in cities:
        missing = {"slug", "name", "pincode"} - c.keys()
        if missing:
            raise ValueError(f"City entry {c} in {path} is missing {sorted(missing)}")
        c["pincode"] = str(c["pincode"])
    return cities


def select_cities(wanted: list[str] | None, cities: list[dict]) -> list[dict]:
    """Pick cities by slug, name or pincode (case-insensitive). None/empty or 'all' means every city."""
    if not wanted or [w.lower() for w in wanted] == ["all"]:
        return cities
    picked = []
    for w in wanted:
        key = w.strip().lower()
        match = next(
            (c for c in cities if key in (c["slug"].lower(), c["name"].lower(), c["pincode"])), None
        )
        if match is None:
            known = ", ".join(c["slug"] for c in cities)
            raise SystemExit(f"Unknown city '{w}'. Known cities: {known}")
        if match not in picked:
            picked.append(match)
    return picked
