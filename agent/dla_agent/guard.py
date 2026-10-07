"""Prompt-stage blocking via a deployed DataRobot binary injection classifier.

Defaults follow DataRobot's Moderations guardrails example (text / injection).
The deployment's actual schema must be verified using check_prompt_guard.py.
"""

import json
import logging
import math
import os
import re

import httpx

from .retrieval import https_url

DEFAULT_DEPLOYMENT = "66561aef1922a4c0c8bcbb51"
logger = logging.getLogger(__name__)


class GuardUnavailable(RuntimeError):
    pass


class GuardHTTPError(RuntimeError):
    def __init__(self, status, body):
        self.status, self.body = status, body


class PromptBlocked(RuntimeError):
    pass


class DataRobotPromptGuard:
    def __init__(self):
        self.deployment_id = os.getenv("DLA_PROMPT_GUARD_DEPLOYMENT_ID") or DEFAULT_DEPLOYMENT
        self.endpoint = os.getenv("DATAROBOT_ENDPOINT", "").rstrip("/")
        self.token = os.getenv("DATAROBOT_API_TOKEN", "")
        self.input_column = os.getenv("DLA_PROMPT_GUARD_INPUT_COLUMN", "text")
        self.attack_label = os.getenv("DLA_PROMPT_GUARD_ATTACK_LABEL", "injection")
        try:
            self.threshold = float(os.getenv("DLA_PROMPT_GUARD_THRESHOLD", "0.8"))
        except ValueError:
            self.threshold = float("nan")

    def score(self, text):
        stage = "configuration"
        try:
            if not (self.token and self.input_column and self.attack_label):
                raise ValueError("Missing guard configuration")
            if not re.fullmatch(r"[a-fA-F0-9]{24}", self.deployment_id):
                raise ValueError("Invalid deployment ID")
            if not math.isfinite(self.threshold) or not 0 <= self.threshold <= 1:
                raise ValueError("Invalid threshold")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Empty input")
            endpoint = https_url(self.endpoint)
            with httpx.Client(
                timeout=httpx.Timeout(30, connect=10), follow_redirects=False
            ) as client:
                headers = {"Authorization": f"Bearer {self.token}"}
                stage = "deployment lookup"
                deployment = self._json(
                    client, "GET", f"{endpoint}/deployments/{self.deployment_id}/", headers=headers
                )
                if (deployment.get("model") or {}).get("targetType") != "Binary":
                    raise ValueError("Expected binary classifier")
                server = deployment.get("defaultPredictionServer") or {}
                if (deployment.get("predictionEnvironment") or {}).get(
                    "platform"
                ) == "datarobotServerless":
                    base = endpoint
                else:
                    base = https_url(server.get("url", "")) + "/predApi/v1.0"
                    if server.get("datarobot-key"):
                        headers["datarobot-key"] = server["datarobot-key"]
                stage = "prediction"
                body = self._json(
                    client,
                    "POST",
                    f"{base}/deployments/{self.deployment_id}/predictions",
                    headers=headers,
                    json=[{self.input_column: text}],
                )
                stage = "score parsing (verify the configured attack label)"
                rows = body["data"]
                if not isinstance(rows, list) or len(rows) != 1:
                    raise ValueError("Expected one prediction")
                values = rows[0]["predictionValues"]
                scores = [p["value"] for p in values if p["label"] == self.attack_label]
                if len(scores) != 1 or isinstance(scores[0], bool):
                    raise ValueError("Missing or ambiguous attack probability")
                score = float(scores[0])
                if not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError("Invalid probability")
                return score
        except Exception as exc:
            # Never return/log upstream payloads, prompts, authorization headers or URLs.
            logger.warning(
                "prompt_guard unavailable stage=%s error_type=%s", stage, type(exc).__name__
            )
            status = f" (HTTP {exc.status})" if isinstance(exc, GuardHTTPError) else ""
            error = GuardUnavailable(
                f"Prompt safety check unavailable during {stage}{status}. No agent processing occurred. Run scripts/check_prompt_guard.py in the Codespace."
            )
            if isinstance(exc, GuardHTTPError):
                # Only the operator check script prints this diagnostic. Runtime chat
                # receives str(error), never the upstream body.
                detail = exc.body
                for secret in (self.token, locals().get("headers", {}).get("datarobot-key"), text):
                    if secret:
                        detail = detail.replace(secret, "[redacted]")
                detail = re.sub(r"https?://[^\s\"<>]+", "[URL]", detail)
                detail = "".join(c for c in detail if c.isprintable() or c in "\n\t")
                error.diagnostic = detail[:2500]
            raise error from None

    def check(self, text):
        score = self.score(text)
        blocked = score > self.threshold
        logger.info(
            "prompt_guard deployment=%s score=%.4f threshold=%.4f blocked=%s",
            self.deployment_id,
            score,
            self.threshold,
            blocked,
        )
        if blocked:
            raise PromptBlocked(
                "This request was blocked by the prompt-injection guard. Rephrase it as a logistics question without instructions to bypass the assistant’s rules."
            )
        return score

    @staticmethod
    def _json(client, method, url, **kwargs):
        with client.stream(method, url, **kwargs) as response:
            content = bytearray()
            for block in response.iter_bytes():
                content.extend(block)
                if len(content) > 100_000:
                    raise ValueError("Guard response too large")
            if response.status_code != 200:
                # Prefer structured error details over HTML error pages.
                try:
                    body = json.loads(content)
                    if isinstance(body, dict):
                        detail = {
                            k: body[k]
                            for k in ("message", "error", "errors", "detail")
                            if k in body
                        }
                        body = (
                            json.dumps(detail) if detail else "No structured error detail supplied."
                        )
                    else:
                        body = "No structured error detail supplied."
                except ValueError:
                    body = "Non-JSON error response. Check deployment service health."
                raise GuardHTTPError(response.status_code, body)
            return json.loads(content)
