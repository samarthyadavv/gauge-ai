import json, os, uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app.main import app
from app.db import Session, Dataset, Run, Result, now
from app.grading import grade, percentile
from app.worker import claim, execute, recover_stale


def start(client, dataset, name="Baseline", key=None):
    return client.post(
        "/api/runs",
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
        json={"dataset_id": dataset, "name": name},
    )


def test_authentication_and_session_csrf(client, dataset):
    c = TestClient(app)
    assert c.get("/api/datasets").status_code == 401
    assert c.post("/api/session", json={"key": "wrong"}).status_code == 401
    login = c.post("/api/session", json={"key": os.environ["ADMIN_KEY"]})
    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    assert c.get("/api/me").status_code == 200
    assert (
        c.post(
            "/api/runs",
            json={"dataset_id": dataset, "name": "bad"},
            headers={"Idempotency-Key": "csrf-test"},
        ).status_code
        == 403
    )
    csrf = login.json()["csrf"]
    assert c.delete("/api/session", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert c.get("/api/me").status_code == 401


def test_origin_and_body_limits(client):
    assert (
        client.post(
            "/api/datasets",
            json={"name": "bad", "cases": []},
            headers={"Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    assert client.post("/api/datasets", content=b"a" * 1000001).status_code == 413


def test_dataset_validation_and_fingerprint(client):
    payload = {
        "name": "Sample",
        "cases": [{"prompt": "x", "expected": "y", "match": "exact"}],
    }
    a = client.post("/api/datasets", json=payload)
    b = client.post("/api/datasets", json=payload)
    assert a.json()["fingerprint"] == b.json()["fingerprint"]
    for cases in [
        [],
        [{"prompt": "x", "expected": "not json", "match": "json"}],
        [{"prompt": "x", "expected": "y", "forbidden": [""]}],
    ]:
        assert (
            client.post(
                "/api/datasets", json={"name": "invalid", "cases": cases}
            ).status_code
            == 422
        )


def test_complete_worker_flow_and_metrics(client, dataset):
    rid = start(client, dataset).json()["id"]
    assert claim() == rid
    execute(rid)
    r = client.get("/api/runs/" + rid).json()
    assert r["status"] == "completed" and r["summary"]["pass_rate"] == 1
    assert r["summary"]["completed"] == 2 and len(r["results"]) == 2
    assert r["summary"]["cost_usd"] == 0 and r["summary"]["output_tokens"] == 0
    assert r["summary"]["p95_ms"] >= 0
    assert all(x["passed"] for x in r["results"])


def test_immutable_run_snapshot(client, dataset):
    rid = start(client, dataset).json()["id"]
    with Session.begin() as s:
        s.get(Dataset, dataset).cases = [
            {
                "prompt": "Add: 2 and 2",
                "expected": "wrong",
                "match": "exact",
                "forbidden": [],
            }
        ]
    assert claim() == rid
    execute(rid)
    assert client.get("/api/runs/" + rid).json()["summary"]["passed"] == 2


def test_concurrent_idempotent_submission(client, dataset):
    key = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: start(client, dataset, key=key), range(6)))
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert start(client, dataset, name="Changed", key=key).status_code == 409
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(Run)) == 1


def test_concurrent_claims_do_not_duplicate(client, dataset):
    expected = {start(client, dataset, name=str(i)).json()["id"] for i in range(4)}
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = [x for x in pool.map(lambda _: claim(), range(8)) if x]
    assert len(claimed) == len(set(claimed))
    # Races may lose an atomic claim and poll again; drain remaining jobs.
    while rid := claim():
        claimed.append(rid)
    assert set(claimed) == expected and len(claimed) == 4


def test_queued_and_running_cancellation(client, dataset):
    rid = start(client, dataset).json()["id"]
    assert client.post("/api/runs/" + rid + "/cancel").json()["status"] == "cancelled"
    assert claim() is None
    rid = start(client, dataset, name="Cancel during generation").json()["id"]
    claim()
    from app.provider import generate

    def stop_after_first(run, prompt):
        with Session.begin() as s:
            s.get(Run, rid).cancel_requested = True
        return generate(run, prompt)

    with patch("app.worker.generate", side_effect=stop_after_first):
        execute(rid)
    data = client.get("/api/runs/" + rid).json()
    assert data["status"] == "cancelled" and data["summary"]["completed"] == 1


def test_interrupted_jobs_are_not_retried(client, dataset):
    rid = start(client, dataset).json()["id"]
    claim()
    with Session.begin() as s:
        s.get(Run, rid).heartbeat = now() - timedelta(minutes=4)
    recover_stale()
    assert client.get("/api/runs/" + rid).json()["status"] == "interrupted"
    assert claim() is None


def test_provider_failure_hides_error_body_and_marks_unknown_usage(client, dataset):
    rid = start(client, dataset).json()["id"]
    claim()
    with patch(
        "app.worker.generate", side_effect=RuntimeError("secret-key-and-private-prompt")
    ):
        execute(rid)
    data = client.get("/api/runs/" + rid).json()
    assert data["summary"]["errors"] == 2 and data["summary"]["pass_rate"] == 0
    assert (
        not data["summary"]["usage_complete"] and not data["summary"]["cost_complete"]
    )
    assert "secret-key" not in json.dumps(data)


def test_comparison_and_dataset_compatibility(client, dataset):
    ids = []
    for name in ["a", "b"]:
        rid = start(client, dataset, name).json()["id"]
        claim()
        execute(rid)
        ids.append(rid)
    c = client.get(
        "/api/compare", params={"baseline": ids[0], "candidate": ids[1]}
    ).json()
    assert c["regressions"] == c["improvements"] == 0 and c["pass_rate_delta"] == 0
    other = client.post(
        "/api/datasets",
        json={"name": "Different", "cases": [{"prompt": "x", "expected": "y"}]},
    ).json()["id"]
    rid = start(client, other).json()["id"]
    claim()
    execute(rid)
    assert (
        client.get(
            "/api/compare", params={"baseline": ids[0], "candidate": rid}
        ).status_code
        == 409
    )


def test_models_are_allowlisted_and_prices_validated(client, dataset):
    for args in [
        {"provider": "openai", "model": "unconfigured"},
        {"provider": "mock", "model": "https://evil.test"},
        {"input_price": -1},
        {"max_tokens": 1001},
    ]:
        r = client.post(
            "/api/runs",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={"dataset_id": dataset, "name": "Invalid", **args},
        )
        assert r.status_code == 422


def test_grading_and_percentiles():
    assert grade("Answer: YES", {"expected": "yes", "match": "contains"})["passed"]
    assert not grade("Yes", {"expected": "yes", "match": "exact"})["passed"]
    assert grade('{"b":2,"a":1}', {"expected": '{"a":1,"b":2}', "match": "json"})[
        "passed"
    ]
    assert not grade("not json", {"expected": "{}", "match": "json"})["passed"]
    assert grade(
        "correct but SECRET",
        {"expected": "correct", "match": "contains", "forbidden": ["secret"]},
    ) == {"correct": True, "forbidden_hit": True, "passed": False}
    assert percentile([10, 20, 30, 40], 0.95) == 38.5
    assert percentile([], 0.95) is None


def test_comparison_detects_actual_regression(client, dataset):
    baseline = start(client, dataset).json()["id"]
    claim()
    execute(baseline)
    candidate = start(client, dataset, name="Regression").json()["id"]
    claim()
    from app.provider import generate

    def degraded(run, prompt):
        result = generate(run, prompt)
        if prompt.startswith("Add:"):
            result["output"] = "5"
        return result

    with patch("app.worker.generate", side_effect=degraded):
        execute(candidate)
    c = client.get(
        "/api/compare", params={"baseline": baseline, "candidate": candidate}
    ).json()
    assert (
        c["regressions"] == 1
        and c["improvements"] == 0
        and c["pass_rate_delta"] == -0.5
    )


def test_real_provider_adapter_usage_and_cost_without_network(client, dataset):
    from app.main import cfg

    with patch.object(cfg, "openai_api_key", "test-only-provider-key"):
        r = client.post(
            "/api/runs",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={
                "dataset_id": dataset,
                "name": "Provider adapter",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "input_price": 0.5,
                "output_price": 1.5,
            },
        )
        assert r.status_code == 201
        rid = r.json()["id"]
        claim()
        with patch("app.provider.httpx.Client") as client_class:
            response = (
                client_class.return_value.__enter__.return_value.post.return_value
            )
            response.json.return_value = {
                "choices": [{"message": {"content": "4"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }
            execute(rid)
            requests = (
                client_class.return_value.__enter__.return_value.post.call_args_list
            )
            assert len(requests) == 2
            assert all(
                "SECURE" not in json.dumps(r.kwargs["json"]["messages"])
                for r in requests
            )
        report = client.get("/api/runs/" + rid).json()
        assert (
            report["summary"]["input_tokens"] == 20
            and report["summary"]["output_tokens"] == 8
        )
        assert report["summary"]["cost_usd"] == 0.000022
        assert (
            report["summary"]["usage_complete"] and report["summary"]["cost_complete"]
        )


def test_json_types_and_invalid_constants():
    assert not grade('{"valid":1}', {"expected": '{"valid":true}', "match": "json"})[
        "passed"
    ]
    assert not grade("[true]", {"expected": "[1]", "match": "json"})["passed"]
    assert not grade("NaN", {"expected": "null", "match": "json"})["passed"]
    assert not grade('{"x":1,"x":2}', {"expected": '{"x":2}', "match": "json"})[
        "passed"
    ]
    assert grade('{"x":1.0}', {"expected": '{"x":1}', "match": "json"})["passed"]


def test_json_numeric_precision_and_nesting():
    assert not grade("1e999", {"expected": "2e999", "match": "json"})["passed"]
    assert not grade("1.000000000000000000001", {"expected": "1", "match": "json"})[
        "passed"
    ]
    assert not grade(
        "[" * 1200 + "0" + "]" * 1200, {"expected": "[]", "match": "json"}
    )["passed"]
