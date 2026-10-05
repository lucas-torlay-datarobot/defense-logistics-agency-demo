# Run the DLA agent and React app

This builds on your **completed prediction run**. It does not train another model. The React layout follows the VA clinic scheduling app: a compact console, left navigation, domain views, and an assistant dock on the right.

The app supports:

- Inventory and incoming-supply views from your existing synthetic dataset pair, with real catalog identifiers and names.
- Current 14-day shortage probabilities from your **selected DataRobot model**.
- Adaptable questions and follow-ups: an LLM generates SQL against the available schema, a bounded read-only tool executes it, and the answer includes the actual rows, SQL, date and source provenance.
- Persistent conversations, explicit saved preferences, and an evidence review queue. Saving a review does not place an order or move stock.

All operations and model results remain prominently marked **synthetic**. This is a single-user demonstration, not a production inventory system or a Databricks Genie integration.

## 1. Pull and install in the existing Codespace environment

Run from the repository directory. Keep the `-I`: this isolates Python from the notebook kernel's injected package paths that caused the earlier `distutils` error.

```bash
git pull --ff-only
./.venv-dla/bin/python -I -m pip install -e './prediction[dev]' -e ./agent -e './fastapi_server[dev]'
node --version
npm --prefix frontend_web ci
npm --prefix frontend_web run build
```

Use Node **20.19+ or 22.12+** (Node 24 also works). Node is used to build the frontend; the production app is then served by Python. Do not replace the notebook kernel environment or overwrite your existing `.env`.

## 2. Prepare and score the app snapshot

Use the prediction run directory that contains `manifest.json`, `model_contract.json`, and `evaluation.json`. For the completed run discussed in this project:

```bash
./.venv-dla/bin/python -I scripts/prepare_app_data.py \
  --run-dir artifacts/prediction/c4b2d58cfff4154c76ca1c2a \
  --score
```

`--score` sends only the latest item-location feature rows to the **existing selected project model** through the DataRobot SDK. This is batch scoring on DataRobot compute, without starting Autopilot or requiring model deployment. Jobs and probabilities are cached under the prediction run's `app_scoring/` directory; rerunning resumes or reuses them. The previous training/model selection files are unchanged. Test/baseline results are displayed in Memory & provenance; a failed baseline gate remains visible even when you inspect scores.

The script verifies input hashes against the training manifest and builds `artifacts/app/snapshot.duckdb`. It uses the small operational dataset pair and the catalog fields already embedded in them. It does **not** download the 17-million-row item catalog or 39-million-row characteristics table. The registered datasets stay in DataRobot; this is their local query snapshot, not another dataset registration.

To inspect the app without scoring, omit `--score`. Probabilities remain blank and the interface says scores are unavailable. Running preparation without `--score` replaces any prior scored app snapshot with an unscored one; use `--score` again to reuse cached scores.

If cached CSVs moved, supply both `--daily-csv /path/to/daily.csv --orders-csv /path/to/orders.csv`. They must match the original dataset bytes/hashes. If the source cache was lost, download the exact dataset versions recorded in the prediction manifest; do not silently use a newer snapshot. Preparation errors leave the last successful app database intact. Stop the app before rebuilding its snapshot, then restart it; an already running process rejects a changed snapshot to prevent mixed evidence.

## 3. Connect the language model

The shortage classifier and the language model have different jobs:

| Component | Job | Configuration |
|---|---|---|
| Existing shortage model | Predicts the next-14-day probability | Read automatically from the prediction run |
| Chat-capable LLM deployment | Interprets questions, proposes SQL, explains returned evidence | `DLA_LLM_DEPLOYMENT_ID` |

Use an existing **chat-capable LLM deployment** in DataRobot, potentially the one used by your VA app if you have access. A predictive classification deployment is not an LLM deployment. If you do not have one, deploy an LLM blueprint from a DataRobot Playground with chat-completions support.

Add these values to your existing untracked `.env`, or set equivalent Codespace secrets:

```dotenv
DATAROBOT_ENDPOINT=https://YOUR-TENANT/api/v2
DATAROBOT_API_TOKEN=YOUR_SERVER_SIDE_API_TOKEN
DLA_LLM_DEPLOYMENT_ID=YOUR_CHAT_CAPABLE_LLM_DEPLOYMENT_ID
DLA_LLM_MODEL=datarobot-deployed-llm
DLA_APP_ACCESS_TOKEN=YOUR_RANDOM_APP_ACCESS_TOKEN_AT_LEAST_24_CHARACTERS
```

Generate an app access token in your terminal, then put the result in `DLA_APP_ACCESS_TOKEN`:

