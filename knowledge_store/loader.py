"""Load knowledge from files and from the code itself.

Runbooks (markdown with YAML frontmatter) and the service catalog (YAML) are authored by people.
Contracts are generated from the Pydantic models and FastAPI apps so they cannot drift from reality.
"""

from datetime import date
from pathlib import Path

import yaml
from pydantic import ValidationError

from knowledge_store.models import KnowledgeDoc

CONTRACT_MAX_ENDPOINTS = 20


class LoadError(Exception):
    pass


def _as_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise LoadError(f"invalid date {value!r}") from exc


def _strings(values) -> list[str]:
    """YAML reads an unquoted 502 as a number; tags and service names are always text."""
    return [str(v) for v in (values or [])]


def parse_runbook(path: Path) -> KnowledgeDoc:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise LoadError(f"{path.name}: missing frontmatter")
    try:
        _, front, body = text.split("---\n", 2)
    except ValueError as exc:
        raise LoadError(f"{path.name}: unterminated frontmatter") from exc
    meta = yaml.safe_load(front) or {}
    try:
        return KnowledgeDoc(
            id=meta["id"],
            kind="runbook",
            title=meta["title"],
            body=body.strip(),
            owner=meta.get("owner", ""),
            last_reviewed=_as_date(meta.get("last_reviewed")),
            applies_to=_strings(meta.get("applies_to")),
            tags=_strings(meta.get("tags")),
            status=meta.get("status", "active"),
            source_path=str(path),
        )
    except KeyError as exc:
        raise LoadError(f"{path.name}: missing frontmatter field {exc}") from exc
    except ValidationError as exc:
        raise LoadError(f"{path.name}: {exc.errors()[0]['loc'][0]}: {exc.errors()[0]['msg']}") from exc


def render_service(data: dict) -> str:
    """Turn a service catalog YAML mapping into readable text for the model."""
    lines = [f"Purpose: {data.get('purpose', '').strip()}"]
    for label, key in (("Depends on", "depends_on"), ("Depended on by", "depended_on_by")):
        if data.get(key):
            lines.append(f"{label}: {', '.join(data[key])}")
    interfaces = data.get("interfaces") or {}
    for kind, items in interfaces.items():
        lines.append(f"Interfaces ({kind}): {', '.join(items)}")
    for key, value in (data.get("configuration") or {}).items():
        lines.append(f"Configuration, {key}: {value}")
    for key, value in (data.get("slo") or {}).items():
        lines.append(f"SLO, {key}: {value}")
    for quirk in data.get("known_quirks") or []:
        lines.append(f"Known quirk: {quirk}")
    escalation = data.get("escalation") or {}
    if escalation:
        lines.append("Escalation: " + ", ".join(f"{k} {v}" for k, v in escalation.items()))
    return "\n".join(lines)


def parse_service(path: Path) -> KnowledgeDoc:
    data = yaml.safe_load(path.read_text()) or {}
    try:
        name = data["id"]
    except KeyError as exc:
        raise LoadError(f"{path.name}: missing field 'id'") from exc
    return KnowledgeDoc(
        id=f"svc:{name}",
        kind="service",
        title=name,
        body=render_service(data),
        owner=data.get("owner", ""),
        last_reviewed=_as_date(data.get("last_reviewed")),
        applies_to=[name],
        tags=_strings(data.get("tags")),
        status=data.get("status", "active"),
        source_path=str(path),
    )


def load_authored(knowledge_dir: Path) -> list[KnowledgeDoc]:
    """Runbooks and service catalog entries. Files starting with '_' (templates) are skipped."""
    docs: list[KnowledgeDoc] = []
    for path in sorted((knowledge_dir / "runbooks").glob("*.md")):
        if not path.name.startswith("_"):
            docs.append(parse_runbook(path))
    for path in sorted((knowledge_dir / "services").glob("*.yaml")):
        if not path.name.startswith("_"):
            docs.append(parse_service(path))
    return docs


def _render_schema(schema: dict) -> list[str]:
    required = set(schema.get("required", []))
    lines = []
    for name, prop in schema.get("properties", {}).items():
        kind = prop.get("type", "any")
        note = prop.get("description", "")
        req = "required" if name in required else "optional"
        lines.append(f"  {name}: {kind}, {req}" + (f". {note}" if note else ""))
    return lines


def render_openapi(spec: dict) -> str:
    lines: list[str] = []
    schemas = spec.get("components", {}).get("schemas", {})
    count = 0
    for path, methods in spec.get("paths", {}).items():
        if path.startswith("/admin"):  # fault-injection admin routes are not part of the contract
            continue
        for method, op in methods.items():
            count += 1
            if count > CONTRACT_MAX_ENDPOINTS:
                continue
            lines.append(f"{method.upper()} {path}")
            for p in op.get("parameters", []):
                lines.append(f"  {p['in']} parameter {p['name']}: {p.get('schema', {}).get('type', 'any')}")
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
            if "$ref" in body:
                lines.append("  request body:")
                lines += ["  " + ln for ln in _render_schema(schemas.get(body["$ref"].rsplit("/", 1)[-1], {}))]
            lines.append("  responses: " + ", ".join(op.get("responses", {})))
    return "\n".join(lines) or "no endpoints"


def generate_contracts() -> list[KnowledgeDoc]:
    """Contract documents derived from the simulator's code. Imported lazily: it loads the apps."""
    from simulator.common.contracts import OrderMessage
    from simulator.order_service.main import app as order_app
    from simulator.pricing_service.main import app as pricing_app

    message_lines = ["Queue: orders (durable). JSON body, persistent delivery. Fields:"]
    message_lines += _render_schema(OrderMessage.model_json_schema())
    message_lines.append("The consumer (pricing-service) reads sku, quantity and quoted_price from every message.")
    specs = {
        "contract:order-message": ("Order message on the orders queue", "\n".join(message_lines), ["order-service", "pricing-service", "rabbitmq"]),
        "contract:order-service-api": ("order-service HTTP API", render_openapi(order_app.openapi()), ["order-service"]),
        "contract:pricing-service-api": ("pricing-service HTTP API", render_openapi(pricing_app.openapi()), ["pricing-service"]),
    }
    return [
        KnowledgeDoc(id=doc_id, kind="contract", title=title, body=body, owner="generated", applies_to=applies, tags=["contract", "schema"], source_path="generated from code")
        for doc_id, (title, body, applies) in specs.items()
    ]
