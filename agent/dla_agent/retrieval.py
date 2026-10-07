"""Bounded semantic search using DataRobot's deployed VDB prediction contract.

Wire contract: datarobot-genai 0.29.63 drtools/vdb/tools.py::vdb_query.
No embedding models or document indexes run in the Codespace process.
"""

import datetime as dt
import json
import os
import re
from urllib.parse import urlparse

import httpx

DEFAULT_DATABASE_ID = "6ac695306e7d24fb54aba935"
DEFAULT_DATABASE_NAME = "DLA_Supply_Chain_Processes"


class RetrievalError(RuntimeError):
    """Safe application-authored diagnostic, without upstream bodies or secrets."""


def documents_from_response(body):
    """Accept the direct and structured prediction shapes used by the VDB tool."""
    rows = body if isinstance(body, list) else body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Unsupported retrieval response")
    documents = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid retrieval row")
        if "page_content" in row or "content" in row:
            documents.append(row)
            continue
        chunks = row.get("prediction")
        if chunks is None:
            values = row.get("predictionValues", [])
            if values and isinstance(values[0], dict):
                chunks = values[0].get("value")
        if not isinstance(chunks, list):
            raise ValueError("Unsupported retrieval prediction")
        for chunk in chunks:
            if isinstance(chunk, str):
                documents.append({"page_content": chunk})
            elif isinstance(chunk, dict):
                documents.append(chunk)
            else:
                raise ValueError("Invalid retrieval document")
    return documents


def https_url(value):
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RetrievalError("Retrieval requires a valid DataRobot HTTPS endpoint")
    return value.rstrip("/")


class DataRobotRetriever:
    def __init__(self):
        self.database_id = os.getenv("DLA_RAG_VECTOR_DATABASE_ID") or DEFAULT_DATABASE_ID
        self.name = os.getenv("DLA_RAG_VECTOR_DATABASE_NAME") or DEFAULT_DATABASE_NAME
        self.deployment_id = os.getenv("DLA_RAG_DEPLOYMENT_ID", "").strip()
        self.endpoint = os.getenv("DATAROBOT_ENDPOINT", "").rstrip("/")
        self.token = os.getenv("DATAROBOT_API_TOKEN", "")
        self.top_k = 5

    @property
    def configured(self):
        return bool(self.deployment_id and self.endpoint and self.token)

    def info(self):
        return {
            "configured": self.configured,
            "name": self.name,
            "vector_database_id": self.database_id,
            "deployment_id": self.deployment_id,
            "top_k": self.top_k,
        }

    def retrieve(self, query):
        if not self.configured:
            missing = [
                key
                for key, value in {
                    "DLA_RAG_DEPLOYMENT_ID": self.deployment_id,
                    "DATAROBOT_ENDPOINT": self.endpoint,
                    "DATAROBOT_API_TOKEN": self.token,
                }.items()
                if not value
            ]
            raise RetrievalError(
                "Missing retrieval configuration: "
                + ", ".join(missing)
                + ". Set these in the server environment or .env."
            )
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise RetrievalError("Document search requires a question of 1–2,000 characters")
        if not re.fullmatch(r"[0-9a-fA-F]{24}", self.deployment_id):
            raise RetrievalError("DLA_RAG_DEPLOYMENT_ID must be a DataRobot deployment ID")
        endpoint = https_url(self.endpoint)
        stage = "deployment metadata lookup"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(60, connect=10), follow_redirects=False
            ) as client:
                headers = {"Authorization": f"Bearer {self.token}"}
                dep = self._json(
                    client, "GET", f"{endpoint}/deployments/{self.deployment_id}/", headers=headers
                )
                model = dep.get("model") or {}
                capabilities = dep.get("capabilities") or {}
                if model.get("targetType") != "VectorDatabase" and not capabilities.get(
                    "supportsVectorDatabaseQuerying"
                ):
                    raise RetrievalError("Configured retrieval deployment is not a vector database")
                stage = "prediction endpoint resolution"
                environment = dep.get("predictionEnvironment") or {}
                server = dep.get("defaultPredictionServer") or {}
                if environment.get("platform") == "datarobotServerless":
                    base = endpoint
                else:
                    base = https_url(server.get("url", "")) + "/predApi/v1.0"
                    if server.get("datarobot-key"):
                        headers["datarobot-key"] = server["datarobot-key"]
                stage = "vector search request"
                body = self._json(
                    client,
                    "POST",
                    f"{base}/deployments/{self.deployment_id}/predictions",
                    headers=headers,
                    json=[
                        {
                            "promptText": query.strip(),
                            "num_results": self.top_k,
                            "retrieval_mode": "similarity",
                        }
                    ],
                )
                stage = "vector search response parsing"
                raw = documents_from_response(body)
        except httpx.TimeoutException:
            raise RetrievalError(
                f"Retrieval timed out during {stage}. Check deployment service health and retry."
            ) from None
        except httpx.HTTPError as exc:
            raise RetrievalError(
                f"Retrieval connection failed during {stage} ({type(exc).__name__}). Check network access and the DataRobot endpoint."
            ) from None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise RetrievalError(
                f"Retrieval failed during {stage}: unsupported response format. Check the vector deployment prediction API schema."
            ) from None
        documents, seen, budget = [], set(), 16000
        for doc in raw:
            content = doc.get("page_content") or doc.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            metadata = doc.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}
            source = (
                metadata.get("source")
                or metadata.get("document_file_path")
                or doc.get("source")
                or "Source not supplied"
            )
            key = (str(source), content)
            if key in seen:
                continue
            seen.add(key)
            excerpt = content[: min(4000, budget)]
            # Keep provenance fields only; never expose arbitrary metadata or HTML links.
            safe_meta = {
                k: str(metadata[k])[:500]
                for k in ("title", "page", "page_number", "section", "revision", "date", "chunk_id")
                if metadata.get(k) is not None
            }
            documents.append(
                {
                    "id": f"D{len(documents) + 1}",
                    "source": str(source)[:500],
                    "text": excerpt,
                    "metadata": safe_meta,
                    "truncated": len(excerpt) < len(content),
                }
            )
            budget -= len(excerpt)
            if len(documents) >= self.top_k or budget <= 0:
                break
        return {
            **self.info(),
            "query": query,
            "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "model_id": (dep.get("model") or {}).get("id"),
            "documents": documents,
            "status": "ok" if documents else "empty",
        }

    @staticmethod
    def _json(client, method, url, **kwargs):
        # Bound the wire response as well as the context sent to the LLM.
        with client.stream(method, url, **kwargs) as response:
            if response.status_code != 200:
                raise RetrievalError(
                    f"DataRobot retrieval {'deployment lookup' if method == 'GET' else 'vector search'} failed (HTTP {response.status_code}). Check deployment access and service health."
                )
            content = bytearray()
            for block in response.iter_bytes():
                content.extend(block)
                if len(content) > 1_000_000:
                    raise RetrievalError("Document retrieval response exceeded the size limit")
            return json.loads(content)
