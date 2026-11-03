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
}


def test_valid_payload_parses():
    assert parse_diagnosis(VALID).confidence == 0.8


@pytest.mark.parametrize(
    "change",
    [{"confidence": 1.7}, {"evidence": []}, {"affected_services": []}, {"category": "x" * 41}, {"evidence": [{"source": "vibes", "reference": "r", "quote": "q"}]}],
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
