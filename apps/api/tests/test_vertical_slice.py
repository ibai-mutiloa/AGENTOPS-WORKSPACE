import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.seed_demo import seed
from app.db.session import get_session
from app.integrations.mock_email import EmailDeliveryFailure, MockEmailAdapter
from app.main import app
from app.tools.repository import BusinessTools, ToolFailure

PASSWORD = "synthetic-test-password-123"


@pytest.fixture(scope="module")
def demo() -> Iterator[dict[str, Any]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        organizations = seed(session, PASSWORD)

    def test_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite://",
        jwt_secret=SecretStr("synthetic-jwt-secret-for-tests-only-123456789"),
        _env_file=None,
    )
    with TestClient(app) as client:
        tokens = {}
        for role, email in {
            "operator": "operator@demo-industrial.example",
            "supervisor": "supervisor@demo-industrial.example",
            "technical": "technical-admin@demo-industrial.example",
            "other": "operator@demo-fluid.example",
        }.items():
            response = client.post("/auth/token", json={"email": email, "password": PASSWORD})
            assert response.status_code == 200, response.text
            tokens[role] = response.json()["access_token"]
        yield {"client": client, "organizations": organizations, "tokens": tokens, "engine": engine}
    app.dependency_overrides.clear()
    engine.dispose()


def headers(
    demo: dict[str, Any], role: str = "operator", other_org: bool = False
) -> dict[str, str]:
    name = "Demo Fluid Systems" if other_org else "Demo Industrial Components"
    return {
        "Authorization": f"Bearer {demo['tokens'][role]}",
        "X-Organization-ID": demo["organizations"][name],
    }


