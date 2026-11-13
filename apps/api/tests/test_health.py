from fastapi.testclient import TestClient

from app.main import app


def test_liveness() -> None:
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_database_failure() -> None:
    from unittest.mock import patch

    from sqlalchemy.exc import OperationalError

    with patch("app.main.create_db_engine", side_effect=OperationalError("connect", {}, None)):
        response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}
