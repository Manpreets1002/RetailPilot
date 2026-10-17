"""Paths, environment and shared helpers for the test suites.

Import this module before any RetailPilot module. It puts the source folders on the import path,
points MongoDB at a throwaway test database, and configures Django.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
MCP_DIR = SRC / "retailpilot" / "mcp"
FRONTEND = SRC / "retailpilot" / "frontend"
CRAWLER = ROOT / "amazon_ecomm_crawler"

# MongoDB tests write only to these two databases and drop them afterwards. Real crawl data is never touched.
TEST_DB = "retailpilot_test_suite"
TEST_CHAT_DB = "retailpilot_test_suite_chat"
os.environ["MONGO_DB"] = TEST_DB
os.environ["CHAT_DB"] = TEST_CHAT_DB

# FRONTEND goes first so Django's `config` package wins over the crawler's config.py.
for folder in (CRAWLER, MCP_DIR, SRC, FRONTEND):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

django.setup()
setup_test_environment()

from pymongo import MongoClient  # noqa: E402

from retailpilot.config.config import settings  # noqa: E402


def _mongo_reachable() -> bool:
    try:
        client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=1000)
        client.admin.command("ping")
        client.close()
        return True
    except Exception:
        return False


MONGO_AVAILABLE = _mongo_reachable()
requires_mongo = pytest.mark.skipif(
    not MONGO_AVAILABLE,
    reason=f"MongoDB is not reachable at {settings.mongo_uri} (start it with: docker compose up -d mongo)",
)
