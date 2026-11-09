"""Retrieval quality: does search put the right runbook in the top 3 before the agent is involved?"""

from pathlib import Path

import pytest
import yaml

from knowledge_store.ingest import ingest
from tests.conftest import needs_postgres

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"
pytestmark = needs_postgres

# (query, expected ids that must appear in the top 3, optional service filter)
FIXTURE_GOLDEN = [
    ("orders rejected price mismatch", ["RB-001"], None),
    ("queue depth climbing consumer slow", ["RB-002"], None),
    ("502 after quote broker connection lost", ["RB-003"], None),
    ("pricing returns 503 outage", ["RB-004"], None),
    ("keyerror missing sku field json decode error", ["RB-005"], None),
    ("certificate expired tls handshake failure", ["RB-006"], None),
    ("memory alarm publishers blocked", ["RB-007"], None),
    ("latency p95 slow but no errors", ["RB-008"], None),
    ("queue backlog", ["RB-002"], "rabbitmq"),
]


async def recall_failures(store, cases) -> list[str]:
    failures = []
    for query, expected, service in cases:
        top3 = [h.id for h in await store.search_runbooks(query, service, limit=3)]
        missing = [e for e in expected if e not in top3]
        if missing:
            failures.append(f"{query!r} expected {missing} in top 3, got {top3}")
    return failures


async def test_fixture_runbooks_are_found_by_their_golden_queries(store):
    await ingest(FIXTURES, Path("/nonexistent"), store, with_contracts=False)
    assert await recall_failures(store, FIXTURE_GOLDEN) == []


async def test_real_knowledge_meets_its_golden_queries(store):
    raw = yaml.safe_load((ROOT / "knowledge" / "golden_queries.yaml").read_text()) or {}
    cases = [(q["query"], q["expect"], q.get("service")) for q in raw.get("queries") or []]
    if not cases:
        pytest.skip("knowledge/golden_queries.yaml has no queries yet")
    result = await ingest(ROOT / "knowledge", Path("/nonexistent"), store, with_contracts=False)
    assert not result.errors, [str(i) for i in result.errors]
    assert await recall_failures(store, cases) == []
