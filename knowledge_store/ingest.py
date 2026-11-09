"""Ingest knowledge into Postgres: load, lint, then replace the index.

    python -m knowledge_store.ingest                 # ingest knowledge/ and the change feed
    python -m knowledge_store.ingest --lint-only     # check content without touching the database
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from knowledge_store.db import KnowledgeStore
from knowledge_store.lint import Issue, banned_terms, lint
from knowledge_store.loader import generate_contracts, load_authored
from knowledge_store.models import ChangeRecord, KnowledgeDoc

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FEED = ROOT / "data" / "change_events.jsonl"
CATALOG = ROOT / "failures" / "catalog.yaml"


@dataclass
class IngestResult:
    docs: list[KnowledgeDoc]
    issues: list[Issue]
    version: str | None = None
    change_events: int = 0

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


def load_change_feed(path: Path) -> list[ChangeRecord]:
    if not path.is_file():
        return []
    return [ChangeRecord(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def check(knowledge_dir: Path, with_contracts: bool = True) -> tuple[list[KnowledgeDoc], list[Issue]]:
    docs = load_authored(knowledge_dir)
    if with_contracts:
        docs += generate_contracts()
    return docs, lint(docs, banned_terms(CATALOG))


async def ingest(knowledge_dir: Path, feed_path: Path, store: KnowledgeStore, with_contracts: bool = True) -> IngestResult:
    docs, issues = check(knowledge_dir, with_contracts)
    result = IngestResult(docs, issues)
    if result.errors:
        return result  # never load content that fails lint
    await store.apply_schema()
    result.version = await store.replace_docs(docs)
    result.change_events = await store.import_change_events(load_change_feed(feed_path))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--knowledge-dir", type=Path, default=ROOT / "knowledge")
    parser.add_argument("--feed", type=Path, default=Path(os.getenv("CHANGE_FEED_PATH", DEFAULT_FEED)))
    parser.add_argument("--lint-only", action="store_true")
    args = parser.parse_args(argv)

    if args.lint_only:
        docs, issues = check(args.knowledge_dir)
        result = IngestResult(docs, issues)
    else:
        result = asyncio.run(ingest(args.knowledge_dir, args.feed, KnowledgeStore()))
    for issue in result.issues:
        print(issue)
    kinds = {k: sum(1 for d in result.docs if d.kind == k) for k in ("runbook", "service", "contract")}
    print(f"{len(result.docs)} documents {kinds}, {len(result.errors)} errors, {len(result.issues) - len(result.errors)} warnings")
    if result.errors:
        print("lint failed: nothing was ingested" if not args.lint_only else "lint failed")
        return 1
    if not args.lint_only:
        print(f"ingested; knowledge version {result.version}; {result.change_events} change events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
