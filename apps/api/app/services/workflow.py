import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import OrganizationContext
from app.db.models import (
    ActionProposal,
    ApprovalDecision,
    AuditEvent,
    Request,
    RequestStatus,
    ToolInvocation,
)
from app.domain.analysis import RequestAnalysis, analyze_request
from app.integrations.mock_email import EmailDeliveryFailure, MockEmailAdapter
from app.services.schemas import DraftPayload, RequestInput
from app.tools.repository import BusinessTools, ToolFailure


class ResourceNotFound(Exception):
    pass


class WorkflowConflict(Exception):
    pass


class PermissionDenied(Exception):
    pass


class ActionExecutionFailed(Exception):
    pass


def _require_business_role(context: OrganizationContext) -> None:
    if context.role not in {"operator", "supervisor", "administrator"}:
        raise PermissionDenied


_transitions: dict[str, set[str]] = {
    RequestStatus.RECEIVED: {RequestStatus.ANALYZING},
    RequestStatus.ANALYZING: {
        RequestStatus.RETRIEVING,
        RequestStatus.NEEDS_INFORMATION,
        RequestStatus.FAILED,
    },
    RequestStatus.RETRIEVING: {
        RequestStatus.DRAFT_READY,
        RequestStatus.NEEDS_INFORMATION,
        RequestStatus.FAILED,
    },
    RequestStatus.DRAFT_READY: {RequestStatus.PENDING_APPROVAL},
    RequestStatus.PENDING_APPROVAL: {
        RequestStatus.APPROVED,
        RequestStatus.REJECTED,
        RequestStatus.FAILED,
    },
    RequestStatus.APPROVED: {RequestStatus.EXECUTING},
    RequestStatus.EXECUTING: {RequestStatus.COMPLETED, RequestStatus.FAILED},
}


def _transition(request: Request, next_status: RequestStatus) -> None:
    if next_status not in _transitions.get(request.status, set()):
        raise WorkflowConflict(f"Invalid transition: {request.status} -> {next_status}")
    request.status = next_status


def _audit(
    session: Session,
    context: OrganizationContext,
    request_id: uuid.UUID,
    event_type: str,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            organization_id=context.organization_id,
            request_id=request_id,
            actor_user_id=context.user_id,
            event_type=event_type,
            details_json=json.dumps(details or {}, sort_keys=True),
        )
    )


def _request(
    session: Session, context: OrganizationContext, request_id: uuid.UUID, *, lock: bool = False
) -> Request:
    statement = select(Request).where(
        Request.id == request_id, Request.organization_id == context.organization_id
    )
    request = session.scalar(statement.with_for_update() if lock else statement)
    if request is None:
        raise ResourceNotFound
    return request


def _proposal(
    session: Session, context: OrganizationContext, proposal_id: uuid.UUID, *, lock: bool = False
) -> ActionProposal:
    statement = select(ActionProposal).where(
        ActionProposal.id == proposal_id,
        ActionProposal.organization_id == context.organization_id,
    )
    proposal = session.scalar(statement.with_for_update() if lock else statement)
    if proposal is None:
        raise ResourceNotFound
    return proposal


def create_request(session: Session, context: OrganizationContext, data: RequestInput) -> Request:
    _require_business_role(context)
    request = Request(
        organization_id=context.organization_id,
        source="synthetic_email",
        external_message_id=data.external_message_id,
        sender=str(data.sender),
        subject=data.subject,
        body=data.body,
        status=RequestStatus.RECEIVED,
    )
    session.add(request)
    session.flush()
    _audit(session, context, request.id, "request.created")
    session.commit()
    return request


def _make_draft(
    request: Request,
    analysis: RequestAnalysis,
    facts: dict[str, Any],
    documents: list[dict[str, str]],
    missing: list[str],
) -> DraftPayload:
    lines = ["Hello,", "", f"Regarding {request.subject}:"]
    if analysis.product_id:
        lines.append(f"Product: {analysis.product_id}.")
    if facts.get("product"):
        lines.append(f"Product name: {facts['product']['name']}.")
    if facts.get("specifications"):
        lines.append(f"Specifications: {json.dumps(facts['specifications'], sort_keys=True)}.")
    if "availability" in facts:
        lines.append(f"Available units in the demo record: {facts['availability']}.")
    if facts.get("order_status"):
        lines.append(f"Order {analysis.order_id} status: {facts['order_status']}.")
    if analysis.category == "compatibility":
        rule = facts.get("compatibility")
        if rule is None:
            lines.append(
                f"Compatibility with {analysis.assembly_id} is not verified. "
                "Our team must confirm it before we can provide an answer."
            )
        else:
            verdict = "compatible" if rule["compatible"] else "not compatible"
            lines.append(
                f"The authoritative product record marks it {verdict} with {analysis.assembly_id}."
            )
            lines.append(f"Basis: {rule['rationale']}.")
    for document in documents:
        lines.append(
            f"Technical document: {document['title']} (source {document['source_ref']}, "
            f"version {document['version']})."
        )
    if missing:
        lines.append("Information requiring verification: " + "; ".join(missing) + ".")
    lines.extend(["", "Kind regards,", "Demo Industrial Components"])
    return DraftPayload(
        recipient=request.sender,
        subject=f"Re: {request.subject}",
        body="\n".join(lines),
        referenced_documents=[document["source_ref"] for document in documents],
        referenced_products=[analysis.product_id]
        if facts.get("product") and analysis.product_id
        else [],
        missing_information=missing,
    )


