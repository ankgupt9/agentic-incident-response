# knowledge/

What the agent knows beyond live telemetry. Everything here is reviewed like code and ingested into Postgres.
Design: [docs/knowledge-design.md](../docs/knowledge-design.md).

| Folder | Holds | Written by |
|---|---|---|
| `runbooks/` | One markdown file per problem: symptoms, causes, how to confirm, fixes, when to escalate | Ankur |
| `services/` | One YAML file per service or queue: purpose, dependencies, configuration, SLOs, known quirks, escalation | Ankur (drafts below are seeded from the code) |
| `golden_queries.yaml` | Search queries paired with the runbook that should be found | Ankur |

Contracts (message and API shapes) are generated from the code at ingest time. The change feed comes from the simulator. Neither is written by hand.

## Rules

- **Describe symptoms and diagnosis steps**, as a real runbook would. Never name a failure ID (F01...) or a fault switch (names in `failures/catalog.yaml`), and never write the word "inject". Lint fails the build if you do.
- **Write from experience.** Specific beats general. "Quotes can lag the master list after a cache refresh" is worth ten generic sentences.
- One problem per runbook, under about 500 words, with all five sections.
- Every entry needs an `owner` and a `last_reviewed` date. Lint warns after 90 days and fails after 180.
- Keep catalog facts consistent with the change records in `failures/catalog.yaml` (for example, if a change says a cache refresh interval changed, the catalog should describe that cache).
- Plan the set: about 10 runbooks matching failures, 2 or 3 failures with no runbook on purpose, and about 5 distractor runbooks for unrelated problems. That mix is what lets the evals measure whether knowledge helps.
- Runbook ids are `RB-001`, `RB-002`, and so on. Start from `runbooks/_TEMPLATE.md`.

## Commands

```bash
.venv/bin/python -m knowledge_store.ingest --lint-only
```

```bash
.venv/bin/python -m knowledge_store.ingest
```

The first checks content without a database. The second needs Postgres (`docker compose up -d postgres`), lints, and replaces the index only if lint passes.

## Golden queries

`golden_queries.yaml` lists queries an on-call engineer or the agent might write, with the runbook ids that should come back in the top 3. A test checks recall against your real content, so retrieval problems show up before the agent is involved.
