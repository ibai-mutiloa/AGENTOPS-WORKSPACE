"""Tenant-aware business schema."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class RequestStatus(StrEnum):
    RECEIVED = "RECEIVED"
    ANALYZING = "ANALYZING"
    RETRIEVING = "RETRIEVING"
    DRAFT_READY = "DRAFT_READY"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    external_subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(40), nullable=False)


class Request(Base):
    __tablename__ = "requests"
    __table_args__ = (
        UniqueConstraint("organization_id", "source", "external_message_id"),
        UniqueConstraint("organization_id", "id"),
        Index("ix_requests_org_status_created", "organization_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(255))
    sender: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    analysis_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default=RequestStatus.RECEIVED, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ActionProposal(Base):
    __tablename__ = "action_proposals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "request_id"], ["requests.organization_id", "requests.id"]
        ),
        UniqueConstraint("organization_id", "idempotency_key"),
        UniqueConstraint("organization_id", "id"),
        Index("ix_action_proposals_org_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    request_id: Mapped[uuid.UUID]
    action_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("organization_id", "sku"),
        UniqueConstraint("organization_id", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    specifications_json: Mapped[str] = mapped_column(Text, nullable=False)
    available_units: Mapped[int] = mapped_column(Integer, nullable=False)


class ProductCompatibility(Base):
    __tablename__ = "product_compatibility"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "product_id"], ["products.organization_id", "products.id"]
        ),
        UniqueConstraint("organization_id", "product_id", "assembly_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    product_id: Mapped[uuid.UUID]
    assembly_id: Mapped[str] = mapped_column(String(80), nullable=False)
    compatible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "product_id"], ["products.organization_id", "products.id"]
        ),
        UniqueConstraint("organization_id", "source_ref"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    product_id: Mapped[uuid.UUID | None]
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("organization_id", "order_number"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    order_number: Mapped[str] = mapped_column(String(80), nullable=False)
    customer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(80), nullable=False)


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "request_id"], ["requests.organization_id", "requests.id"]
        ),
        Index("ix_tool_invocations_org_request", "organization_id", "request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    request_id: Mapped[uuid.UUID]
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    input_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "request_id"], ["requests.organization_id", "requests.id"]
        ),
        Index("ix_audit_events_org_request_created", "organization_id", "request_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    request_id: Mapped[uuid.UUID | None]
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    details_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "proposal_id"],
            ["action_proposals.organization_id", "action_proposals.id"],
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    proposal_id: Mapped[uuid.UUID]
    decided_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SimulatedEmailDelivery(Base):
    __tablename__ = "simulated_email_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "proposal_id"],
            ["action_proposals.organization_id", "action_proposals.id"],
        ),
        UniqueConstraint("organization_id", "proposal_id"),
        UniqueConstraint("organization_id", "idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    proposal_id: Mapped[uuid.UUID]
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
