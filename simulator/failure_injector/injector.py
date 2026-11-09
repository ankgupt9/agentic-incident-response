"""Failure injector CLI: reads failures/catalog.yaml and flips the fault switch on a service.

    python -m simulator.failure_injector.injector list
    python -m simulator.failure_injector.injector inject F03 [--duration 120]
    python -m simulator.failure_injector.injector clear
"""

import argparse
import os
import sys
import time
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel

from simulator.failure_injector.change_feed import ChangeFeed, ChangeSpec, record_change_events

CATALOG_PATH = Path(__file__).resolve().parents[2] / "failures" / "catalog.yaml"
# Host-side ports published by docker-compose.yml. Inside the compose network set these to
# http://order-service:8000 and http://pricing-service:8000 (the agent-trigger container does).
SERVICE_URLS = {
    "order-service": os.getenv("ORDER_SERVICE_URL", "http://localhost:8001"),
    "pricing-service": os.getenv("PRICING_SERVICE_URL", "http://localhost:8002"),
}


class FaultSpec(BaseModel):
    service: str
    name: str
    params: dict = {}


class RootCause(BaseModel):
    category: str
    statement: str


class Failure(BaseModel):
    id: str
    title: str
    placeholder: bool = False
    fault: FaultSpec
    expected_alert: str
    # The change a real change record would show for this failure. None when there is no causal change.
    change_event: ChangeSpec | None = None
    symptoms: str
    root_cause: RootCause
    suggested_fix: str


def load_failure_catalog(path: Path = CATALOG_PATH) -> dict[str, Failure]:
    raw = yaml.safe_load(path.read_text())
    failures = [Failure(**item) for item in raw["failures"]]
    ids = [f.id for f in failures]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate failure ids in catalog")
    return {f.id: f for f in failures}


def inject(failure: Failure, client: httpx.Client | None = None, urls: dict[str, str] = SERVICE_URLS) -> None:
    client = client or httpx.Client(timeout=5)
    resp = client.put(f"{urls[failure.fault.service]}/admin/faults/{failure.fault.name}", json=failure.fault.params)
    resp.raise_for_status()


def clear_all(client: httpx.Client | None = None, urls: dict[str, str] = SERVICE_URLS) -> None:
    client = client or httpx.Client(timeout=5)
    for url in urls.values():
        client.delete(f"{url}/admin/faults").raise_for_status()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    inj = sub.add_parser("inject")
    inj.add_argument("failure_id")
    inj.add_argument("--duration", type=float, default=0, help="seconds before auto-clearing (0 = leave on)")
    sub.add_parser("clear")
    args = parser.parse_args(argv)

    catalog = load_failure_catalog()
    if args.cmd == "list":
        for f in catalog.values():
            print(f"{f.id}  {f.title:<32} -> {f.expected_alert}{'  [placeholder]' if f.placeholder else ''}")
    elif args.cmd == "clear":
        clear_all()
        print("all faults cleared")
    else:
        if args.failure_id not in catalog:
            print(f"unknown failure {args.failure_id}", file=sys.stderr)
            return 1
        failure = catalog[args.failure_id]
        inject(failure)
        record_change_events(failure.change_event, ChangeFeed())
        print(f"injected {failure.id}: {failure.title} (expect {failure.expected_alert})")
        if args.duration:
            try:
                time.sleep(args.duration)
            finally:
                clear_all()
                print("cleared")
    return 0


if __name__ == "__main__":
    sys.exit(main())