def _payload_hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def process_request(
    session: Session, context: OrganizationContext, request_id: uuid.UUID
) -> Request:
    _require_business_role(context)
    request = _request(session, context, request_id, lock=True)
    if request.status != RequestStatus.RECEIVED:
        raise WorkflowConflict("Request has already been processed")
    try:
        _transition(request, RequestStatus.ANALYZING)
        analysis = analyze_request(request.subject, request.body)
        request.category = analysis.category
        request.analysis_json = analysis.model_dump_json()
        _audit(session, context, request.id, "request.analyzed", {"category": analysis.category})
        if analysis.missing_information:
            _transition(request, RequestStatus.NEEDS_INFORMATION)
            _audit(
                session,
                context,
                request.id,
                "request.needs_information",
                {"reasons": analysis.missing_information},
            )
            session.commit()
            return request

        _transition(request, RequestStatus.RETRIEVING)
        tools = BusinessTools(session, context.organization_id, request.id)
        facts: dict[str, Any] = {}
        documents: list[dict[str, str]] = []
        missing: list[str] = []
        if analysis.category == "order_status" and analysis.order_id:
            facts["order_status"] = tools.get_order_status(analysis.order_id)
            if facts["order_status"] is None:
                missing.append("Order record not found")
        elif analysis.product_id:
            facts["product"] = tools.get_product_details(analysis.product_id)
            if facts["product"] is None:
                missing.append("Product record not found")
            else:
                if analysis.category in {"specification", "documentation", "compatibility"}:
                    facts["specifications"] = tools.get_product_specifications(analysis.product_id)
                    documents = tools.search_documents(analysis.product_id)
                    if not documents:
                        missing.append("No active technical document found")
                if analysis.category == "compatibility" and analysis.assembly_id:
                    facts["compatibility"] = tools.check_product_compatibility(
                        analysis.product_id, analysis.assembly_id
                    )
                    if facts["compatibility"] is None:
                        missing.append("Compatibility requires human verification")
                if analysis.category == "availability":
                    facts["availability"] = tools.get_product_availability(analysis.product_id)
        if "Product record not found" in missing or "Order record not found" in missing:
            _transition(request, RequestStatus.NEEDS_INFORMATION)
            _audit(session, context, request.id, "request.needs_information", {"reasons": missing})
            session.commit()
            return request

        _transition(request, RequestStatus.DRAFT_READY)
        draft = _make_draft(request, analysis, facts, documents, missing)
        payload_json = draft.model_dump_json()
        proposal = ActionProposal(
            organization_id=context.organization_id,
            request_id=request.id,
            action_type="send_email_simulated",
            payload_json=payload_json,
            payload_sha256=_payload_hash(payload_json),
            idempotency_key=f"request:{request.id}:email",
            status="PENDING",
            version=1,
        )
        session.add(proposal)
        _audit(
            session,
            context,
            request.id,
            "draft.created",
            {"source_refs": draft.referenced_documents},
        )
        _transition(request, RequestStatus.PENDING_APPROVAL)
        _audit(session, context, request.id, "approval.requested")
        session.commit()
        return request
    except ToolFailure as exc:
        session.rollback()
        request = _request(session, context, request_id, lock=True)
        request.category = request.category or "unsupported"
        _transition(request, RequestStatus.ANALYZING)
        _transition(request, RequestStatus.FAILED)
        session.add(
            ToolInvocation(
                organization_id=context.organization_id,
                request_id=request.id,
                tool_name=exc.name,
                input_json="{}",
                error="Simulated tool failure",
            )
        )
        _audit(session, context, request.id, "tool.failed", {"tool": exc.name})
        _audit(session, context, request.id, "request.failed")
        session.commit()
        return request


