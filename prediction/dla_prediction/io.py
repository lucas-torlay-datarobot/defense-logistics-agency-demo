"""Snapshot discovery, local checkpoints and SDK registration."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv


def log(message):
    print(f"[prediction] {message}", flush=True)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n")
    temporary.replace(path)


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect(root):
    import datarobot as dr

    load_dotenv(root / ".env", override=False)
    # Supports Codespace environment/config credentials and ordinary .env credentials.
    options = {}
    if os.getenv("DATAROBOT_API_TOKEN"):
        options["token"] = os.environ["DATAROBOT_API_TOKEN"]
    if os.getenv("DATAROBOT_ENDPOINT"):
        options["endpoint"] = os.environ["DATAROBOT_ENDPOINT"]
    client = dr.Client(**options)
    client.get("version/")
    return dr, client


def resolve_use_case(dr, client, use_case_id, name):
    if use_case_id:
        return dr.UseCase.get(use_case_id)
    matches, offset = [], 0
    while True:
        payload = client.get(
            "useCases/", params={"search": name, "limit": 100, "offset": offset}
        ).json()
        rows = payload.get("data", payload.get("items"))
        if not isinstance(rows, list):
            raise RuntimeError("Unexpected Use Case response")
        matches.extend(r for r in rows if r.get("name") == name)
        offset += len(rows)
        if len(rows) < 100:
            break
    if len(matches) != 1:
        raise ValueError("Set DLA_USE_CASE_ID: expected one exact existing Use Case match")
    return dr.UseCase.get(matches[0]["id"])


def resolve_sources(dr, use_case_id, daily_id=None, orders_id=None):
    datasets = list(dr.Dataset.iterate(use_cases=[use_case_id]))

    def choose(explicit, prefix):
        matches = (
            [dr.Dataset.get(explicit)]
            if explicit
            else [d for d in datasets if (d.name or "").startswith(prefix)]
        )
        if len(matches) != 1:
            raise ValueError(
                f"Set explicit dataset IDs; matches for {prefix}: {[(d.id, d.name) for d in matches]}"
            )
        d = dr.Dataset.get(matches[0].id)
        if str(d.processing_state).upper() != "COMPLETED":
            raise ValueError(f"Dataset not ready: {d.id}")
        return d

    return {
        "daily": choose(daily_id, "SYNTHETIC_daily_inventory_demand__"),
        "orders": choose(orders_id, "SYNTHETIC_replenishment_orders__"),
    }


def download_sources(dr, datasets, cache):
    result, provenance = {}, {}
    for kind, dataset in datasets.items():
        version = str(dataset.version_id)
        path = cache / f"{dataset.id}_{version}.csv"
        marker = path.with_suffix(".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or not marker.exists():
            log(f"Downloading {kind}: {dataset.name}")
            temporary = path.with_suffix(".downloading")
            with temporary.open("wb") as handle:
                dataset.get_file(filelike=handle)
            if str(dr.Dataset.get(dataset.id).version_id) != version:
                temporary.unlink(missing_ok=True)
                raise RuntimeError("Source version changed during download; rerun")
            temporary.replace(path)
            atomic_json(marker, {"sha256": digest_file(path)})
        sha = digest_file(path)
        if sha != json.loads(marker.read_text())["sha256"]:
            raise RuntimeError(f"Cached data changed: {path}; remove this cache entry and rerun")
        result[kind] = path
        provenance[kind] = {"dataset_id": dataset.id, "version_id": version, "sha256": sha}
    return result, provenance


def read_inputs(paths):
    return tuple(
        pd.read_csv(
            paths[kind],
            dtype={
                "niin": str,
                "nsn": str,
                "fsc": str,
                "simulation_id": str,
                "source_items_dataset_id": str,
                "source_items_version_id": str,
            },
        )
        for kind in ("daily", "orders")
    )


def register_csv(dr, client, use_case, path, name, state, save):
    from datarobot.utils.waiters import wait_for_async_resolution

    record = state.setdefault("training_dataset", {})
    if record.get("id"):
        dataset = dr.Dataset.get(record["id"])
    else:
        matches = [d for d in dr.Dataset.iterate() if d.name in {name, name + ".csv"}]
        if len(matches) > 1:
            raise RuntimeError("Duplicate training dataset names; resolve IDs before rerunning")
        if matches:
            dataset = matches[0]
        else:
            if not record.get("location"):
                if record.get("stage") == "uploading":
                    raise RuntimeError(
                        "Previous training upload outcome is uncertain; inspect DataRobot before resetting state"
                    )
                record["stage"] = "uploading"
                save()
                with path.open("rb") as handle:
                    response = client.build_request_with_file(
                        fname=name + ".csv",
                        filelike=handle,
                        url="datasets/fromFile/",
                        read_timeout=3600,
                        method="post",
                    )
                record["location"] = response.headers["Location"]
                save()
            location = wait_for_async_resolution(client, record["location"], 7200)
            dataset = dr.Dataset.from_location(location)
        record["id"] = dataset.id
        save()
    import time

    deadline = time.monotonic() + 7200
    while True:
        dataset.update()
        status = str(dataset.processing_state).upper()
        if status == "COMPLETED":
            break
        if status in {"ERROR", "FAILED", "ABORTED", "CANCELLED"}:
            raise RuntimeError(f"Training dataset failed: {status}")
        if time.monotonic() > deadline:
            raise TimeoutError("Dataset still processing; rerun to resume")
        time.sleep(15)
    if dataset.id not in {d.id for d in dr.Dataset.iterate(use_cases=[use_case.id])}:
        use_case.add(entity=dataset)
    record.update(stage="registered", version_id=str(dataset.version_id))
    save()
    return dataset
