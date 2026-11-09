from datetime import date

from knowledge_store.lint import STALE_ERROR_DAYS, STALE_WARN_DAYS, lint
from knowledge_store.models import KnowledgeDoc

TODAY = date(2026, 11, 10)
BANNED = ["stale_quotes", "inject"]
FULL_BODY = "## Symptoms\ns\n## Likely causes\nc\n## How to confirm\nh\n## Fix options\nf\n## Escalate when\ne"


def runbook(**kw) -> KnowledgeDoc:
    base = dict(id="RB-001", kind="runbook", title="t", body=FULL_BODY, owner="ankur", last_reviewed=date(2026, 11, 1), applies_to=["pricing-service"])
    return KnowledgeDoc(**{**base, **kw})


def service(name="pricing-service", **kw) -> KnowledgeDoc:
    base = dict(id=f"svc:{name}", kind="service", title=name, body="Purpose: x", owner="ankur", last_reviewed=date(2026, 11, 1), applies_to=[name])
    return KnowledgeDoc(**{**base, **kw})


def messages(docs, severity="error") -> list[str]:
    return [i.message for i in lint(docs, BANNED, TODAY) if i.severity == severity]


def test_clean_content_has_no_issues():
    assert lint([runbook(), service()], BANNED, TODAY) == []


def test_structure_errors():
    assert any("pattern" in m for m in messages([runbook(id="runbook-1"), service()]))
    assert any("duplicate" in m for m in messages([runbook(), runbook(), service()]))
    assert any("owner" in m for m in messages([runbook(owner=" "), service()]))
    assert any("last_reviewed" in m for m in messages([runbook(last_reviewed=None), service()]))
    assert any("future" in m for m in messages([runbook(last_reviewed=date(2027, 1, 1)), service()]))


def test_staleness_warns_then_fails():
    warn_date = date.fromordinal(TODAY.toordinal() - STALE_WARN_DAYS - 1)
    error_date = date.fromordinal(TODAY.toordinal() - STALE_ERROR_DAYS - 1)
    assert messages([runbook(last_reviewed=warn_date), service()]) == []
    assert any("days ago" in m for m in messages([runbook(last_reviewed=warn_date), service()], "warning"))
    assert any("stale" in m for m in messages([runbook(last_reviewed=error_date), service()]))


def test_runbook_applies_to_must_name_a_catalogued_service():
    assert any("unknown service" in m for m in messages([runbook(applies_to=["payments-service"]), service()]))


def test_eval_leakage_is_an_error():
    assert any("failure catalog id" in m for m in messages([runbook(body=FULL_BODY + "\nSee F03."), service()]))
    assert any("stale_quotes" in m for m in messages([runbook(title="the stale_quotes problem"), service()]))
    assert any("inject" in m for m in messages([runbook(tags=["fault-injection"]), service()]))
    assert messages([runbook(body=FULL_BODY + "\nQuotes can be stale."), service()]) == []  # natural words are fine


def test_shape_warnings_do_not_fail_the_build():
    long_body = FULL_BODY + " word" * 600
    warns = messages([runbook(body=long_body), service()], "warning")
    assert any("words" in w for w in warns)
    assert any("missing section" in w for w in messages([runbook(body="## Symptoms\nonly"), service()], "warning"))
    assert any("draft" in w for w in messages([runbook(status="draft"), service()], "warning"))
