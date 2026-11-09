import pytest
from pydantic import ValidationError

from agent.schemas import Diagnosis, parse_diagnosis, submit_diagnosis_tool, ungrounded_evidence, validation_message

VALID = {
    "root_cause": "pricing-service rejects orders whose quoted price differs from the catalog.",
    "category": "data_inconsistency",
    "evidence": [{"source": "logs", "reference": "pricing-service", "quote": "price mismatch for SKU-1001"}],
    "confidence": 0.8,
    "affected_services": ["pricing-service"],
    "recommended_action": "Refresh the quote cache.",
    "knowledge_refs": [],
}


def test_valid_payload_parses():
    assert parse_diagnosis(VALID).confidence == 0.8


@pytest.mark.parametrize(
    "change",
    [{"confidence": 1.7}, {"evidence": []}, {"affected_services": []}, {"category": "x" * 41}, {"category": "x"}, {"category": "Price Mismatch"}, {"evidence": [{"source": "vibes", "reference": "r", "quote": "q"}]}],
)
def test_invalid_payloads_are_rejected(change):
    with pytest.raises(ValidationError):
        parse_diagnosis({**VALID, **change})


def test_validation_message_is_compact_and_names_the_field():
    with pytest.raises(ValidationError) as exc:
        parse_diagnosis({**VALID, "confidence": 1.7})
    assert "confidence" in validation_message(exc.value)


def test_submit_tool_schema_comes_from_the_model():
    tool = submit_diagnosis_tool()
    assert tool["name"] == "submit_diagnosis"
    assert set(tool["input_schema"]["required"]) >= {"root_cause", "evidence", "confidence"}


def test_ungrounded_evidence_flags_quotes_not_in_tool_results():
    d = Diagnosis.model_validate({**VALID, "evidence": [
        {"source": "logs", "reference": "a", "quote": "Price  Mismatch for SKU-1001"},
        {"source": "logs", "reference": "b", "quote": "database connection pool exhausted"},
    ]})
    bad = ungrounded_evidence(d, ["... failed to price order: price mismatch for sku-1001 ..."])
    assert [e.reference for e in bad] == ["b"]


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_submit_tool_is_strict_with_a_schema_the_api_accepts():
    tool = submit_diagnosis_tool()
    assert tool["strict"] is True
    nodes = list(_walk(tool["input_schema"]))
    # strict mode rejects these keywords; Pydantic still enforces them on our side
    assert not any(k in n for n in nodes for k in ("minimum", "maximum", "minLength", "maxLength", "exclusiveMinimum", "pattern"))
    assert all(n.get("additionalProperties") is False for n in nodes if n.get("type") == "object")
    assert "category" in tool["input_schema"]["required"] and "evidence" in tool["input_schema"]["required"]


def test_constraints_dropped_from_the_strict_schema_are_still_enforced_by_validation():
    with pytest.raises(ValidationError):
        parse_diagnosis({**VALID, "confidence": 1.7})
    with pytest.raises(ValidationError):
        parse_diagnosis({**VALID, "category": "x" * 41})
    assert "0" in str(submit_diagnosis_tool()["input_schema"]["properties"]["confidence"]["description"])  # hint kept for the model
