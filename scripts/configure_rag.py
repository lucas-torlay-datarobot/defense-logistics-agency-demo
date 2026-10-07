#!/usr/bin/env python
"""Connect an existing VDB deployment, or explicitly deploy the configured index once."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))


def main():
    from dla_agent.retrieval import DEFAULT_DATABASE_ID, DataRobotRetriever
    from dotenv import load_dotenv, set_key

    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--deployment-id", help="Existing deployment of this vector database")
    actions.add_argument(
        "--deploy",
        action="store_true",
        help="Create a retrieval deployment (consumes DataRobot compute)",
    )
    parser.add_argument(
        "--prediction-environment-id", default=os.getenv("DLA_PREDICTION_ENVIRONMENT_ID")
    )
    parser.add_argument("--prediction-server-id")
    parser.add_argument(
        "--query", default="What guidance applies when a replenishment order is overdue?"
    )
    args = parser.parse_args()
    state_path = ROOT / "artifacts/rag/deployment.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    database_id = os.getenv("DLA_RAG_VECTOR_DATABASE_ID") or DEFAULT_DATABASE_ID
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state and state.get("vector_database_id") != database_id:
        parser.error(
            "Saved retrieval setup belongs to another vector database. Use a separate checkout or review artifacts/rag/deployment.json first."
        )
    deployment_id = (
        args.deployment_id or os.getenv("DLA_RAG_DEPLOYMENT_ID") or state.get("deployment_id")
    )
    if not deployment_id and args.deploy:
        if state.get("status") == "submitted":
            parser.error(
                "A previous deployment attempt may still be running. Find it in DataRobot Console and rerun with --deployment-id; this command will not create a duplicate."
            )
        if not args.prediction_environment_id and not args.prediction_server_id:
            parser.error(
                "Choose --prediction-environment-id or --prediction-server-id (or set DLA_PREDICTION_ENVIRONMENT_ID). Alternatively deploy the vector database in the UI and pass --deployment-id."
            )
        import datarobot as dr
        from datarobot.models.genai.vector_database import VectorDatabase

        dr.Client()
        vdb = VectorDatabase.get(database_id)
        if str(vdb.execution_status).lower() != "completed":
            parser.error("Vector database build is not completed")
        if not hasattr(vdb, "deploy"):
            parser.error(
                "This DataRobot SDK lacks VectorDatabase.deploy. Deploy this version in the DataRobot UI, then pass --deployment-id."
            )
        state = {"vector_database_id": database_id, "status": "submitted"}
        state_path.write_text(json.dumps(state, indent=2))
        print("Deploying the existing vector database. Leave this command running.", flush=True)
        kwargs = (
            {"prediction_environment_id": args.prediction_environment_id}
            if args.prediction_environment_id
            else {"default_prediction_server_id": args.prediction_server_id}
        )
        deployment_id = vdb.deploy(**kwargs).id
    if not deployment_id:
        parser.error(
            "Deploy DLA_Supply_Chain_Processes in DataRobot, then use --deployment-id ID; or use --deploy with a prediction environment."
        )
    if not re.fullmatch(r"[0-9a-fA-F]{24}", deployment_id):
        parser.error("Deployment ID must contain 24 hexadecimal characters")
    state.update(vector_database_id=database_id, deployment_id=deployment_id, status="configured")
    state_path.write_text(json.dumps(state, indent=2))
    # Write only non-secret RAG settings. Preserve the app token and LLM configuration.
    for key, value in {
        "DLA_RAG_VECTOR_DATABASE_ID": database_id,
        "DLA_RAG_DEPLOYMENT_ID": deployment_id,
    }.items():
        set_key(str(ROOT / ".env"), key, value)
        os.environ[key] = value
    print(f"Saved DLA_RAG_DEPLOYMENT_ID={deployment_id}. Testing retrieval…", flush=True)
    result = DataRobotRetriever().retrieve(args.query)
    print(f"Retrieved {len(result['documents'])} passages from the configured deployment.")
    for document in result["documents"]:
        print(f"[{document['id']}] {document['source']}: {document['text'][:240]}")
    print(
        "Restart the app to load these settings. No prediction retraining or snapshot refresh is needed."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # SDK exceptions may contain headers or response bodies. Don't print them.
        print(
            "RAG setup/check failed. Any returned deployment ID was saved in artifacts/rag/deployment.json. Check DataRobot Console for deployment status/access and retry with --deployment-id. Do not blindly create another deployment.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
