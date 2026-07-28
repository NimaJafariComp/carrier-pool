from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from carrier_pool.demo import DEMO_TENANTS
from carrier_pool.domain.types import LoadStatus
from carrier_pool.main import app, database_session

DEMO_TENANT_ID = DEMO_TENANTS[0].id


class FakeSession:
    def __init__(self, result: object | None) -> None:
        self.result = result

    def execute(self, *args: object, **kwargs: object) -> None:
        pass

    def scalar(self, *args: object, **kwargs: object) -> object | None:
        return self.result

    def scalars(self, *args: object, **kwargs: object) -> "FakeSession":
        return self

    def all(self) -> list[object]:
        return []


class SequenceSession(FakeSession):
    def __init__(self, *results: object | None) -> None:
        super().__init__(None)
        self._results = iter(results)

    def scalar(self, *args: object, **kwargs: object) -> object | None:
        return next(self._results)


def test_load_api_rejects_invalid_tenant_context() -> None:
    assert TestClient(app).get(f"/api/v1/loads/{uuid4()}").status_code == 400


def test_readiness_requires_a_database_connection(monkeypatch) -> None:
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, *_args: object) -> None:
            return None

    class Engine:
        def connect(self) -> Connection:
            return Connection()

        def dispose(self) -> None:
            return None

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test")
    monkeypatch.setattr("carrier_pool.main.create_engine", lambda _url: Engine())

    assert TestClient(app).get("/ready").json() == {"status": "ok"}


def test_load_api_returns_same_not_found_for_absent_or_other_tenant() -> None:
    app.dependency_overrides[database_session] = lambda: FakeSession(None)
    client = TestClient(app)
    headers = {"X-Tenant-ID": str(DEMO_TENANT_ID)}
    assert client.get(f"/api/v1/loads/{uuid4()}", headers=headers).json() == {
        "detail": "Load not found."
    }
    app.dependency_overrides.clear()


def test_load_api_returns_own_tenant_current_load() -> None:
    tenant_id, load_id = DEMO_TENANT_ID, uuid4()
    load = SimpleNamespace(
        id=load_id,
        external_id="demo-load",
        status=SimpleNamespace(value="ACTIVE"),
        equipment=None,
        distance_miles=None,
        current_version_id=None,
        observed_at=datetime(2026, 7, 11, tzinfo=UTC),
    )
    app.dependency_overrides[database_session] = lambda: FakeSession(load)
    response = TestClient(app).get(
        f"/api/v1/loads/{load_id}", headers={"X-Tenant-ID": str(tenant_id)}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["id"] == str(load_id)


def test_mandatory_phase_11_routes_are_exported() -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert {
        "/api/v1/tenants",
        "/api/v1/loads",
        "/api/v1/loads/{load_id}",
        "/api/v1/loads/{load_id}/decision",
    } <= set(paths)


def test_decision_api_has_stable_inactive_and_not_computed_responses() -> None:
    tenant_id, load_id = DEMO_TENANT_ID, uuid4()
    inactive = SimpleNamespace(status=LoadStatus.COMPLETED)
    app.dependency_overrides[database_session] = lambda: SequenceSession(inactive)
    client = TestClient(app)
    assert client.get(
        f"/api/v1/loads/{load_id}/decision", headers={"X-Tenant-ID": str(tenant_id)}
    ).json() == {"detail": "Load is not active."}
    active = SimpleNamespace(
        id=load_id,
        status=LoadStatus.ACTIVE,
        current_version_id=uuid4(),
    )
    app.dependency_overrides[database_session] = lambda: SequenceSession(active, None)
    assert client.get(
        f"/api/v1/loads/{load_id}/decision", headers={"X-Tenant-ID": str(tenant_id)}
    ).json() == {"detail": "Decision not computed."}
    app.dependency_overrides.clear()
