"""Knowledge lint: the build fails on errors so bad knowledge never reaches the agent.

Rules protect three things: structure (ids, owners, review dates), freshness (stale content gives
confident wrong answers), and eval honesty (no entry may name a failure ID or a fault switch).
"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from knowledge_store.models import KnowledgeDoc

STALE_WARN_DAYS = 90
STALE_ERROR_DAYS = 180
MAX_RUNBOOK_WORDS = 500
ID_PATTERNS = {
    "runbook": re.compile(r"^RB-\d{3}$"),
    "service": re.compile(r"^svc:[a-z0-9][a-z0-9-]*$"),
    "contract": re.compile(r"^contract:[a-z0-9][a-z0-9-]*$"),
}
REQUIRED_SECTIONS = ["## Symptoms", "## Likely causes", "## How to confirm", "## Fix options", "## Escalate when"]
FAILURE_ID = re.compile(r"\bF\d{2}\b")


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    doc_id: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity.upper():7} {self.doc_id}: {self.message}"


def banned_terms(catalog_path: Path) -> list[str]:
    """Terms no knowledge entry may contain: injected fault switch names and the word 'inject'."""
    raw = yaml.safe_load(catalog_path.read_text()) if catalog_path.is_file() else {"failures": []}
    names = {f["fault"]["name"].lower() for f in raw.get("failures", [])}
    return sorted(names | {"inject"})


def lint(
    docs: list[KnowledgeDoc],
    banned: list[str],
    today: date | None = None,
    known_services: set[str] | None = None,
) -> list[Issue]:
    today = today or date.today()
    issues: list[Issue] = []
    if known_services is None:
        known_services = {d.id.removeprefix("svc:") for d in docs if d.kind == "service"}

    seen: set[str] = set()
    for d in docs:
        err = lambda msg, d=d: issues.append(Issue("error", d.id, msg))  # noqa: E731
        warn = lambda msg, d=d: issues.append(Issue("warning", d.id, msg))  # noqa: E731

        if d.id in seen:
            err("duplicate id")
        seen.add(d.id)
        if not ID_PATTERNS[d.kind].match(d.id):
            err(f"id does not match the {d.kind} pattern")

        if d.kind != "contract":
            if not d.owner.strip():
                err("missing owner")
            if d.last_reviewed is None:
                err("missing last_reviewed")
            else:
                age = (today - d.last_reviewed).days
                if age < 0:
                    err("last_reviewed is in the future")
                elif age > STALE_ERROR_DAYS:
                    err(f"stale: last reviewed {age} days ago (limit {STALE_ERROR_DAYS})")
                elif age > STALE_WARN_DAYS:
                    warn(f"last reviewed {age} days ago")
        if d.status == "draft":
            warn("status is draft")

        if d.kind == "runbook":
            words = len(d.body.split())
            if words > MAX_RUNBOOK_WORDS:
                warn(f"{words} words (aim for under {MAX_RUNBOOK_WORDS})")
            for section in REQUIRED_SECTIONS:
                if section not in d.body:
                    warn(f"missing section '{section}'")
            if known_services:
                for svc in d.applies_to:
                    if svc not in known_services:
                        err(f"applies_to names unknown service '{svc}' (add a catalog entry or fix the name)")

        text = f"{d.title}\n{d.body}\n{' '.join(d.tags)}"
        lowered = text.lower()
        if FAILURE_ID.search(text):
            err("names a failure catalog id (would leak eval answers)")
        for term in banned:
            if term in lowered:
                err(f"contains banned term '{term}' (would leak eval answers)")
    return issues
