"""PostgreSQL integration check for approval idempotency under concurrency."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.auth.security import OrganizationContext
from app.db.base import Base
from app.db.models import ActionProposal, SimulatedEmailDelivery, User
from app.db.seed_demo import seed
from app.services.schemas import RequestInput
from app.services.workflow import approve, create_request, process_request


def test_two_concurrent_approvals_create_one_delivery() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL concurrency test")
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql") or not (url.database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a dedicated PostgreSQL *_test database")
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            organizations = seed(session, "synthetic-concurrency-password")
            user = session.scalar(
                select(User).where(User.email == "operator@demo-industrial.example")
            )
            assert user is not None
            context = OrganizationContext(
                user_id=user.id,
                organization_id=uuid.UUID(organizations["Demo Industrial Components"]),
                role="operator",
            )
            request = create_request(
                session,
                context,
                RequestInput(
                    sender="buyer@example.com",
                    subject="ABC-104 availability",
                    body="Is ABC-104 available?",
                ),
            )
            process_request(session, context, request.id)
            proposal = session.scalar(
                select(ActionProposal).where(ActionProposal.request_id == request.id)
            )
            assert proposal is not None
            proposal_id = proposal.id

        barrier = Barrier(2)

        def worker() -> str:
            with Session(engine, expire_on_commit=False) as worker_session:
                barrier.wait(timeout=10)
                return approve(worker_session, context, proposal_id, 1).status

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _index: worker(), range(2)))
        assert outcomes == ["COMPLETED", "COMPLETED"]
        with Session(engine) as session:
            count = session.scalar(
                select(func.count())
                .select_from(SimulatedEmailDelivery)
                .where(SimulatedEmailDelivery.proposal_id == proposal_id)
            )
            assert count == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
