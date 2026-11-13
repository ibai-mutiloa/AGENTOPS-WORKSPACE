import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from app.core.config import Settings, get_settings
from app.db.models import Membership, User
from app.db.session import get_session

_bearer = HTTPBearer(auto_error=False)
_issuer = "agentops-workspace"
_audience = "agentops-api"


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID


@dataclass(frozen=True)
class OrganizationContext:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str


def hash_password(password: str) -> str:
    return generate_password_hash(password, method="scrypt")


def verify_password(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)


def _jwt_secret(settings: Settings) -> str:
    if settings.jwt_secret is None or len(settings.jwt_secret.get_secret_value()) < 32:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    return settings.jwt_secret.get_secret_value()


def create_access_token(user_id: uuid.UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iss": _issuer,
            "aud": _audience,
            "iat": now,
            "exp": now + timedelta(minutes=30),
        },
        _jwt_secret(settings),
        algorithm="HS256",
    )


def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    try:
        claims = jwt.decode(
            credentials.credentials,
            _jwt_secret(settings),
            algorithms=["HS256"],
            issuer=_issuer,
            audience=_audience,
            options={"require": ["sub", "iss", "aud", "iat", "exp"]},
        )
        user_id = uuid.UUID(claims["sub"])
    except (InvalidTokenError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    if session.get(User, user_id) is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return Principal(user_id=user_id)


def get_organization_context(
    organization_id: uuid.UUID = Header(alias="X-Organization-ID"),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> OrganizationContext:
    membership = session.scalar(
        select(Membership).where(
            Membership.organization_id == organization_id,
            Membership.user_id == principal.user_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Organization access denied")
    return OrganizationContext(
        user_id=principal.user_id,
        organization_id=organization_id,
        role=membership.role,
    )


def require_approver(
    context: OrganizationContext = Depends(get_organization_context),
) -> OrganizationContext:
    if context.role not in {"operator", "supervisor", "administrator"}:
        raise HTTPException(status_code=403, detail="Approval permission required")
    return context


def require_business_reader(
    context: OrganizationContext = Depends(get_organization_context),
) -> OrganizationContext:
    if context.role not in {"operator", "supervisor", "administrator"}:
        raise HTTPException(status_code=403, detail="Business content permission required")
    return context
