# Prompt-injection guard

The app calls the existing DataRobot binary detector `66561aef1922a4c0c8bcbb51` before accepting a chat turn. A flagged prompt never reaches the agent LLM, SQL planner, document retrieval, or conversation memory. Saved preferences are checked before saving and checked again before inclusion in a chat prompt. A timeout, permission error, wrong model type, missing attack probability, or malformed response pauses chat; it does not silently allow the prompt. Dashboard browsing remains available without login.

This follows DataRobot's **prompt-stage model guard with a block intervention**. The small HTTP adapter uses the deployment's prediction server (or serverless endpoint) with the existing server-side DataRobot credentials, avoiding another runtime package. It does not alter the LLM deployment's native moderation settings or create a new deployment.

## Check in your Codespace

After pulling the code, stop the app and run:

```bash
./.venv-dla/bin/python -I scripts/check_prompt_guard.py
```

The deployment ID is the built-in default. No `.env` change is required when `DATAROBOT_ENDPOINT` and `DATAROBOT_API_TOKEN` are already set. The script creates nothing; it makes two prediction requests and checks that an ordinary logistics question passes and a simple injection probe blocks. Restart the app after it passes. No frontend rebuild is needed for this guard change.

The private deployment schema cannot be verified from the repository. Defaults match DataRobot's documented example:

```dotenv
DLA_PROMPT_GUARD_DEPLOYMENT_ID=66561aef1922a4c0c8bcbb51
DLA_PROMPT_GUARD_INPUT_COLUMN=text
DLA_PROMPT_GUARD_ATTACK_LABEL=injection
DLA_PROMPT_GUARD_THRESHOLD=0.8
```

In the detector's **Prediction API** example, verify the input feature and the label in `predictionValues` representing an attack. `DLA_PROMPT_GUARD_ATTACK_LABEL` is that class label, not the model name, predicted class, or a flattened CSV column such as `injection_injection_PREDICTION`. The adapter requires `model.targetType=Binary`; a different model contract intentionally fails closed instead of guessing its meaning. Inspect the deployment API example if the check reports a schema problem.

The app blocks when the attack probability is **greater than** the threshold, matching the documentation example. `0.8` is an initial demo setting, not a calibrated operating point. Validate on representative ordinary questions and attack examples before choosing a threshold. A passing two-prompt check confirms connectivity and basic behavior, not comprehensive protection.

## Monitoring and scope

These calls are predictions on the guard deployment, so use its DataRobot service-health/prediction monitoring to inspect operation. Python logger `dla_agent.guard` emits deployment ID, score, threshold, and decision at INFO level, and failures at WARNING, without logging prompt text or API secrets. These are application gate events; this implementation does not forward custom guard metrics to the central LLM deployment.

This change checks newly submitted questions and saved preferences. It does not scan old conversation history, retrieved documents, or SQL results for indirect injection. Existing read-only SQL restrictions and treatment of documents as untrusted evidence remain in place. Detection can have false positives and missed attacks; it is an additional control, not a guarantee that all bad prompts are prevented. Rejected chat turns are shown as a notice but not persisted into model context.

References:

- [DataRobot Moderations guardrails: model guards, prompt stage, block conditions, and prompt-injection example](https://docs.datarobot.com/en/docs/api/code-first-tools/moderations-library/moderations-guardrails.html)
- [Configure native evaluation and moderation](https://docs.datarobot.com/latest/en/docs/agentic-ai/agentic-deploy/agentic-configure-evaluation-moderation.html)

## Temporarily run without the detector

Set `DLA_PROMPT_GUARD_ENABLED=false` in `.env` and restart the app. Or apply it only to one launch:

```bash
DLA_PROMPT_GUARD_ENABLED=false ./.venv-dla/bin/python -I scripts/run_app.py --host 0.0.0.0 --port 8080
```

Chat and saved preferences then skip detector requests, so its availability does not block them. SQL restrictions and the rest of the app remain unchanged. The check script reports disabled without calling DataRobot. Set the flag to `true` and restart to restore checking; the default is enabled. Only the explicit value `false` (case insensitive) disables it.
