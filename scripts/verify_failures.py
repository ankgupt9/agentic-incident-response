"""Live check: for each catalog failure, inject it under load and confirm the expected
Grafana alert reaches the webhook, then clear it and wait for the alert to resolve.

Needs the compose stack up. Usage: python scripts/verify_failures.py [F01 F03 ...]
"""

import sys
import threading
import time

import httpx

from simulator.failure_injector.injector import clear_all, inject, load_failure_catalog

ORDER_URL = "http://localhost:8001/orders"
RECORDER_URL = "http://localhost:8003/alerts/recent"
FIRE_TIMEOUT, RESOLVE_TIMEOUT = 180, 300
SKUS = ["SKU-1001", "SKU-1002", "SKU-1003", "SKU-1004"]


def load_worker(stop: threading.Event) -> None:
    with httpx.Client(timeout=10) as client:
        i = 0
        while not stop.is_set():
            i += 1
            body = {"sku": SKUS[i % 4], "quantity": 1, "customer_id": "verify"}
            try:
                client.post(ORDER_URL, json=body)
            except httpx.HTTPError:
                pass
            time.sleep(0.2)


def statuses(client: httpx.Client, alertname: str) -> set[str]:
    seen = set()
    for payload in client.get(RECORDER_URL).json():
        for alert in payload["alerts"]:
            if alert["labels"]["alertname"] == alertname:
                seen.add(alert["status"])
    return seen


def wait_for(client: httpx.Client, alertname: str, status: str, timeout: float) -> float | None:
    start = time.time()
    while time.time() - start < timeout:
        if status in statuses(client, alertname):
            return time.time() - start
        time.sleep(3)
    return None


def main() -> int:
    catalog = load_failure_catalog()
    ids = sys.argv[1:] or list(catalog)
    stop = threading.Event()
    workers = [threading.Thread(target=load_worker, args=(stop,), daemon=True) for _ in range(6)]
    for w in workers:
        w.start()
    results = {}
    with httpx.Client(timeout=5) as client:
        try:
            for fid in ids:
                failure = catalog[fid]
                clear_all(client)
                client.delete(RECORDER_URL)
                inject(failure, client)
                fired = wait_for(client, failure.expected_alert, "firing", FIRE_TIMEOUT)
                clear_all(client)
                resolved = wait_for(client, failure.expected_alert, "resolved", RESOLVE_TIMEOUT) if fired else None
                results[fid] = (fired, resolved)
                print(f"{fid} {failure.expected_alert}: fired={fired and round(fired)}s resolved={resolved and round(resolved)}s", flush=True)
        finally:
            clear_all(client)
            stop.set()
    return 0 if all(f and r for f, r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
