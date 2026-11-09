"""Static checks on the provisioned alerting and dashboards (the live check is in the baseline log)."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent / "observability" / "grafana"
ALERTS = yaml.safe_load((ROOT / "provisioning" / "alerting" / "alerts.yaml").read_text())


def test_agent_alerts_are_labeled_and_routed_away_from_the_agent_trigger():
    group = next(g for g in ALERTS["groups"] if g["name"] == "agent-health")
    assert {r["title"] for r in group["rules"]} == {"AgentCostPerDiagnosisHigh", "AgentToolErrorRateHigh", "AgentGuardrailBlocksHigh", "AgentDiagnosisFailureRateHigh"}
    assert all(r["labels"]["source"] == "agent" for r in group["rules"])
    route = ALERTS["policies"][0]["routes"][0]
    assert route["receiver"] == "ai-governance-notifications" and ["source", "=", "agent"] in route["object_matchers"]
    governance = next(c for c in ALERTS["contactPoints"] if c["name"] == "ai-governance-notifications")
    assert all("8004" not in r["settings"]["url"] for r in governance["receivers"])  # never the agent trigger


def test_agent_alert_queries_use_metrics_the_agent_exports():
    exported = ("agent_cost_usd_total", "agent_diagnoses_total", "agent_tool_calls_total", "agent_guardrail_blocks_total")
    group = next(g for g in ALERTS["groups"] if g["name"] == "agent-health")
    for rule in group["rules"]:
        expr = rule["data"][0]["model"]["expr"]
        assert any(name in expr for name in exported), rule["title"]


def test_dashboard_has_governance_panels_and_valid_json():
    dash = json.loads((ROOT / "dashboards" / "ai-health.json").read_text())
    titles = [p["title"] for p in dash["panels"]]
    assert any("Guardrail blocks" in t for t in titles) and any("Actions proposed" in t for t in titles)
    assert len({p["id"] for p in dash["panels"]}) == len(dash["panels"])
