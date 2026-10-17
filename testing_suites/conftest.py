"""Fixtures shared by every suite. support is imported first because it sets up paths and environment."""

import asyncio

import pytest
from pymongo import MongoClient

import support  # noqa: F401  (must load before any RetailPilot module)
import sample_data
from retailpilot.config.config import settings
from support import MONGO_AVAILABLE, TEST_CHAT_DB, TEST_DB


@pytest.fixture(scope="session")
def mongo_db():
    """The test database, freshly seeded with sample_data. Skips when MongoDB is down."""
    if not MONGO_AVAILABLE:
        pytest.skip("MongoDB is not reachable")
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
    client.drop_database(TEST_DB)
    client.drop_database(TEST_CHAT_DB)
    sample_data.seed_mongo(client[TEST_DB])
    yield client[TEST_DB]
    client.drop_database(TEST_DB)
    client.drop_database(TEST_CHAT_DB)
    client.close()


@pytest.fixture(scope="session")
def run():
    """Runs a coroutine on one event loop for the whole session.

    The async Mongo clients in mcp_connection and history bind to the first loop they use,
    so every async call in the suites goes through this loop.
    """
    loop = asyncio.new_event_loop()
    yield lambda coro: loop.run_until_complete(coro)
    loop.close()
