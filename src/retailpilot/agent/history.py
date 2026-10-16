from datetime import UTC, datetime

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient

from retailpilot.config.config import settings


def _now() -> datetime:
    return datetime.now(UTC)


class ChatHistory:
    """Threads and messages in the chatbot database.

    threads: one document per conversation (title, role, pending question, timestamps).
    messages: one document per user or assistant message, in order.
    """

    def __init__(self, uri: str, db_name: str | None = None):
        db_name = db_name or settings.chat_db
        self.client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.client[db_name]

    async def ensure_indexes(self) -> None:
        await self.db.threads.create_index("thread_id", unique=True)
        await self.db.threads.create_index([("updated_at", DESCENDING)])
        await self.db.messages.create_index([("thread_id", ASCENDING), ("created_at", ASCENDING)])
        await self.db.shown_products.create_index([("session_id", ASCENDING), ("asin", ASCENDING)])

    async def close(self) -> None:
        await self.client.close()

    async def thread_exists(self, thread_id: str) -> bool:
        return await self.db.threads.find_one({"thread_id": thread_id}, {"_id": 1}) is not None

    async def create_thread(self, thread_id: str, title: str, channel: str) -> None:
        now = _now()
        await self.db.threads.insert_one({
            "thread_id": thread_id,
            "title": title,
            "channel": channel,
            "role": None,
            "pending_question": None,
            "message_count": 0,
            "last_message": None,
            "created_at": now,
            "updated_at": now,
        })

    async def get_pending(self, thread_id: str) -> str | None:
        doc = await self.db.threads.find_one({"thread_id": thread_id}, {"_id": 0, "pending_question": 1})
        return doc.get("pending_question") if doc else None

    async def set_pending(self, thread_id: str, question: str | None) -> None:
        await self.db.threads.update_one({"thread_id": thread_id}, {"$set": {"pending_question": question, "updated_at": _now()}})

    async def add_message(self, thread_id: str, author: str, content: str, kind: str, role: str | None) -> None:
        now = _now()
        await self.db.messages.insert_one({
            "thread_id": thread_id,
            "author": author,
            "kind": kind,
            "role": role,
            "content": content,
            "created_at": now,
        })
        await self.db.threads.update_one(
            {"thread_id": thread_id},
            {"$set": {"last_message": content[:200], "updated_at": now, "role": role or None},
             "$inc": {"message_count": 1}},
        )

    async def list_threads(self, limit: int = 50) -> list[dict]:
        cursor = self.db.threads.find({}, {"_id": 0, "pending_question": 0}).sort("updated_at", DESCENDING).limit(limit)
        return [_clean(doc) async for doc in cursor]

    async def messages(self, thread_id: str, limit: int = 200) -> list[dict]:
        cursor = self.db.messages.find({"thread_id": thread_id}, {"_id": 0}).sort("created_at", ASCENDING).limit(limit)
        return [_clean(doc) async for doc in cursor]

    async def record_shown(self, session_id: str, items: list[dict]) -> None:
        now = _now()
        if not items:
            return
        await self.db.shown_products.insert_many([
            {"session_id": session_id, "asin": i["asin"], "title": i.get("title"), "price": i.get("price"),
             "url": i.get("url"), "city": i.get("city"), "category": i.get("category"),
             "interest": None, "shown_at": now, "interest_at": None}
            for i in items
        ])

    async def shown_asins(self, session_id: str) -> list[str]:
        return await self.db.shown_products.distinct("asin", {"session_id": session_id})

    async def shown(self, session_id: str, limit: int = 100) -> list[dict]:
        cursor = self.db.shown_products.find({"session_id": session_id}, {"_id": 0}).sort("shown_at", DESCENDING).limit(limit)
        return [_clean(doc) async for doc in cursor]

    async def set_interest(self, session_id: str, asin: str, interest: str | None) -> bool:
        result = await self.db.shown_products.update_many(
            {"session_id": session_id, "asin": asin},
            {"$set": {"interest": interest, "interest_at": _now()}},
        )
        return result.matched_count > 0


def _clean(doc: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in doc.items()}