def create(demo: dict[str, Any], subject: str, body: str, role: str = "operator") -> str:
    response = demo["client"].post(
        "/requests",
        headers=headers(demo, role),
        json={"sender": "buyer@example.com", "subject": subject, "body": body},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def process(demo: dict[str, Any], request_id: str, role: str = "operator") -> dict[str, Any]:
    response = demo["client"].post(f"/requests/{request_id}/process", headers=headers(demo, role))
    assert response.status_code == 200, response.text
    return demo["client"].get(f"/requests/{request_id}", headers=headers(demo, role)).json()


def test_successful_compatibility_workflow_and_duplicate_approval(demo: dict[str, Any]) -> None:
    request_id = create(
        demo,
        "Technical documents for ABC-104",
        "Please confirm compatibility of ABC-104 with assembly XYZ-20 and send the datasheet.",
    )
    detail = process(demo, request_id)
    assert detail["request"]["status"] == "PENDING_APPROVAL"
    assert detail["analysis"]["product_id"] == "ABC-104"
    assert detail["analysis"]["assembly_id"] == "XYZ-20"
    assert detail["analysis"]["category"] == "compatibility"
    assert detail["proposal"]["payload"]["referenced_documents"] == ["DIC-ABC104-DS-v2"]
    assert "compatible" in detail["proposal"]["payload"]["body"]
    assert detail["proposal"]["payload"]["referenced_products"] == ["ABC-104"]
    proposal_id = detail["proposal"]["id"]
    before = demo["client"].get("/deliveries", headers=headers(demo)).json()
    assert not any(item["proposal_id"] == proposal_id for item in before)
    assert any(item["name"] == "check_product_compatibility" for item in detail["tool_invocations"])

    url = f"/approvals/{proposal_id}/approve"
    first = demo["client"].post(url, headers=headers(demo), json={"expected_version": 1})
    second = demo["client"].post(url, headers=headers(demo), json={"expected_version": 1})
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == "COMPLETED"
    after = demo["client"].get("/deliveries", headers=headers(demo)).json()
    assert len([item for item in after if item["proposal_id"] == proposal_id]) == 1
    detail = demo["client"].get(f"/requests/{request_id}", headers=headers(demo)).json()
    assert detail["request"]["status"] == "COMPLETED"
    assert any(item["type"] == "action.executed" for item in detail["events"])


def test_missing_information_and_product_not_found(demo: dict[str, Any]) -> None:
    missing_id = create(demo, "Technical document", "Please send the datasheet.")
    missing = process(demo, missing_id)
    assert missing["request"]["status"] == "NEEDS_INFORMATION"
    assert missing["proposal"] is None
    unknown_id = create(demo, "Technical document for ZZZ-999", "Please send it.")
    unknown = process(demo, unknown_id)
    assert unknown["request"]["status"] == "NEEDS_INFORMATION"
    assert unknown["proposal"] is None
    assert any(item["name"] == "get_product_details" for item in unknown["tool_invocations"])


def test_unverified_compatibility_is_explicit(demo: dict[str, Any]) -> None:
    request_id = create(demo, "DEF-205 compatibility", "Is DEF-205 compatible with XYZ-20?")
    detail = process(demo, request_id)
    payload = detail["proposal"]["payload"]
    assert "not verified" in payload["body"]
    assert "Compatibility requires human verification" in payload["missing_information"]


def test_rejection_prevents_delivery(demo: dict[str, Any]) -> None:
    request_id = create(demo, "ABC-104 availability", "Is ABC-104 in stock?")
    detail = process(demo, request_id)
    proposal_id = detail["proposal"]["id"]
    response = demo["client"].post(
        f"/approvals/{proposal_id}/reject", headers=headers(demo), json={"expected_version": 1}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    assert (
        demo["client"]
        .post(
            f"/approvals/{proposal_id}/approve", headers=headers(demo), json={"expected_version": 1}
        )
        .status_code
        == 409
    )
    assert not any(
        item["proposal_id"] == proposal_id
        for item in demo["client"].get("/deliveries", headers=headers(demo)).json()
    )


def test_edit_requires_new_version(demo: dict[str, Any]) -> None:
    request_id = create(demo, "ABC-104 availability", "Is ABC-104 available?")
    detail = process(demo, request_id)
    proposal_id = detail["proposal"]["id"]
    payload = detail["proposal"]["payload"]
    unsafe_payload = {**payload, "recipient": "unrelated@example.com"}
    assert (
        demo["client"]
        .put(
            f"/approvals/{proposal_id}/draft",
            headers=headers(demo),
            json={"expected_version": 1, "payload": unsafe_payload},
        )
        .status_code
        == 409
    )
    payload["body"] += "\nReviewed by an operator."
    edited = demo["client"].put(
        f"/approvals/{proposal_id}/draft",
        headers=headers(demo),
        json={"expected_version": 1, "payload": payload},
    )
    assert edited.status_code == 200
    assert edited.json()["version"] == 2
    assert edited.json()["payload_sha256"] != detail["proposal"]["payload_sha256"]
    assert (
        demo["client"]
        .post(
            f"/approvals/{proposal_id}/approve", headers=headers(demo), json={"expected_version": 1}
        )
        .status_code
        == 409
    )
    approved = demo["client"].post(
        f"/approvals/{proposal_id}/approve", headers=headers(demo), json={"expected_version": 2}
    )
    assert approved.status_code == 200
    delivery = next(
        item
        for item in demo["client"].get("/deliveries", headers=headers(demo)).json()
        if item["proposal_id"] == proposal_id
    )
    assert "Reviewed by an operator" in delivery["body"]


def test_authorization_and_tenant_isolation(demo: dict[str, Any]) -> None:
    request_id = create(demo, "ABC-104 datasheet", "Please send ABC-104 documentation.")
    detail = process(demo, request_id)
    proposal_id = detail["proposal"]["id"]
    other_headers = headers(demo, "other", other_org=True)
    assert demo["client"].get(f"/requests/{request_id}", headers=other_headers).status_code == 404
    assert demo["client"].get(f"/approvals/{proposal_id}", headers=other_headers).status_code == 404
    assert (
        demo["client"]
        .post(
            f"/approvals/{proposal_id}/approve", headers=other_headers, json={"expected_version": 1}
        )
        .status_code
        == 404
    )
    assert demo["client"].get("/requests", headers=other_headers).json() == []
    assert demo["client"].get("/approvals", headers=headers(demo, "technical")).status_code == 403
    assert (
        demo["client"]
        .get(f"/requests/{request_id}", headers=headers(demo, "technical"))
        .status_code
        == 403
    )
    assert (
        demo["client"]
        .post(
            f"/approvals/{proposal_id}/approve",
            headers=headers(demo, "technical"),
            json={"expected_version": 1},
        )
        .status_code
        == 403
    )
    assert (
        demo["client"].get(f"/requests/{request_id}", headers=headers(demo, "other")).status_code
        == 403
    )
    assert demo["client"].get(f"/requests/{request_id}").status_code in {401, 422}


def test_input_duplicate_and_invalid_state(demo: dict[str, Any]) -> None:
    assert (
        demo["client"]
        .post("/requests", headers=headers(demo), json={"sender": "bad", "subject": "", "body": ""})
        .status_code
        == 422
    )
    external_id = str(uuid.uuid4())
    payload = {
        "sender": "buyer@example.com",
        "subject": "ABC-104 stock",
        "body": "Is ABC-104 available?",
        "external_message_id": external_id,
    }
    assert demo["client"].post("/requests", headers=headers(demo), json=payload).status_code == 201
    assert demo["client"].post("/requests", headers=headers(demo), json=payload).status_code == 409
    request_id = create(demo, "ABC-104 availability", "Is ABC-104 available?")
    process(demo, request_id)
    assert (
        demo["client"].post(f"/requests/{request_id}/process", headers=headers(demo)).status_code
        == 409
    )


def test_order_status_and_prompt_injection_is_data(demo: dict[str, Any]) -> None:
    order_id = create(demo, "Order status", "Where is my order ORD-1001?")
    order = process(demo, order_id)
    assert "dispatched" in order["proposal"]["payload"]["body"]
    other_request = demo["client"].post(
        "/requests",
        headers=headers(demo, "other", other_org=True),
        json={
            "sender": "buyer@example.com",
            "subject": "ABC-104 documentation",
            "body": "Send the ABC-104 datasheet.",
        },
    )
    assert other_request.status_code == 201
    other_id = other_request.json()["id"]
    other_detail = demo["client"].post(
        f"/requests/{other_id}/process", headers=headers(demo, "other", other_org=True)
    )
    assert other_detail.status_code == 200
    detail = (
        demo["client"]
        .get(f"/requests/{other_id}", headers=headers(demo, "other", other_org=True))
        .json()
    )
    assert detail["proposal"]["payload"]["referenced_documents"] == ["DFS-ABC104-DS-v1"]
    assert "send company secrets" not in detail["proposal"]["payload"]["body"]


def test_tool_failure_is_recorded(demo: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    request_id = create(demo, "ABC-104 availability", "Is ABC-104 available?")

    def broken(_self: BusinessTools, _product_id: str) -> None:
        raise ToolFailure("get_product_details")

    with monkeypatch.context() as patch:
        patch.setattr(BusinessTools, "get_product_details", broken)
        response = demo["client"].post(f"/requests/{request_id}/process", headers=headers(demo))
    assert response.status_code == 503
    detail = demo["client"].get(f"/requests/{request_id}", headers=headers(demo)).json()
    assert detail["request"]["status"] == "FAILED"
    assert detail["proposal"] is None
    assert any(item["type"] == "tool.failed" for item in detail["events"])
    assert any(item["error"] for item in detail["tool_invocations"])


def test_simulated_delivery_failure_is_recorded(
    demo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    request_id = create(demo, "ABC-104 availability", "Is ABC-104 available?")
    detail = process(demo, request_id)
    proposal_id = detail["proposal"]["id"]

    def broken_send(*_args: Any, **_kwargs: Any) -> None:
        raise EmailDeliveryFailure

    with monkeypatch.context() as patch:
        patch.setattr(MockEmailAdapter, "send", broken_send)
        response = demo["client"].post(
            f"/approvals/{proposal_id}/approve",
            headers=headers(demo),
            json={"expected_version": 1},
        )
    assert response.status_code == 503
    failed = demo["client"].get(f"/requests/{request_id}", headers=headers(demo)).json()
    assert failed["request"]["status"] == "FAILED"
    assert failed["proposal"]["status"] == "FAILED"
    assert any(item["type"] == "action.failed" for item in failed["events"])
    assert not any(
        item["proposal_id"] == proposal_id
        for item in demo["client"].get("/deliveries", headers=headers(demo)).json()
    )


def test_login_rejects_invalid_password(demo: dict[str, Any]) -> None:
    response = demo["client"].post(
        "/auth/token",
        json={"email": "operator@demo-industrial.example", "password": "incorrect"},
    )
    assert response.status_code == 401