```bash
./.venv-dla/bin/python -I -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Browser requests send the app access token in `X-DLA-App-Token`, leaving DataRobot platform authentication and same-origin session cookies intact. The app access token is **not** your DataRobot API token. Only enter the app access token in the browser. DataRobot credentials remain in the Python server. The LLM adapter expects the explicit environment variables above, even if the predictive SDK can authenticate from a separate SDK configuration file.

The server uses the documented OpenAI-compatible endpoint:
`{DATAROBOT_ENDPOINT}/deployments/{DLA_LLM_DEPLOYMENT_ID}/chat/completions`.
The dashboard, SQL workbench, memory and review queue work without an LLM; free-form chat is disabled rather than replaced with canned answers. Live LLM quality depends on the selected deployment and must be checked with your data.

Official API reference: [DataRobot LLM providers](https://docs.datarobot.com/en/docs/agentic-ai/agentic-develop/agentic-llm-providers.html).

## 4. Start one server

```bash
./.venv-dla/bin/python -I scripts/run_app.py --host 0.0.0.0 --port 8080
```

In **Session Environment → Exposed ports**, add port **8080** if it is missing (DataRobot requires the session to be stopped while changing exposed ports), then restart the session and run the server command above. Click **Link** beside port **8080** to open the app in a new tab. Paste `DLA_APP_ACCESS_TOKEN` on the welcome screen. Keep this terminal running. The token is stored in browser session storage; locking the workspace clears it. The UI and API share one port. Binding `0.0.0.0` permits Codespace port forwarding; use the default localhost binding for local-only access.

Start with these questions:

- “Which items have the highest 14-day shortage probability?”
- “Which of those have overdue incoming orders at the same depot?”
- “Compare shortage frequency across depots during the last 28 days.”
- “Show the demand history for NIIN [pick one from inventory] at Depot B.”
- “Which NIINs had shortages at more than one depot?”

Select **Ask agent** on an inventory row to carry its NIIN and depot into a question. The agent treats “today” as the scenario snapshot date, shown in the header. Inspect the evidence table and SQL, download the bounded result as CSV, and save relevant results for review.

Use **Memory** to explicitly save a preference, such as “Focus on Depot B unless I specify otherwise.” Conversations and preferences are stored in `artifacts/app/memory.sqlite3`, not just browser state. They survive server restarts while that disk persists. Deleting a conversation deletes its messages; an independently saved review retains its evidence. Preferences can be deleted separately. To back up memory, stop the app and copy the app directory. Codespace/session deletion can still remove local storage; this is not a hosted multi-user memory service.

## Where compute runs

| Work | Compute/storage |
|---|---|
| Snapshot preparation, SQL, FastAPI, persistent demo memory | Your Codespace and its disk |
| Batch shortage scoring | Existing DataRobot project model |
| Question interpretation and answer generation | Configured DataRobot LLM deployment |
| React rendering | Browser |
| Source and tests | GitHub |

This commit provides the runnable Codespace application. It does not create a hosted DataRobot Custom Application or deploy the agent as a separate model. Those are subsequent packaging/hosting steps once the data and LLM connection are validated.

## Troubleshooting

- **Browser shows HTTP 428:** older app builds put the app token in the platform's `Authorization` header. Pull the latest code, rebuild React, restart the Python process and hard-refresh the browser. Keep the existing app token. If 428 persists, reopen the current exposed-port link and inspect the failed request path/status/response in browser Developer Tools → Network. Share only those details, never tokens or cookies; a 428 alone does not identify the platform's exact precondition.
- **No probability values:** run preparation with `--score`. Training success alone does not populate current app scores.
- **LLM HTTP 401/403:** check the server token, tenant, and deployment access. **404/405:** confirm this is a chat-capable LLM deployment and the endpoint ends in `/api/v2`.
- **SQL rejected:** queries are intentionally limited to the five published tables and safe analytical functions; external files, recursive queries, writes, and administrative functions are unavailable. Narrow the question or inspect the schema under Explore.
- **Ambiguous or unsupported questions:** the agent should ask a clarifying question or explain what data is missing. Supplier recommendation, cost savings, approved substitutes, real DLA readiness, and optimal stock transfers are not supported by these tables.
- **Scoring interrupted:** rerun the identical preparation command. If a DataRobot prediction job has failed permanently, inspect its saved ID in `app_scoring/state.json` before removing that failed job entry and retrying.
- **Notebook shuts down:** no Python code can prevent a platform lifecycle shutdown. The app snapshot and memory must live on persistent storage. Restart with the same directory; the app does not need to reload the large catalogs.
- **Frontend changed after pulling:** rerun `npm ci` and `npm run build`, then restart the Python process.

## Verification and scope

```bash
./.venv-dla/bin/python -I -m pytest prediction/tests fastapi_server/tests -q
./.venv-dla/bin/python -I -m ruff check prediction agent fastapi_server scripts
npm --prefix frontend_web run build
```

Tests exercise source identity, join grain, missing-score behavior, query restrictions/time limits, authenticated API calls, session persistence, explicit memory, SQL repair and inspectable evidence. Cloud calls are mocked in unit tests. Run the example questions against your actual LLM and review SQL correctness before a live demonstration. An LLM explanation is an interpretation; the query rows and recorded source snapshot are the checkable evidence.
