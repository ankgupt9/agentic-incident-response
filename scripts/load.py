"""Steady order traffic against the order service, so dashboards have data.

Usage: python scripts/load.py [--url http://localhost:8001] [--rps 2] [--seconds 0]
"""

import argparse
import random
import time

import httpx

SKUS = ["SKU-1001", "SKU-1002", "SKU-1003", "SKU-1004"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8001")
    parser.add_argument("--rps", type=float, default=2)
    parser.add_argument("--seconds", type=float, default=0, help="0 = run until interrupted")
    args = parser.parse_args()

    deadline = time.time() + args.seconds if args.seconds else None
    sent = failed = 0
    with httpx.Client(base_url=args.url, timeout=10) as client:
        while deadline is None or time.time() < deadline:
            body = {"sku": random.choice(SKUS), "quantity": random.randint(1, 5), "customer_id": f"cust-{random.randint(1, 50)}"}
            try:
                client.post("/orders", json=body).raise_for_status()
            except httpx.HTTPError:
                failed += 1
            sent += 1
            if sent % 20 == 0:
                print(f"sent={sent} failed={failed}", flush=True)
            time.sleep(1 / args.rps)


if __name__ == "__main__":
    main()
