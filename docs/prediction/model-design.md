# Prediction design

## Model choice

The first task is pooled **14-day shortage classification** across item/location pairs, using end-of-day tabular features. It directly supports a planner's ranked investigation queue. A single year of synthetic history is not a strong reason to build hundreds of separate item forecasts or to add unrelated supplier-failure models.

DataRobot's Quick Autopilot decides which available tabular blueprints to train. Depending on platform availability this can include linear models and nonlinear tree/boosting approaches. The code does not claim a particular algorithm will win and does not hard-code a preferred family. `autopilot_mode="auto"` allows a broader normal Autopilot run; comprehensive mode is deliberately not the default.

## Target and timing

For an observation at the **end** of date t:

`shortage_next_14d = 1` if any `unfulfilled_quantity > 0` on t+1 through t+14.

The current day's shortage is historical information, not part of the future target. The final 14 observation dates have incomplete outcomes and are excluded from labeled data. Zero closing stock without unmet demand is not itself a positive label. Unmet demand is lost demand in this simulator, not a retained backlog.

Features include demand means/variation, recent unmet demand, stock, expected incoming units, overdue orders and observed delivery history. Requested quantity represents demand; fulfilled quantity alone would underestimate it when stock is depleted.

An order is known only once placed. It is open at t if no receipt has been observed by t. Expected receipt dates are known at placement and fixed by this simulator. Actual lead times and lateness enter features only after a receipt occurs. An order's eventual status and future actual receipt date are never model features. No new orders after t are assumed in the coverage baseline.

Only the `FEATURES` allowlist is sent to DataRobot. NIIN, NSN, item text, location identifiers, simulator seed/ID, final-snapshot status, row IDs and audit metadata remain outside the model matrix. The simulation did not tie real catalog attributes to demand, so including them would add apparent domain sophistication without evidence of a real relationship.

## Chronological evaluation

Default 365-day scenario:

- Skip the initial 60 days of assumed starting inventory.
- Reserve the last 45 fully labeled observation days for an external final test.
- Reserve the preceding 60 observation days for validation, with a 14-day purge gap before the test.
- Train on earlier observations, with another 14-day purge before validation.
- Require at least 90 training dates and both target classes in every split.

A training outcome window ends before the first validation observation. A validation outcome window ends before the first final-test observation. Feature histories may extend into prior dates, as they would for a real planner. The same NIINs appear across periods: this tests future decisions on an existing item portfolio, not generalization to unseen parts.

DataRobot receives a `UserTVH` train/validation partition **without final-test data**. The external test cannot influence Autopilot. No random cross-validation or full-data refit is used by this workflow.

Daily horizons overlap within partitions, so rows and errors are correlated. Reported metrics are descriptive, not confidence intervals or independent sample counts. One scenario and one final period are not operational validation. Before stronger claims, use independent simulation seeds, shifted disruption periods and ultimately real historical outcomes.

## Ranking and baselines

1. Take up to five models with the lowest finite DataRobot **validation LogLoss**. Exclude models trained beyond the training partition (including 100% refits).
2. Score each candidate on the same complete chronological validation rows.
3. Rank by **average precision**, a precision-recall summary relevant to a minority shortage class; use LogLoss then model ID to break ties. Record whether DataRobot recommended each candidate.
4. Freeze the winner before requesting its external final-test predictions. Never use test performance to choose a runner-up.
5. Evaluate the winner and two baselines on that final period.

Metrics include average precision, LogLoss, Brier score, ROC AUC, and precision/recall within a **daily** top-10% investigation budget. The same deterministic NIIN/location tie-break is used for all candidates and baselines. The investigation budget is a demo assumption, not a DLA staffing constraint. A high ROC AUC alone does not justify deployment.

The coverage baseline estimates 14-day demand from the trailing 28-day requested-demand mean and compares it with closing stock plus incoming quantities expected in that window. Overdue receipts are not optimistically counted. A one-feature logistic regression fitted **only on training** calibrates this pressure score for probability comparisons. The second baseline predicts the training shortage prevalence for everyone.

Deployment eligibility requires the model to beat coverage-baseline average precision and prevalence-baseline LogLoss in both validation and final test. There is no arbitrary accuracy threshold and no automatic relaxation on failure. This is a demo gate, not statistical evidence of operational superiority. Inspect Brier score and probability behavior before relying on the risk values; probability outputs are not a guarantee of calibration.

Changing config/inputs/code creates a new run. Repeated experimentation after viewing final-test results turns that period into development data; reserve a fresh scenario for the next genuinely independent audit.

## Deployment and application contract

The script evaluates first; `--deploy` explicitly requests deployment. It uses the chosen prediction environment, or a unique dedicated prediction server. It saves `DLA_SHORTAGE_DEPLOYMENT_ID` to the run's `runtime.env`. Import that ID into the later agent/app environment, not the token.

`predict_rows` is the future agent integration point for already-built feature rows. `build_features` is shared by training and scoring; provide complete prior history and the same definition of end-of-day observation. `score_demo.py` checks code/model identity, produces one probability per item/location, marks all output as synthetic, and fails on missing positive-class probabilities or unavailable serving. It does not infer a positive-class probability from the returned class label.

For manual local input testing:

```bash
python scripts/provision_demo_models.py --dry-run --daily-csv /path/daily.csv --orders-csv /path/orders.csv
```

This mode requires no DataRobot credentials and creates no cloud resources. For scoring a fresh synthetic history, use `score_demo.py --run-dir ... --daily-csv ... --orders-csv ...`; both input tables must remain a consistent pair.

## Scope and limitations

- Real NIINs do not make simulated stock, orders or outcomes real DLA data.
- The simulator uses abstract item units and fictional depots. No supplier/CAGE bridge, price, actual depot capability, substitution approval or transfer feasibility exists in these tables.
- Predicted shortage probability is suitable for a demonstration investigation queue. It does not estimate the causal effect of transferring or expediting stock. That requires separate explicitly stated planning assumptions.
- SDK integration is tested with mocks and installed SDK signatures; cloud training, deployment permissions and tenant-specific behavior must be checked by running in your Codespace.
- Every cloud operation uses your existing account permissions. This commit itself starts no DataRobot job.

## API references and conventions

- [DataRobot project/partitioning SDK](https://docs.datarobot.com/en/docs/api/reference/sdk/projects.html)
- [Prediction jobs](https://docs.datarobot.com/latest/en/docs/api/dev-learning/python/predictions/predict_job.html)
- [Dataset SDK](https://datarobot-public-api-client.readthedocs-hosted.com/en/early-access/data-registry.html)
- [Deployment SDK](https://datarobot-public-api-client.readthedocs-hosted.com/en/early-access/reference/mlops/deployment.html)
- [VA provisioning reference](https://github.com/lucas-torlay-datarobot/va-clinic-scheduling/blob/main/scripts/provision_demo_models.py)

The DLA package follows the VA script naming, argparse entry point, environment-ID configuration, optional deployment and Taskfile conventions. It does not copy the VA frontend, RAG setup or infrastructure scaffold into this prediction-only release.
