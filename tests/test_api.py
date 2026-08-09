from __future__ import annotations

import hashlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from recalllease.api import build_service, create_app
from recalllease.settings import Settings


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(
        settings=Settings(
            environment="test",
            backend="memory",
            public_session_limit_per_hour=20,
            session_use_limit=8,
        )
    )
    with TestClient(app) as active_client:
        yield active_client


def create_session(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/demo/sessions",
        headers={"X-RecallLease-Client": "browser-v1"},
        json={},
    )
    assert response.status_code == 201
    return response.json()


def auth(session: dict[str, object]) -> dict[str, str]:
    return {"X-Demo-Token": str(session["token"])}


def test_index_has_security_headers(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "RecallLease" in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_production_refuses_ephemeral_memory() -> None:
    with pytest.raises(RuntimeError, match="Production requires"):
        create_app(settings=Settings(environment="production", backend="memory"))


def test_session_creation_rejects_cross_site_and_unmarked_clients(
    client: TestClient,
) -> None:
    cross_site = client.post(
        "/api/demo/sessions",
        headers={
            "Origin": "https://attacker.invalid",
            "Sec-Fetch-Site": "cross-site",
        },
        content="",
    )
    missing_client_header = client.post("/api/demo/sessions", json={})

    assert cross_site.status_code == 403
    assert cross_site.json() == {"detail": "Cross-site request denied"}
    assert cross_site.headers["cache-control"] == "no-store"
    assert cross_site.headers["x-frame-options"] == "DENY"
    assert missing_client_header.status_code == 422


def test_initial_permission_expires_with_the_demo_session(client: TestClient) -> None:
    session = create_session(client)

    state = client.get(
        f"/api/demo/sessions/{session['tenant_id']}",
        headers=auth(session),
    ).json()
    initial_permission = next(
        memory for memory in state["memories"] if memory["id"] == session["initial_permission_id"]
    )

    assert initial_permission["valid_until"] == session["expires_at"]


def test_cloud_backed_loopback_requires_an_ephemeral_capability() -> None:
    local_service = build_service(Settings(environment="test", backend="memory"))

    with pytest.raises(RuntimeError, match="LOOPBACK_CAPABILITY"):
        create_app(
            settings=Settings(
                environment="test",
                backend="cockroach",
                exposure_mode="loopback",
            ),
            service=local_service,
        )

    capability = "c" * 43
    app = create_app(
        settings=Settings(
            environment="test",
            backend="cockroach",
            exposure_mode="loopback",
            loopback_capability=capability,
        ),
        service=local_service,
    )
    with TestClient(app) as cloud_loopback:
        missing = cloud_loopback.post(
            "/api/demo/sessions",
            headers={"X-RecallLease-Client": "browser-v1"},
            json={},
        )
        wrong = cloud_loopback.post(
            "/api/demo/sessions",
            headers={
                "X-RecallLease-Client": "browser-v1",
                "X-RecallLease-Loopback-Capability": "w" * 43,
            },
            json={},
        )
        valid = cloud_loopback.post(
            "/api/demo/sessions",
            headers={
                "X-RecallLease-Client": "browser-v1",
                "X-RecallLease-Loopback-Capability": capability,
            },
            json={},
        )

    assert missing.status_code == 401
    assert missing.json() == {"detail": "Loopback capability required"}
    assert wrong.status_code == 401
    assert wrong.json() == {"detail": "Loopback capability required"}
    assert valid.status_code == 201


def test_iam_front_door_does_not_require_the_loopback_capability() -> None:
    local_service = build_service(Settings(environment="test", backend="memory"))
    app = create_app(
        settings=Settings(
            environment="production",
            backend="cockroach",
            exposure_mode="aws_iam",
        ),
        service=local_service,
    )

    with TestClient(app) as iam_front_door:
        response = iam_front_door.post(
            "/api/demo/sessions",
            headers={"X-RecallLease-Client": "browser-v1"},
            json={},
        )

    assert response.status_code == 201


def test_private_state_requires_the_session_token(client: TestClient) -> None:
    session = create_session(client)
    tenant_id = session["tenant_id"]

    missing = client.get(f"/api/demo/sessions/{tenant_id}")
    wrong = client.get(
        f"/api/demo/sessions/{tenant_id}",
        headers={"X-Demo-Token": "x" * 32},
    )
    valid = client.get(f"/api/demo/sessions/{tenant_id}", headers=auth(session))

    assert missing.status_code == 422
    assert wrong.status_code == 401
    assert valid.status_code == 200
    assert valid.headers["cache-control"] == "no-store"
    assert valid.json()["uses_remaining"] == 7


def test_token_cannot_cross_demo_tenants(client: TestClient) -> None:
    first = create_session(client)
    second = create_session(client)

    response = client.get(
        f"/api/demo/sessions/{second['tenant_id']}",
        headers=auth(first),
    )

    assert response.status_code == 401


def test_revocation_survives_fresh_agent_retrieval(client: TestClient) -> None:
    session = create_session(client)
    tenant_id = session["tenant_id"]
    headers = auth(session)

    revoked = client.post(
        f"/api/demo/sessions/{tenant_id}/memories",
        headers=headers,
        json={
            "kind": "permission",
            "effect": "deny",
            "subject": "Public weekly status publishing",
            "content": (
                "The agent must not publish the weekly project status. "
                "This revocation supersedes every earlier publication permission."
            ),
            "source": "demo-user:policy-update",
            "supersedes_id": session["initial_permission_id"],
        },
    )
    assert revoked.status_code == 201

    decision = client.post(
        f"/api/demo/sessions/{tenant_id}/actions",
        headers=headers,
        json={
            "action": "Publish the weekly status publicly",
            "intent": "Publish a sanitized weekly status to the public project page.",
        },
    )
    assert decision.status_code == 201
    receipt = decision.json()

    state_response = client.get(f"/api/demo/sessions/{tenant_id}", headers=headers)
    assert state_response.status_code == 200
    state = state_response.json()
    permissions = [item for item in state["memories"] if item["effect"] != "context"]

    assert receipt["decision"] == "deny"
    assert receipt["agent_instance_id"].startswith("agent-")
    assert len(receipt["retrieval_query_sha256"]) == 64
    assert len(receipt["memory_set_digest_sha256"]) == 64
    assert len(receipt["digest_sha256"]) == 64
    assert revoked.json()["id"] in receipt["recalled_memory_ids"]
    assert [item["status"] for item in permissions] == ["superseded", "active"]
    assert state["uses_remaining"] == 5
    assert state["receipts"][0]["digest_sha256"] == receipt["digest_sha256"]


def test_memory_content_hash_is_computed_server_side(client: TestClient) -> None:
    session = create_session(client)
    content = "Do not disclose private paths in the public status."

    response = client.post(
        f"/api/demo/sessions/{session['tenant_id']}/memories",
        headers=auth(session),
        json={
            "kind": "instruction",
            "effect": "deny",
            "subject": "Private path disclosure",
            "content": content,
            "source": "demo-user",
        },
    )

    assert response.status_code == 201
    assert response.json()["content_sha256"] == hashlib.sha256(content.encode()).hexdigest()


@pytest.mark.parametrize(
    "valid_from,valid_until",
    [
        ("2026-08-09T10:00:00", None),
        ("2026-08-09T10:00:00Z", "2026-08-09T09:59:59Z"),
    ],
)
def test_memory_rejects_unsafe_time_windows(
    client: TestClient,
    valid_from: str,
    valid_until: str | None,
) -> None:
    session = create_session(client)
    payload = {
        "kind": "permission",
        "effect": "allow",
        "subject": "Time boxed action",
        "content": "Allow an action only during its explicit validity window.",
        "source": "demo-user",
        "valid_from": valid_from,
    }
    if valid_until is not None:
        payload["valid_until"] = valid_until

    response = client.post(
        f"/api/demo/sessions/{session['tenant_id']}/memories",
        headers=auth(session),
        json=payload,
    )

    assert response.status_code == 422
