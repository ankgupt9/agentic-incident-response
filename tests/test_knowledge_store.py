import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from knowledge_store.db import KnowledgeStore, build_tsquery
from knowledge_store.ingest import ingest
from knowledge_store.models import ChangeRecord
from tests.conftest import needs_postgres

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"
pytestmark = needs_postgres


def test_build_tsquery_ors_safe_words():
    assert build_tsquery("Orders rejected, price-mismatch!") == "orders | rejected | price | mismatch"
    assert build_tsquery("a ' ; DROP TABLE x") == "drop | table"  # single letters and punctuation never reach the query
    assert build_tsquery("   ") == ""


async def ingest_fixtures(store, **kw):
    return await ingest(FIXTURES, Path("/nonexistent/feed.jsonl"), store, with_contracts=False, **kw)


async def test_ingest_loads_docs_and_records_a_stable_version(store):
    first = await ingest_fixtures(store)
    assert first.version and len(first.docs) == 11 and not first.errors
    second = await ingest_fixtures(store)
    assert second.version == first.version and await store.current_version() == first.version


async def test_replace_removes_documents_that_are_no_longer_in_the_source(store):
    result = await ingest_fixtures(store)
    kept = [d for d in result.docs if d.id != "RB-006"]
    await store.replace_docs(kept)
    assert await store.get_doc("RB-006") is None and await store.get_doc("RB-001") is not None


async def test_search_ranks_the_right_runbook_first_and_filters_by_service(store):
    await ingest_fixtures(store)
    hits = await store.search_runbooks("orders rejected with a price mismatch")
    assert hits[0].id == "RB-001" and hits[0].score > 0 and "**" in hits[0].snippet
    only_broker = await store.search_runbooks("queue backlog", service="rabbitmq")
    assert only_broker and all(h.id in {"RB-002", "RB-003", "RB-006", "RB-007"} for h in only_broker)
    assert await store.search_runbooks("zebra unicorn") == []
    assert await store.search_runbooks("   ") == []


async def test_retired_runbooks_are_not_searchable(store):
    result = await ingest_fixtures(store)
    docs = [d.model_copy(update={"status": "retired"}) if d.id == "RB-001" else d for d in result.docs]
    await store.replace_docs(docs)
    assert "RB-001" not in [h.id for h in await store.search_runbooks("price mismatch")]


async def test_change_events_import_is_idempotent_and_filterable(store):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    records = [
        ChangeRecord(id="CHG-1", ts=now - timedelta(minutes=5), service="pricing-service", kind="config", summary="cache interval changed", actor="a", ref="CFG-1"),
        ChangeRecord(id="CHG-2", ts=now - timedelta(hours=3), service="order-service", kind="deploy", summary="v3 rolled out", actor="b"),
    ]
    assert await store.import_change_events(records) == 2
    await store.import_change_events(records)
    recent = await store.recent_changes(minutes=60, now=now)
    assert [c.id for c in recent] == ["CHG-1"]
    everything = await store.recent_changes(minutes=1440, now=now)
    assert [c.id for c in everything] == ["CHG-1", "CHG-2"]  # newest first
    assert [c.id for c in await store.recent_changes("order-service", 1440, now=now)] == ["CHG-2"]


async def test_lint_errors_block_ingestion_and_leave_the_store_untouched(store, tmp_path):
    (tmp_path / "runbooks").mkdir()
    (tmp_path / "runbooks" / "rb.md").write_text("---\nid: RB-001\ntitle: t\nowner: o\nlast_reviewed: 2026-11-01\napplies_to: [x]\n---\n## Symptoms\nSee F03\n")
    result = await ingest(tmp_path, Path("/nonexistent"), store, with_contracts=False)
    assert result.errors and result.version is None
    assert await store.current_version() is None


async def test_unreachable_database_raises_instead_of_hanging():
    import psycopg

    bad = KnowledgeStore("postgresql://nobody@localhost:1/none?connect_timeout=2")
    with pytest.raises(psycopg.Error):
        await bad.current_version()
