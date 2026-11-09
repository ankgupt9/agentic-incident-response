"""Change feed: the records a release pipeline or change-management system would keep.

When the injector switches a fault on, it also writes the change that plausibly caused it, worded
like a real change record (a deploy, a config change, a schema bump) and never naming the fault's
internal switch. Unrelated "distractor" changes are written alongside, so an investigator has to
pick the relevant change out of noise. Some failures have no causal change at all (broker or
consumer problems), which is realistic and lets the ablation test the no-change case.

The feed is a JSON Lines file. The knowledge layer ingests it into the change_events table.
"""

import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "change_events.jsonl"
DISTRACTOR_COUNT = 3
DISTRACTOR_WINDOW_MIN = 120
CAUSAL_LEAD_S = 60  # the causal change is recorded this long before the failure starts


class ChangeSpec(BaseModel):
    """A change as written in failures/catalog.yaml."""

    service: str
    kind: str  # deploy | config | data | schema | infra
    summary: str
    actor: str = "release-pipeline"
    ref: str | None = None


class ChangeEvent(ChangeSpec):
    id: str
    ts: str  # ISO 8601, UTC


# Plausible, unrelated changes. None may describe any failure in the catalog.
DISTRACTORS = [
    ChangeSpec(service="grafana", kind="config", summary="dashboard 'Order Flow' panel layout updated", actor="platform-team", ref="CFG-301"),
    ChangeSpec(service="rabbitmq", kind="config", summary="management UI session timeout raised from 30m to 60m", actor="platform-team", ref="CFG-305"),
    ChangeSpec(service="order-service", kind="config", summary="request log sampling set to 50% for health checks", actor="platform-team", ref="CFG-309"),
    ChangeSpec(service="pricing-service", kind="data", summary="nightly catalog sync completed, 0 items changed", actor="catalog-sync", ref="SYNC-8812"),
    ChangeSpec(service="loki", kind="config", summary="log retention reviewed, no change (7 days)", actor="platform-team", ref="CFG-311"),
    ChangeSpec(service="prometheus", kind="config", summary="scrape interval for rabbitmq confirmed at 5s", actor="platform-team", ref="CFG-312"),
    ChangeSpec(service="order-service", kind="deploy", summary="documentation-only release, no code change", actor="release-pipeline", ref="REL-3.1.9"),
    ChangeSpec(service="tempo", kind="config", summary="trace retention reviewed, no change (48 hours)", actor="platform-team", ref="CFG-315"),
]


class ChangeFeed:
    def __init__(self, path: Path | None = None):
        self.path = path or Path(os.getenv("CHANGE_FEED_PATH", DEFAULT_PATH))

    def read_all(self) -> list[ChangeEvent]:
        if not self.path.is_file():
            return []
        events = [ChangeEvent(**json.loads(line)) for line in self.path.read_text().splitlines() if line.strip()]
        return sorted(events, key=lambda e: e.ts)

    def append(self, spec: ChangeSpec, ts: datetime) -> ChangeEvent:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        n = sum(1 for _ in self.path.open()) if self.path.is_file() else 0
        event = ChangeEvent(id=f"CHG-{2000 + n}", ts=ts.astimezone(timezone.utc).isoformat(timespec="seconds"), **spec.model_dump())
        with self.path.open("a") as f:
            f.write(event.model_dump_json() + "\n")
        return event


def record_change_events(
    change: ChangeSpec | None,
    feed: ChangeFeed,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> list[ChangeEvent]:
    """Write the causal change (if the failure has one) and a few distractors. Returns what was written."""
    now = now or datetime.now(timezone.utc)
    rng = rng or random.Random()
    pending: list[tuple[datetime, ChangeSpec]] = []
    if change is not None:
        pending.append((now - timedelta(seconds=CAUSAL_LEAD_S), change))
    for spec in rng.sample(DISTRACTORS, DISTRACTOR_COUNT):
        pending.append((now - timedelta(minutes=rng.randint(2, DISTRACTOR_WINDOW_MIN)), spec))
    return [feed.append(spec, ts) for ts, spec in sorted(pending, key=lambda p: p[0])]
