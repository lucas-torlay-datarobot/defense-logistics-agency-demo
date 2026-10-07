# Document retrieval in the DLA assistant

The assistant can query the operational snapshot, search supply-chain documents, or do both in one turn. Numerical results remain synthetic. Document passages are separate reference evidence, with source names, available metadata, deployment/model IDs, and retrieval time preserved in conversation history and saved reviews.

The configured source is **DLA_Supply_Chain_Processes**, vector database ID `6ac695306e7d24fb54aba935`. The ID identifies a vector database; querying it from this app requires a **vector database deployment ID**. The existing chat-capable LLM remains the planner and answer writer. There is no second LLM, local embedding model, re-upload, or retraining step.

## One-time setup in the Codespace

Pull and build, with the app stopped:

```bash
cd ~/storage/defense-logistics-agency-demo
git pull --ff-only
npm --prefix frontend_web run build
```

In DataRobot, open your Use Case → Vector databases → DLA_Supply_Chain_Processes → **Deploy this version**. Choose your prediction environment and wait for its deployment to become active. Copy its **deployment ID** from Console. If already deployed, reuse that deployment of the intended document version.

```bash
./.venv-dla/bin/python -I scripts/configure_rag.py --deployment-id YOUR_VECTOR_DEPLOYMENT_ID
./.venv-dla/bin/python -I scripts/run_app.py --host 0.0.0.0 --port 8080
```

The setup command changes only the two RAG IDs in `.env`, records the deployment ID under `artifacts/rag`, and runs a real test search. It preserves the app access token and existing LLM settings. It uses the same server-side `DATAROBOT_ENDPOINT` and `DATAROBOT_API_TOKEN` as the chat deployment. The account must have access to the retrieval deployment. It validates the deployment type; when attaching an existing deployment, the operator must select the one created from the stated vector database/version.

Alternatively, deploy the existing index through the SDK:

```bash
./.venv-dla/bin/python -I scripts/configure_rag.py \
  --deploy --prediction-environment-id YOUR_PREDICTION_ENVIRONMENT_ID
```

`DLA_PREDICTION_ENVIRONMENT_ID` can supply that argument. A dedicated prediction server can instead be supplied with `--prediction-server-id`. This command creates platform compute only when no deployment ID is configured or saved. It records an attempt before creation; if interrupted, it refuses to blindly repeat the creation. Find the resulting deployment in Console and resume with `--deployment-id`. A saved/configured ID takes precedence over `--deploy`; no resource is created during chat or app startup.

Run `configure_rag.py` without arguments to recheck the configured deployment. A cold or launching deployment may require another check once active. A shell-exported RAG ID overrides `.env` when the app starts; keep it consistent with the saved ID. The setup command shows sanitized errors and does not print credentials.

## Retrieval and answer behavior

- The plan selects `query`, `retrieve`, `hybrid`, `clarification`, or `unavailable`. Follow-ups are rewritten into a standalone document search using conversation context.
- Search uses five similarity matches from the configured deployment. Duplicate excerpts are removed; context is bounded to 16,000 characters, 4,000 per passage. A retrieval request has a 60-second HTTP timeout; responses over 1 MB are rejected. Network reads have bounded inactivity timeouts, not an absolute end-to-end deadline.
- Server-side deployment metadata resolves serverless versus dedicated prediction routing. HTTPS is required and redirects are not followed. Dedicated prediction keys come from the authenticated deployment record. The model cannot choose a deployment, URL, headers or credentials.
- Documents cannot trigger subsequent SQL or tools: both tool arguments are planned before the passages are seen. The synthesis prompt treats documents as untrusted evidence. SQL retains its existing read-only AST and engine restrictions.
- Answers cite passages as `[D1]`, `[D2]`, etc. Unknown citation IDs or entirely missing citations with retrieved evidence withhold the narrative and retain the passages. This checks reference integrity, not semantic truth or whether every claim is supported.
- Empty or failed document search cannot produce a document-only answer from model memory. A mixed request retains completed SQL evidence if retrieval fails and explicitly reports missing guidance.
- Source names and page/section/revision fields are shown only if returned. Source strings are escaped text, never automatically opened or turned into links. No page numbers or document titles are invented. Scores are not displayed as confidence probabilities.
- The scenario date applies to operations. The retrieval timestamp is separate; the assistant must not assume a document was effective at the scenario date. Guidance does not establish an NIIN-specific packaging instruction, substitute approval, supplier relationship, or authority to place orders.

The vector deployment serves a snapshot of the index. Changing/rebuilding the vector database does not automatically change this deployment. Deploy the intended new version and rerun setup with its ID. Saved reviews retain their original excerpts and retrieval model ID.

## Demo questions and evaluation

Try questions supported by your actual uploaded documents:

1. “What do the documents say about checking the status of an overdue replenishment order?”
2. “Show overdue orders, then retrieve guidance for following up on them.”
3. “What packaging and marking guidance is available?”
4. “Does that document authorize an automatic substitute for this NIIN?” — it should distinguish general guidance from evidence of specific approval.

Before presenting, write 10–15 questions with expected source passages from the uploaded documents, including a follow-up, a mixed SQL/document question, an unanswerable question, and conflicting revision dates. Check retrieval relevance, citation support, correct separation of simulated facts from guidance, and latency. Tune top-k or source chunking based on these results. Similarity retrieval always returns nearest matches when available; it does not prove relevance. The agent must abstain when passages do not support the answer.

Automated tests cover the vendor request contract (serverless and dedicated), response shapes, bounds, errors, citation integrity, SQL preservation, and evidence persistence. They use fixture passages and mocked platform responses. They do not establish live tenant compatibility or real-document answer quality; the setup command is the live integration check.

## Architecture references (reviewed October 7, 2026)

- [DataRobot Agent Assist skills](https://github.com/datarobot-oss/datarobot-agent-skills/tree/main/skills/datarobot-agent-assist): explicit tool/auth contracts, modular agent logic, and evaluation. Applied to this existing React/FastAPI app; this change does not migrate it to the full DRAgent/NAT starter or claim platform-managed memory/tracing.
- [DataRobot GenAI source](https://github.com/datarobot-oss/datarobot-genai): `datarobot-genai==0.29.63`, `drtools/vdb/tools.py::vdb_query` and `drmcputils/deployment.py` define the reference wire contract: deployment lookup, prediction routing, `promptText`, `num_results`, and `retrieval_mode`. The small HTTP adapter avoids installing the whole agent framework into the Codespace. It should be revalidated when upgrading platform versions.
- [Register and deploy vector databases](https://docs.datarobot.com/en/docs/agentic-ai/vector-database/vector-dbs-register-deploy.html).
- [VectorDatabase.deploy SDK reference](https://docs.datarobot.com/en/docs/api/reference/sdk/gen-vector-databases.html).

This remains a single-user workspace with shared access-token scope. Only add documents every workspace user is permitted to read. Multi-user document ACLs and per-user credential propagation require the identity work described in [architecture.md](architecture.md).
