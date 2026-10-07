# Agent and application design

The VA pattern is preserved: shared domain data services power both structured UI views and the docked assistant. Dashboard metrics are SQL results, never parsed from chat text.

## Request path

1. The authenticated API loads the current snapshot schema, source IDs, the last 10 conversation messages (up to 8 evidence rows per message), and explicitly saved preferences.
2. The DataRobot chat deployment returns a structured plan: query, document retrieval, both, clarification, or unavailable. It can propose new SQL, rather than selecting only hard-coded question intents.
3. A SQL AST check permits a single SELECT/non-recursive CTE over five curated tables and a conservative scalar/aggregate function allowlist. SQL is never passed to a shell. One repair attempt is permitted for invalid SQL or schema references.
4. DuckDB opens the snapshot read-only, with external access and extension loading disabled, locked configuration, one thread, 512 MB memory, no spill space, and a 12-second interrupt timer. Queries are serialized. Returned data is capped at 200 rows/100 KB, 60 uniquely named columns, and 2,000 characters per text cell. Large/full scans may still be necessary for an aggregate.
5. For document questions, a bounded retrieval tool searches the configured deployed vector database. The LLM summarizes the bounded SQL and/or document evidence. The API returns its SQL, actual rows, source table names, source dataset/version IDs, snapshot ID, scenario date and synthetic marker. Narrative failures preserve completed evidence.
6. SQLite stores the user turn and assistant response. Explicit preferences persist across conversations. Review items preserve the evidence snapshot and are idempotent by assistant message ID.

The query restrictions reduce access/resource risks; they do not establish that an LLM's SQL or explanation is semantically correct. In particular, fan-out, date boundaries, and unit aggregation still require evaluation. The prompt documents grain and allowed joins, historical receipt visibility, scenario dates, probability semantics, absent domains, and the difference between simulated operations and catalog identities. Tables are trusted prepared files, never uploaded arbitrary DuckDB databases.

## Semantic tables

| Table | Grain | Meaning |
|---|---|---|
| `items` | NIIN | Catalog identifiers and names represented in this scenario |
| `daily_inventory` | date × NIIN × location | Synthetic observed stock, demand, fulfillment and receipts |
| `replenishment_orders` | order ID | Synthetic order and receipt dates; open/overdue flags at snapshot date |
| `latest_inventory` | NIIN × location at as-of date | Shared causal features plus catalog identity and nullable model score |
| `risk_scores` | date × NIIN × location | Actual output of the selected DataRobot model; empty when not scored |

Identifiers stay strings so leading zeros survive. Join `items` on NIIN; join risk on date+NIIN+location. Aggregate order rows by NIIN+destination before joining inventory. Never sum quantity across different NIINs as a meaningful inventory-volume KPI; quantity units are synthetic and item-specific. “Observed shortage incidence” is the percentage of item-location days with unmet demand, rather than a mixture of different item units.

As-of date is the latest completed scenario date. The initial release serves one fixed scenario snapshot. Historical questions use historical observations; it does not backfill historical model predictions. The app does not load organizations, characteristics, freight or packaging, nor infer an NIIN-to-supplier relationship that is not present. Additional domains can be added with explicit grain, provenance, bounded preparation and tests.

`prepare_app_data.py --score` imports the existing prediction modules without modifying them. This preserves the training code digest contract. Scoring uses the frozen selected model, not a newly chosen leaderboard candidate. There is no generated/heuristic probability fallback. Probability ≥50% is an illustrative watchlist filter, not a validated business decision threshold. The app does not claim an order/transfer will reduce risk.

## Persistence and deployment boundary

The app is single-user. All users of the shared app access token share the same conversations and memories; the token is not an identity system. The browser only receives that app token and public-to-this-workspace evidence, never the DataRobot API token. Browser-to-app requests use `X-DLA-App-Token` and same-origin cookies, so the Codespace forwarding proxy can use its own platform authentication without interpreting the app token as a DataRobot bearer token. Server-side DataRobot LLM and SDK calls still use DataRobot credentials as required. No CORS access is enabled. React renders data as escaped text, never model-authored HTML. CSV exports escape formula-like cells.

Before shared hosting, add trusted per-user identity and authorization to every data, conversation, memory and review access; migrate memory to a durable service with those scopes; package the snapshot refresh job; and choose a hosted application environment. Do not expose a shared token as a substitute for multi-user access control. The current API requires a secret even for localhost development and the run script defaults to localhost.

References: [DataRobot LLM provider configuration](https://docs.datarobot.com/en/docs/agentic-ai/agentic-develop/agentic-llm-providers.html), [DuckDB security](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview), and [VA app attribution](../../THIRD_PARTY_NOTICES.md).

## Reference document retrieval

See [RAG setup and evaluation](rag.md) for the deployment contract, citation behavior, versioning, limits, and current DataRobot architecture references. Retrieval is an additional read-only tool; the dashboard and operational snapshot stay independent. Reference passages are saved alongside SQL evidence and shown in the chat and review queue. No resources are provisioned during a chat turn.
