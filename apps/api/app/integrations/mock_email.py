import uuid

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import SimulatedEmailDelivery
from app.services.schemas import DraftPayload


class EmailDeliveryFailure(Exception):
    pass


class MockEmailAdapter:
    """Persist a simulated delivery; never contact an external email provider."""

    def send(
        self,
        session: Session,
        organization_id: uuid.UUID,
        proposal_id: uuid.UUID,
        idempotency_key: str,
        payload: DraftPayload,
    ) -> SimulatedEmailDelivery:
        existing = session.scalar(
            select(SimulatedEmailDelivery).where(
                SimulatedEmailDelivery.organization_id == organization_id,
                SimulatedEmailDelivery.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
        delivery = SimulatedEmailDelivery(
            organization_id=organization_id,
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            recipient=str(payload.recipient),
            subject=payload.subject,
            body=payload.body,
        )
        try:
            session.add(delivery)
            session.flush()
        except SQLAlchemyError as exc:
            raise EmailDeliveryFailure from exc
        return delivery
