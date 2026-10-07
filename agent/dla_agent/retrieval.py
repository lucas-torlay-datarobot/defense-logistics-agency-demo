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
        raise RuntimeError("Retrieval requires a valid DataRobot HTTPS endpoint")
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
            raise RuntimeError(
                "Document retrieval needs DLA_RAG_DEPLOYMENT_ID. Deploy DLA_Supply_Chain_Processes and run scripts/configure_rag.py; the vector database ID is not a deployment ID."
            )
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise RuntimeError("Document search requires a question of 1–2,000 characters")
        if not re.fullmatch(r"[0-9a-fA-F]{24}", self.deployment_id):
            raise RuntimeError("DLA_RAG_DEPLOYMENT_ID must be a DataRobot deployment ID")
        endpoint = https_url(self.endpoint)
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
                    raise RuntimeError("Configured retrieval deployment is not a vector database")
                environment = dep.get("predictionEnvironment") or {}
                server = dep.get("defaultPredictionServer") or {}
                if environment.get("platform") == "datarobotServerless":
                    base = endpoint
                else:
                    base = https_url(server.get("url", "")) + "/predApi/v1.0"
                    if server.get("datarobot-key"):
                        headers["datarobot-key"] = server["datarobot-key"]
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
                raw = documents_from_response(body)
        except httpx.TimeoutException:
            raise RuntimeError(
                "Document retrieval timed out. Retry after checking the vector deployment's service health."
            ) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise RuntimeError(
                "Document retrieval returned an unsupported response or could not connect. Check the vector deployment and server credentials."
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
                raise RuntimeError(
                    f"DataRobot retrieval request failed (HTTP {response.status_code}). Check deployment access and service health."
                )
            content = bytearray()
            for block in response.iter_bytes():
                content.extend(block)
                if len(content) > 1_000_000:
                    raise RuntimeError("Document retrieval response exceeded the size limit")
            return json.loads(content)
