from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from carrier_pool.main import app, database_session


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


def test_load_api_rejects_invalid_tenant_context() -> None:
    assert TestClient(app).get(f"/api/v1/loads/{uuid4()}").status_code == 400


def test_load_api_returns_same_not_found_for_absent_or_other_tenant() -> None:
    app.dependency_overrides[database_session] = lambda: FakeSession(None)
    client = TestClient(app)
    headers = {"X-Tenant-ID": str(uuid4())}
    assert client.get(f"/api/v1/loads/{uuid4()}", headers=headers).json() == {
        "detail": "Load not found."
    }
    app.dependency_overrides.clear()


def test_load_api_returns_own_tenant_current_load() -> None:
    tenant_id, load_id = uuid4(), uuid4()
    load = SimpleNamespace(id=load_id, status=SimpleNamespace(value="ACTIVE"), equipment=None)
    app.dependency_overrides[database_session] = lambda: FakeSession(load)
    response = TestClient(app).get(
        f"/api/v1/loads/{load_id}", headers={"X-Tenant-ID": str(tenant_id)}
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["id"] == str(load_id)
