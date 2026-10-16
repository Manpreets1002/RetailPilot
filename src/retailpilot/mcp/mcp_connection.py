import json

from pymongo import AsyncMongoClient

from retailpilot.config.config import settings

AMAZON_BASE_URL = settings.base_url

db = AsyncMongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)[settings.mongo_db]


def load_cities() -> list[dict]:
    return json.loads(settings.cities_file.read_text())["cities"]
