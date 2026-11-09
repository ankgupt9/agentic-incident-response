import random
from datetime import datetime, timezone

import pytest

from simulator.failure_injector.change_feed import DISTRACTORS, ChangeFeed, ChangeSpec, record_change_events
from simulator.failure_injector.injector import load_failure_catalog

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def feed(tmp_path):
    return ChangeFeed(tmp_path / "changes.jsonl")


def test_causal_change_is_written_before_the_failure_with_distractors(feed):
    spec = load_failure_catalog()["F03"].change_event
    written = record_change_events(spec, feed, NOW, random.Random(1))
    assert len(written) == 4
    causal = next(e for e in written if e.summary == spec.summary)
    assert causal.ts == "2026-11-10T11:59:00+00:00"
    assert all(e.ts <= NOW.isoformat(timespec="seconds") for e in written)


def test_failures_without_a_causal_change_still_get_distractors(feed):
    written = record_change_events(None, feed, NOW, random.Random(1))
    assert len(written) == 3
    assert all(any(e.summary == d.summary for d in DISTRACTORS) for e in written)


def test_catalog_has_both_kinds_of_failure():
    changes = {f.id: f.change_event for f in load_failure_catalog().values()}
    assert changes["F07"] is None and changes["F08"] is None
    assert all(changes[i] is not None for i in ("F01", "F02", "F03", "F04", "F05", "F06"))


def test_feed_is_ordered_readable_and_ids_are_sequential(feed):
    record_change_events(None, feed, NOW, random.Random(2))
    record_change_events(ChangeSpec(service="s", kind="config", summary="x"), feed, NOW, random.Random(3))
    events = feed.read_all()
    assert len(events) == 7
    assert [e.ts for e in events] == sorted(e.ts for e in events)
    assert sorted(e.id for e in events) == [f"CHG-{2000 + i}" for i in range(7)]


def test_missing_feed_file_reads_as_empty(tmp_path):
    assert ChangeFeed(tmp_path / "nope.jsonl").read_all() == []


def test_same_seed_gives_same_distractors(tmp_path):
    a = [e.summary for e in record_change_events(None, ChangeFeed(tmp_path / "a.jsonl"), NOW, random.Random(7))]
    b = [e.summary for e in record_change_events(None, ChangeFeed(tmp_path / "b.jsonl"), NOW, random.Random(7))]
    assert a == b


def test_no_change_record_names_the_fault_switch_or_the_failure_id():
    """Leakage lint: records must read like real change records, not like the injector's own notes."""
    texts = []
    for f in load_failure_catalog().values():
        banned = {f.id.lower(), f.fault.name.lower(), f.fault.name.replace("_", " ").lower(), "inject", "fault"}
        records = [f.change_event] if f.change_event else []
        for r in records:
            text = f"{r.summary} {r.actor} {r.ref}".lower()
            assert not any(b in text for b in banned), (f.id, text)
        texts += [r.summary for r in records]
    for d in DISTRACTORS:
        assert not any(w in d.summary.lower() for w in ("inject", "fault", "stale", "mismatch", "drift"))
