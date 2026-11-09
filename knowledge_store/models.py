"""Typed records for the knowledge layer."""

import hashlib
import json
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

DocKind = Literal["runbook", "service", "contract"]
DocStatus = Literal["active", "draft", "retired"]


class KnowledgeDoc(BaseModel):
    id: str  # RB-007 | svc:pricing-service | contract:order-message
    kind: DocKind
    title: str
    body: str
    owner: str = ""
    last_reviewed: date | None = None  # None for generated contracts
    applies_to: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: DocStatus = "active"
    source_path: str = ""

    @property
    def content_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"source_path"})
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class ChangeRecord(BaseModel):
    """One line of the change feed (see simulator/failure_injector/change_feed.py)."""

    id: str
    ts: datetime
    service: str
    kind: str
    summary: str
    actor: str = ""
    ref: str | None = None


class SearchHit(BaseModel):
    id: str
    title: str
    owner: str
    last_reviewed: date | None
    status: str
    score: float
    snippet: str


def knowledge_version(docs: list[KnowledgeDoc]) -> str:
    """Stable fingerprint of all ingested content: recorded on traces and audit entries."""
    digest = hashlib.sha256()
    for d in sorted(docs, key=lambda d: d.id):
        digest.update(f"{d.id}:{d.content_hash}\n".encode())
    return digest.hexdigest()[:12]
