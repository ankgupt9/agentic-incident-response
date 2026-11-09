from datetime import date
from pathlib import Path

import pytest

from knowledge_store.loader import LoadError, generate_contracts, load_authored, parse_runbook, render_service
from knowledge_store.models import KnowledgeDoc, knowledge_version
from simulator.common.contracts import OrderMessage
from simulator.pricing_service.price_list import price_order

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"


def test_fixture_knowledge_loads_runbooks_and_services():
    docs = load_authored(FIXTURES)
    assert sorted(d.id for d in docs if d.kind == "runbook") == [f"RB-00{i}" for i in range(1, 9)]
    assert {d.id for d in docs if d.kind == "service"} == {"svc:order-service", "svc:pricing-service", "svc:rabbitmq"}


def test_runbook_fields_and_numeric_tags(tmp_path):
    path = tmp_path / "rb.md"
    path.write_text("---\nid: RB-010\ntitle: T\nowner: o\nlast_reviewed: 2026-11-02\napplies_to: [a]\ntags: [502, 503, word]\n---\nbody text\n")
    doc = parse_runbook(path)
    assert doc.tags == ["502", "503", "word"] and doc.last_reviewed == date(2026, 11, 2) and doc.body == "body text"


@pytest.mark.parametrize("text,match", [("no frontmatter", "missing frontmatter"), ("---\nid: RB-1\n---\nb", "missing frontmatter field"), ("---\nid: RB-1\ntitle: t\nlast_reviewed: nope\n---\nb", "invalid date")])
def test_bad_runbooks_are_reported_with_the_file_name(tmp_path, text, match):
    path = tmp_path / "bad.md"
    path.write_text(text)
    with pytest.raises(LoadError, match=match):
        parse_runbook(path)


def test_templates_are_skipped(tmp_path):
    (tmp_path / "runbooks").mkdir()
    (tmp_path / "runbooks" / "_TEMPLATE.md").write_text("not a runbook")
    assert load_authored(tmp_path) == []


def test_render_service_is_readable_text():
    text = render_service({"purpose": "Does x.", "depends_on": ["a", "b"], "interfaces": {"http": ["GET /x"]}, "configuration": {"timeout": "5 seconds"}, "known_quirks": ["Lags."], "escalation": {"ask": "ankur"}})
    assert "Depends on: a, b" in text and "Interfaces (http): GET /x" in text and "Known quirk: Lags." in text


def test_real_service_catalog_drafts_load():
    docs = load_authored(Path(__file__).resolve().parent.parent / "knowledge")
    assert {"svc:order-service", "svc:pricing-service", "svc:rabbitmq"} <= {d.id for d in docs}


def test_generated_contracts_come_from_the_code_and_hide_fault_admin_routes():
    docs = {d.id: d for d in generate_contracts()}
    assert set(docs) == {"contract:order-message", "contract:order-service-api", "contract:pricing-service-api"}
    assert "POST /orders" in docs["contract:order-service-api"].body
    assert "GET /quote/{sku}" in docs["contract:pricing-service-api"].body
    assert all("admin" not in d.body and "fault" not in d.body.lower() for d in docs.values())
    assert "quoted_price: number, required" in docs["contract:order-message"].body


def test_documented_order_message_matches_what_the_consumer_accepts():
    msg = OrderMessage(order_id="o1", sku="SKU-1001", quantity=2, customer_id="c1", quoted_price=19.99)
    assert price_order(msg.model_dump()) == 39.98


def test_knowledge_version_is_stable_and_changes_with_content():
    a = KnowledgeDoc(id="RB-001", kind="runbook", title="t", body="b", owner="o", last_reviewed=date(2026, 11, 1))
    changed = a.model_copy(update={"body": "different"})
    assert knowledge_version([a]) == knowledge_version([a.model_copy()]) and knowledge_version([a]) != knowledge_version([changed])
    other = a.model_copy(update={"id": "RB-002"})
    assert knowledge_version([a, other]) == knowledge_version([other, a])  # order does not matter
