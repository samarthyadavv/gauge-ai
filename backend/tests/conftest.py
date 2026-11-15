import os

os.environ.setdefault(
    "ADMIN_KEY", "test-only-operator-key-with-more-than-32-characters"
)
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/gauge_test.db")
import pytest
from fastapi.testclient import TestClient
from app.db import Base, engine
from app.main import app, run_attempts, login_attempts


@pytest.fixture(autouse=True)
def isolated_db():
    if "_test" not in engine.url.database:
        raise RuntimeError("Tests require a dedicated database containing _test")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    run_attempts.clear()
    login_attempts.clear()
    yield


@pytest.fixture
def client():
    with TestClient(
        app, headers={"Authorization": "Bearer " + os.environ["ADMIN_KEY"]}
    ) as c:
        yield c


@pytest.fixture
def dataset(client):
    r = client.post(
        "/api/datasets",
        json={
            "name": "Arithmetic",
            "cases": [
                {"prompt": "Add: 2 and 2", "expected": "4", "match": "exact"},
                {"prompt": "Uppercase: secure", "expected": "SECURE", "match": "exact"},
            ],
        },
    )
    assert r.status_code == 201
    return r.json()["id"]
