"""Integration tests for the chat history store (threads, messages, shown products).

Runs against the test chat database. Skipped automatically when MongoDB is not reachable.
"""

import pytest

import support  # noqa: F401
from retailpilot.agent.history import ChatHistory
from retailpilot.config.config import settings
from support import TEST_CHAT_DB, requires_mongo

pytestmark = [requires_mongo, pytest.mark.integration]


@pytest.fixture
def history(mongo_db, run):
    store = ChatHistory(settings.mongo_uri, db_name=TEST_CHAT_DB)
    run(store.db.threads.delete_many({}))
    run(store.db.messages.delete_many({}))
    run(store.db.shown_products.delete_many({}))
    run(store.ensure_indexes())
    yield store
    run(store.close())


def test_thread_exists_only_after_creation(history, run):
    assert run(history.thread_exists("t1")) is False
    run(history.create_thread("t1", "Laptop question", "amazon"))
    assert run(history.thread_exists("t1")) is True


def test_pending_question_round_trip(history, run):
    run(history.create_thread("t1", "Title", "amazon"))
    run(history.set_pending("t1", "Which city?"))
    assert run(history.get_pending("t1")) == "Which city?"
    run(history.set_pending("t1", None))
    assert run(history.get_pending("t1")) is None


def test_messages_are_counted_and_kept_in_order(history, run):
    run(history.create_thread("t1", "Title", "amazon"))
    run(history.add_message("t1", "user", "hi", kind="message", role="customer"))
    run(history.add_message("t1", "assistant", "hello", kind="answer", role="customer"))
    threads = run(history.list_threads())
    thread = next(t for t in threads if t["thread_id"] == "t1")
    assert thread["message_count"] == 2
    assert thread["last_message"] == "hello"
    assert [m["content"] for m in run(history.messages("t1"))] == ["hi", "hello"]


def test_threads_are_listed_newest_first(history, run):
    run(history.create_thread("older", "A", "amazon"))
    run(history.add_message("older", "user", "x", kind="message", role=None))
    run(history.create_thread("newer", "B", "amazon"))
    run(history.add_message("newer", "user", "y", kind="message", role=None))
    assert [t["thread_id"] for t in run(history.list_threads())][:2] == ["newer", "older"]


def test_shown_products_and_interest_are_scoped_to_a_session(history, run):
    run(history.record_shown("s1", [{"asin": "B0TESTA001", "title": "Laptop", "price": 50000, "city": "delhi",
                                     "category": "Laptops", "url": None}]))
    assert run(history.shown_asins("s1")) == ["B0TESTA001"]
    assert run(history.shown_asins("someone-else")) == []
    assert run(history.set_interest("s1", "B0TESTA001", "buy")) is True
    assert run(history.shown("s1"))[0]["interest"] == "buy"
    assert run(history.set_interest("s1", "B0NOTSHOWN0", "buy")) is False
