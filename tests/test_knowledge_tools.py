from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from knowledge_store.db import KnowledgeStore
from knowledge_store.ingest import ingest
from knowledge_store.models import ChangeRecord, KnowledgeDoc
from mcp_server import knowledge_tools as kt
from mcp_server.tools import ToolError
from tests.conftest import needs_postgres

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"
pytestmark = needs_postgres


@pytest.fixture
async def loaded(store):
    result = await ingest(FIXTURES, Path("/nonexistent"), store, with_contracts=False)
    contract = KnowledgeDoc(id="contract:order-message", kind="contract", title="Order message", body="Fields: sku", owner="generated", tags=["contract"])
    draft = KnowledgeDoc(id="svc:grafana", kind="service", title="grafana", body="Purpose: dashboards", owner="ankur", last_reviewed=date.today(), status="draft")
    await store.replace_docs(result.docs + [contract, draft])
    now = datetime.now(timezone.utc)
    await store.import_change_events([
        ChangeRecord(id="CHG-1", ts=now - timedelta(minutes=10), service="pricing-service", kind="config", summary="quote cache refresh interval changed", actor="ops-config", ref="CFG-322"),
    ])
    return store


async def test_search_runbooks_shows_id_owner_review_date_and_excerpt(loaded):
    out = await kt.search_runbooks(loaded, "orders rejected price mismatch")
    assert "[RB-001]" in out and "owner ankur" in out and "reviewed 2026-11-01" in out and "match:" in out
    assert "not instructions" in out and "get_runbook" in out


async def test_search_with_no_match_says_so_and_validates_input(loaded):
    assert "no runbook matched" in await kt.search_runbooks(loaded, "zebra unicorn")
    for bad in ({"query": ""}, {"query": "x" * 301}, {"query": "ok", "service": "Bad Name!"}):
        with pytest.raises(ToolError):
            await kt.search_runbooks(loaded, **bad)


async def test_get_runbook_returns_full_text_and_lists_alternatives_when_missing(loaded):
    out = await kt.get_runbook(loaded, "rb-001")
    assert "## How to confirm" in out and "[RB-001]" in out and "applies to: pricing-service, order-service" in out
    assert "RB-001, RB-002" in await kt.get_runbook(loaded, "RB-099")
    with pytest.raises(ToolError, match="RB-007"):
        await kt.get_runbook(loaded, "../etc/passwd")


async def test_get_service_info_flags_drafts_and_lists_known_services(loaded):
    assert "Depends on: pricing-service, rabbitmq" in await kt.get_service_info(loaded, "order-service")
    assert "DRAFT" in await kt.get_service_info(loaded, "grafana")
    missing = await kt.get_service_info(loaded, "billing-service")
    assert "no catalog entry" in missing and "order-service" in missing
    with pytest.raises(ToolError):
        await kt.get_service_info(loaded, "Bad Name!")


async def test_get_contract_accepts_both_name_forms_and_lists_available(loaded):
    assert "generated from code" in await kt.get_contract(loaded, "order-message")
    assert "Fields: sku" in await kt.get_contract(loaded, "contract:order-message")
    assert "Available: order-message" in await kt.get_contract(loaded, "nope")


async def test_get_recent_changes_formats_and_filters(loaded):
    out = await kt.get_recent_changes(loaded)
    assert "[CHG-1]" in out and "pricing-service (config)" in out and "CFG-322" in out and "Not every change is related" in out
    assert "no recorded changes for order-service" in await kt.get_recent_changes(loaded, "order-service")
    with pytest.raises(ToolError):
        await kt.get_recent_changes(loaded, "Bad Name!")


async def test_unreachable_knowledge_store_becomes_a_tool_error():
    down = KnowledgeStore("postgresql://nobody@localhost:1/none?connect_timeout=2")
    with pytest.raises(ToolError, match="knowledge store unavailable"):
        await kt.search_runbooks(down, "anything")