def edit_draft(
    session: Session,
    context: OrganizationContext,
    proposal_id: uuid.UUID,
    expected_version: int,
    payload: DraftPayload,
) -> ActionProposal:
    _require_business_role(context)
    proposal = _proposal(session, context, proposal_id, lock=True)
    if proposal.status != "PENDING" or proposal.version != expected_version:
        raise WorkflowConflict("Proposal state or version changed")
    request = _request(session, context, proposal.request_id)
    if str(payload.recipient).lower() != request.sender.lower():
        raise WorkflowConflict("Draft recipient must match the original sender")
    payload_json = payload.model_dump_json()
    proposal.payload_json = payload_json
    proposal.payload_sha256 = _payload_hash(payload_json)
    proposal.version += 1
    proposal.approved_by_user_id = None
    proposal.approved_at = None
    _audit(session, context, proposal.request_id, "draft.edited", {"version": proposal.version})
    session.commit()
    return proposal


def approve(
    session: Session, context: OrganizationContext, proposal_id: uuid.UUID, expected_version: int
) -> ActionProposal:
    _require_business_role(context)
    proposal = _proposal(session, context, proposal_id, lock=True)
    if proposal.version != expected_version:
        raise WorkflowConflict("Proposal version changed")
    if proposal.status == "COMPLETED":
        return proposal
    if proposal.status != "PENDING":
        raise WorkflowConflict("Proposal is not pending")
    request = _request(session, context, proposal.request_id, lock=True)
    payload = DraftPayload.model_validate_json(proposal.payload_json)
    if str(payload.recipient).lower() != request.sender.lower():
        raise WorkflowConflict("Draft recipient must match the original sender")
    if _payload_hash(proposal.payload_json) != proposal.payload_sha256:
        raise WorkflowConflict("Proposal payload hash mismatch")
    _transition(request, RequestStatus.APPROVED)
    proposal.status = "APPROVED"
    proposal.approved_by_user_id = context.user_id
    proposal.approved_at = datetime.now(UTC)
    session.add(
        ApprovalDecision(
            organization_id=context.organization_id,
            proposal_id=proposal.id,
            decided_by_user_id=context.user_id,
            decision="APPROVED",
            payload_sha256=proposal.payload_sha256,
            proposal_version=proposal.version,
        )
    )
    _audit(session, context, request.id, "approval.approved", {"version": proposal.version})
    _transition(request, RequestStatus.EXECUTING)
    try:
        delivery = MockEmailAdapter().send(
            session, context.organization_id, proposal.id, proposal.idempotency_key, payload
        )
    except EmailDeliveryFailure as exc:
        session.rollback()
        proposal = _proposal(session, context, proposal_id, lock=True)
        request = _request(session, context, proposal.request_id, lock=True)
        proposal.status = "FAILED"
        proposal.approved_by_user_id = context.user_id
        proposal.approved_at = datetime.now(UTC)
        _transition(request, RequestStatus.FAILED)
        session.add(
            ApprovalDecision(
                organization_id=context.organization_id,
                proposal_id=proposal.id,
                decided_by_user_id=context.user_id,
                decision="APPROVED",
                payload_sha256=proposal.payload_sha256,
                proposal_version=proposal.version,
            )
        )
        _audit(session, context, request.id, "approval.approved", {"version": proposal.version})
        _audit(session, context, request.id, "action.failed")
        session.commit()
        raise ActionExecutionFailed from exc
    proposal.result_json = json.dumps({"delivery_id": str(delivery.id)})
    proposal.executed_at = datetime.now(UTC)
    proposal.status = "COMPLETED"
    _transition(request, RequestStatus.COMPLETED)
    _audit(session, context, request.id, "action.executed", {"delivery_id": str(delivery.id)})
    session.commit()
    return proposal


def reject(
    session: Session, context: OrganizationContext, proposal_id: uuid.UUID, expected_version: int
) -> ActionProposal:
    _require_business_role(context)
    proposal = _proposal(session, context, proposal_id, lock=True)
    if proposal.version != expected_version:
        raise WorkflowConflict("Proposal version changed")
    if proposal.status == "REJECTED":
        return proposal
    if proposal.status != "PENDING":
        raise WorkflowConflict("Proposal is not pending")
    request = _request(session, context, proposal.request_id, lock=True)
    proposal.status = "REJECTED"
    session.add(
        ApprovalDecision(
            organization_id=context.organization_id,
            proposal_id=proposal.id,
            decided_by_user_id=context.user_id,
            decision="REJECTED",
            payload_sha256=proposal.payload_sha256,
            proposal_version=proposal.version,
        )
    )
    _transition(request, RequestStatus.REJECTED)
    _audit(session, context, request.id, "approval.rejected", {"version": proposal.version})
    session.commit()
    return proposal
