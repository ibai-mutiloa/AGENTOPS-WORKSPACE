import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.security import (
    OrganizationContext,
    Principal,
    create_access_token,
    get_principal,
    require_approver,
    require_business_reader,
    verify_password,
)
from app.core.config import Settings, get_settings
from app.db.models import (
    ActionProposal,
    AuditEvent,
    Membership,
    Request,
    SimulatedEmailDelivery,
    ToolInvocation,
    User,
)
from app.db.session import get_session
from app.services.schemas import (
    DecisionInput,
    DraftPayload,
    EditDraftInput,
    LoginInput,
    ProposalResponse,
    RequestInput,
    RequestResponse,
)
from app.services.workflow import (
    ResourceNotFound,
    approve,
    create_request,
    edit_draft,
    process_request,
    reject,
)

router = APIRouter()


def _request_response(request: Request) -> RequestResponse:
    return RequestResponse(
        id=request.id,
        status=request.status,
        category=request.category,
        sender=request.sender,
        subject=request.subject,
        body=request.body,
    )


def _proposal_response(proposal: ActionProposal) -> ProposalResponse:
    return ProposalResponse(
        id=proposal.id,
        request_id=proposal.request_id,
        status=proposal.status,
        version=proposal.version,
        action_type=proposal.action_type,
        payload=DraftPayload.model_validate_json(proposal.payload_json),
        payload_sha256=proposal.payload_sha256,
    )


@router.post("/auth/token")
def login(
    data: LoginInput,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    user = session.scalar(select(User).where(User.email == str(data.email).lower()))
    if (
        user is None
        or user.password_hash is None
        or not verify_password(data.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"access_token": create_access_token(user.id, settings), "token_type": "bearer"}


@router.get("/auth/me")
def current_user(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_session)
) -> dict[str, Any]:
    user = session.get(User, principal.user_id)
    assert user is not None
    memberships = session.scalars(
        select(Membership).where(Membership.user_id == principal.user_id)
    ).all()
    return {
        "id": str(user.id),
        "email": user.email,
        "memberships": [
            {"organization_id": str(item.organization_id), "role": item.role}
            for item in memberships
        ],
    }


@router.post("/requests", status_code=201)
def intake(
    data: RequestInput,
    context: OrganizationContext = Depends(require_business_reader),
    session: Session = Depends(get_session),
) -> RequestResponse:
    try:
        request = create_request(session, context, data)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Duplicate external message") from exc
    return _request_response(request)


@router.get("/requests")
def list_requests(
    status: str | None = Query(default=None, max_length=40),
    context: OrganizationContext = Depends(require_business_reader),
    session: Session = Depends(get_session),
) -> list[RequestResponse]:
    statement = select(Request).where(Request.organization_id == context.organization_id)
    if status is not None:
        statement = statement.where(Request.status == status)
    requests = session.scalars(statement.order_by(Request.created_at.desc()).limit(100)).all()
    return [_request_response(request) for request in requests]


@router.get("/requests/{request_id}")
def request_detail(
    request_id: uuid.UUID,
    context: OrganizationContext = Depends(require_business_reader),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    request = session.scalar(
        select(Request).where(
            Request.id == request_id, Request.organization_id == context.organization_id
        )
    )
    if request is None:
        raise ResourceNotFound
    proposal = session.scalar(
        select(ActionProposal).where(
            ActionProposal.request_id == request_id,
            ActionProposal.organization_id == context.organization_id,
        )
    )
    events = session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == context.organization_id,
            AuditEvent.request_id == request_id,
        )
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ).all()
    invocations = session.scalars(
        select(ToolInvocation)
        .where(
            ToolInvocation.organization_id == context.organization_id,
            ToolInvocation.request_id == request_id,
        )
        .order_by(ToolInvocation.created_at, ToolInvocation.id)
    ).all()
    return {
        "request": _request_response(request).model_dump(mode="json"),
        "analysis": json.loads(request.analysis_json) if request.analysis_json else None,
        "proposal": _proposal_response(proposal).model_dump(mode="json") if proposal else None,
        "events": [
            {"type": event.event_type, "details": json.loads(event.details_json or "{}")}
            for event in events
        ],
        "tool_invocations": [
            {
                "name": item.tool_name,
                "result": json.loads(item.result_json) if item.result_json else None,
                "error": item.error,
            }
            for item in invocations
        ],
    }


@router.post("/requests/{request_id}/process")
def process(
    request_id: uuid.UUID,
    context: OrganizationContext = Depends(require_business_reader),
    session: Session = Depends(get_session),
) -> RequestResponse:
    request = process_request(session, context, request_id)
    if request.status == "FAILED":
        raise HTTPException(
            status_code=503, detail="Request processing failed; failure was recorded"
        )
    return _request_response(request)


@router.get("/approvals")
def list_approvals(
    context: OrganizationContext = Depends(require_approver),
    session: Session = Depends(get_session),
) -> list[ProposalResponse]:
    proposals = session.scalars(
        select(ActionProposal)
        .where(
            ActionProposal.organization_id == context.organization_id,
            ActionProposal.status == "PENDING",
        )
        .order_by(ActionProposal.created_at)
        .limit(100)
    ).all()
    return [_proposal_response(proposal) for proposal in proposals]


@router.get("/approvals/{proposal_id}")
def approval_detail(
    proposal_id: uuid.UUID,
    context: OrganizationContext = Depends(require_approver),
    session: Session = Depends(get_session),
) -> ProposalResponse:
    proposal = session.scalar(
        select(ActionProposal).where(
            ActionProposal.id == proposal_id,
            ActionProposal.organization_id == context.organization_id,
        )
    )
    if proposal is None:
        raise ResourceNotFound
    return _proposal_response(proposal)


@router.put("/approvals/{proposal_id}/draft")
def update_draft(
    proposal_id: uuid.UUID,
    data: EditDraftInput,
    context: OrganizationContext = Depends(require_approver),
    session: Session = Depends(get_session),
) -> ProposalResponse:
    return _proposal_response(
        edit_draft(session, context, proposal_id, data.expected_version, data.payload)
    )


@router.post("/approvals/{proposal_id}/approve")
def approve_proposal(
    proposal_id: uuid.UUID,
    data: DecisionInput,
    context: OrganizationContext = Depends(require_approver),
    session: Session = Depends(get_session),
) -> ProposalResponse:
    return _proposal_response(approve(session, context, proposal_id, data.expected_version))


@router.post("/approvals/{proposal_id}/reject")
def reject_proposal(
    proposal_id: uuid.UUID,
    data: DecisionInput,
    context: OrganizationContext = Depends(require_approver),
    session: Session = Depends(get_session),
) -> ProposalResponse:
    return _proposal_response(reject(session, context, proposal_id, data.expected_version))


@router.get("/deliveries")
def list_deliveries(
    context: OrganizationContext = Depends(require_approver),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    deliveries = session.scalars(
        select(SimulatedEmailDelivery)
        .where(SimulatedEmailDelivery.organization_id == context.organization_id)
        .order_by(SimulatedEmailDelivery.created_at.desc())
        .limit(100)
    ).all()
    return [
        {
            "id": str(item.id),
            "proposal_id": str(item.proposal_id),
            "recipient": item.recipient,
            "subject": item.subject,
            "body": item.body,
        }
        for item in deliveries
    ]
