# Defense Logistics Agency demo

A prediction → agent with memory → application demonstration using real PUB LOG catalog identifiers and **explicitly synthetic operational history**. The initial implementation is the prediction foundation. Agent and UI work will follow the VA clinic scheduling application's structure and visual conventions.

## What this release predicts

**At the end of today, will this item at this fictional depot experience unmet demand during the next 14 days?**

A supervised binary model learns from the two synthetic datasets already generated in DataRobot. This is not a demand forecast or a claim about actual DLA shortages. Real catalog item attributes do not establish actual inventory, supplier relationships, or operational readiness.

- Reads `SYNTHETIC_daily_inventory_demand__...` and `SYNTHETIC_replenishment_orders__...` from your existing Use Case.
- Builds historical features without revealing future demand or actual future deliveries.
- Uses DataRobot Quick Autopilot and its leaderboard to shortlist models; reranks the shortlist by validation average precision.
- Evaluates only the selected model on a separate final period; compares it with a calibrated inventory-coverage baseline and training-prevalence baseline.
- Optionally deploys a passing model and creates a ranked daily risk CSV through its prediction endpoint.
- Saves source versions, project/job/model IDs, and reports for resume. No tokens or source datasets are committed.

## Run from your DataRobot Codespace terminal

The terminal prepares data on **Codespace compute**. SDK requests submit training to **DataRobot modeling compute**. Deployed predictions execute in your **DataRobot prediction environment**. GitHub stores the code; it does not start those jobs.

```bash
git clone https://github.com/lucas-torlay-datarobot/defense-logistics-agency-demo.git
cd defense-logistics-agency-demo
python -m pip install -e './prediction[dev]'
cp .env.example .env
```

Use your Codespace's existing DataRobot authentication. If authentication is not available in its terminal, configure `DATAROBOT_ENDPOINT` and `DATAROBOT_API_TOKEN` through Codespace secrets or the untracked `.env`. Never commit a token. Set your tenant endpoint, not an assumed public-cloud URL.

With one matching synthetic dataset pair, IDs are discovered automatically in `Defense_Logistics_Demo`. If multiple snapshots exist, set `DLA_DAILY_DATASET_ID` and `DLA_ORDERS_DATASET_ID` to the intended matching pair in `.env`. The code verifies their simulation ID and source item version agree.

First prepare the features without creating training resources:

```bash
python scripts/provision_demo_models.py --dry-run
```

Then train and rank models:

```bash
python scripts/provision_demo_models.py
```

The script prints the run directory and DataRobot project ID. Inspect that experiment in the DataRobot UI and the generated `evaluation.json` and `model_ranking.csv`. Training may take a while; rerun the same command after interruption to resume recorded work. Do not start concurrent copies from different checkouts.

For deployment, set `DLA_PREDICTION_ENVIRONMENT_ID` to the intended environment ID, then:

```bash
python scripts/provision_demo_models.py --deploy
```

This resumes the same run and deploys only when the selected model beats the documented baselines. It does **not** silently choose a runner-up using final-test results. The evaluated model is deployed without a full-data refit, so its artifact remains the one tested. If gates fail, reports remain available and no deployment is created.

Score the latest day (substitute the run directory printed above):

```bash
python scripts/score_demo.py --run-dir artifacts/prediction/YOUR_RUN_ID
```

The output is a `SYNTHETIC_risk_YYYY-MM-DD.csv` ranked by shortage probability. The deployment ID comes from that run's saved state. No synthetic fallback probabilities are substituted when the deployment is unavailable.

### VA-style command alternatives

The public entry point is `scripts/provision_demo_models.py`, matching the VA repository's provisioning pattern. Reusable code and dependencies live in their own `prediction` package; `Taskfile.yml` provides the same style of project-level commands.

```bash
uv sync --project prediction --extra dev
uv run --project prediction python scripts/provision_demo_models.py --dry-run
uv run --project prediction python scripts/provision_demo_models.py
# If go-task is installed:
task prediction:prepare
task prediction:train
task prediction:deploy
```

## Outputs

| File under `artifacts/prediction/<run>/` | Purpose |
| --- | --- |
| `manifest.json` | Source dataset IDs/versions/checksums, code hash, feature contract, date splits |
| `SYNTHETIC_train_validation.csv` | Only permitted model inputs, target and partition; registered as a dataset |
| `SYNTHETIC_labeled_audit.csv` | Locally held keyed observations, including final-test labels |
| `model_ranking.csv` / `.json` | Common validation-set model comparison |
| `evaluation.json` | Selected model's final-period metrics, baselines, deployment gates |
| `model_contract.json` | Feature order, positive class, horizon, project and selected model IDs |
| `feature_impact.json` | Optional global explanation data from DataRobot |
| `runtime.env` | IDs for later agent/application configuration; no credentials |
| `state.json` | Checkpoints for dataset/project/jobs/model/deployment |

Keep the complete run directory to retain checkpoints and cached scores. It is intentionally excluded from Git. Local run artifacts can be lost with the Codespace filesystem; version-specific cloud dataset/project names support partial recovery, but local reports must be regenerated. Failed uploads with ambiguous outcomes stop for inspection instead of blindly submitting duplicates.

## Development

```bash
python -m pytest prediction/tests -q
python -m ruff check prediction scripts
python -m ruff format --check prediction scripts
```

See [model design and limitations](docs/prediction/model-design.md) for target semantics, temporal splits, scoring conventions, and evaluation limits. The training code is not a substitute recommendation engine, procurement decision system, or validated DLA operational model.
