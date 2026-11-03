"""Typed contracts at the agent's boundaries: the alert in, the diagnosis out."""

import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

SUBMIT_DIAGNOSIS = "submit_diagnosis"


class Alert(BaseModel):
    """One firing alert, reduced to what the agent needs from Grafana's webhook payload."""

    name: str
    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    summary: str = ""
    starts_at: str = ""


class Evidence(BaseModel):
    source: Literal["logs", "trace", "metrics"]
    reference: str = Field(description="Trace id, query, or metric name the evidence came from")
    quote: str = Field(max_length=400, description="Short excerpt copied verbatim from a tool result")


class Diagnosis(BaseModel):
    """What the model must submit. Structure is checked here; correctness is checked by evals."""

    root_cause: str = Field(description="One or two sentences: what failed and why")
    category: str = Field(max_length=40, description="Short snake_case label, e.g. schema_mismatch")
    evidence: list[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, description="0 to 1. Use below 0.5 when evidence is thin")
    affected_services: list[str] = Field(min_length=1)
    recommended_action: str = Field(description="Proposed fix as text only. Nothing is executed")


class DiagnosisResult(BaseModel):
    """What run_diagnosis returns: the diagnosis plus the facts about how it was produced."""

    alert: Alert
    # complete: model submitted a valid diagnosis. forced: step cap hit, final answer forced.
    # failed: no valid diagnosis could be obtained.
    status: Literal["complete", "forced", "failed"]
    diagnosis: Diagnosis | None = None
    error: str | None = None
    steps: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0


def submit_diagnosis_tool() -> dict:
    """The tool the model calls to finish. Its input schema is generated from the Diagnosis model."""
    return {
        "name": SUBMIT_DIAGNOSIS,
        "description": (
            "Submit your final diagnosis once you have enough evidence. Call this exactly once, "
            "when done investigating. Every evidence quote must be copied from a tool result."
        ),
        "input_schema": Diagnosis.model_json_schema(),
    }


def parse_diagnosis(payload: dict) -> Diagnosis:
    """Validate a submit_diagnosis payload. Raises pydantic.ValidationError."""
    return Diagnosis.model_validate(payload)


def validation_message(exc: ValidationError) -> str:
    """Compact error text to send back to the model so it can correct its submission."""
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def ungrounded_evidence(diagnosis: Diagnosis, observed: list[str]) -> list[Evidence]:
    """Evidence whose quote does not appear in any tool result the model was shown.

    Catches fabricated citations. Quotes are compared whitespace- and case-insensitively.
    """
    haystack = _norm("\n".join(observed))
    return [e for e in diagnosis.evidence if _norm(e.quote) not in haystack]
