from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class RequestInput(BaseModel):
    sender: EmailStr
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)
    external_message_id: str | None = Field(default=None, max_length=255)


class DraftPayload(BaseModel):
    recipient: EmailStr
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)
    referenced_documents: list[str]
    referenced_products: list[str]
    missing_information: list[str]


class EditDraftInput(BaseModel):
    expected_version: int = Field(ge=1)
    payload: DraftPayload


class DecisionInput(BaseModel):
    expected_version: int = Field(ge=1)


class RequestResponse(BaseModel):
    id: UUID
    status: str
    category: str | None
    sender: EmailStr
    subject: str
    body: str


class ProposalResponse(BaseModel):
    id: UUID
    request_id: UUID
    status: str
    version: int
    action_type: str
    payload: DraftPayload
    payload_sha256: str
