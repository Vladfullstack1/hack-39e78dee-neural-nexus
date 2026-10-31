from __future__ import annotations

from typing import Any, Literal

import pydantic
from pydantic import BaseModel, Field

if hasattr(pydantic, "field_validator"):
    from pydantic import field_validator
    _PYDANTIC_V2 = True
else:
    from pydantic import validator as _v1_validator
    _PYDANTIC_V2 = False


class CompatBaseModel(BaseModel):
    """BaseModel with seamless Pydantic v1 & v2 compatibility."""

    if not _PYDANTIC_V2:
        @classmethod
        def model_validate(cls, obj: Any):
            return cls.parse_obj(obj)

        def model_dump(self, **kwargs: Any) -> dict[str, Any]:
            return self.dict(**kwargs)


Role = Literal["player", "reported_player", "admin"]


class Message(CompatBaseModel):
    role: Role = "player"
    text: str = ""
    attachments: int = 0
    ts: str | None = None

    if _PYDANTIC_V2:
        @field_validator("attachments")
        @classmethod
        def non_negative_attachments(cls, value: int) -> int:
            return max(0, value)
    else:
        @_v1_validator("attachments")
        def non_negative_attachments(cls, value: int) -> int:
            return max(0, value)


class TriageRequest(CompatBaseModel):
    kind: Literal["complaint", "report"] = "report"
    messages: list[Message] = Field(default_factory=list)
    ticket_id: str | None = None
    created_at: str | None = None
    region: str | None = None
    server_lang: str | None = None
    lang: str | None = None
    reporter_hash: str | None = None
    reported_hash: str | None = None
    reported_nick: str | None = None
    rule_refs_raw: str | None = None
    images_count: int = 0
    status: int | None = None
    verdict: str | None = None
    punishment: str | None = None
    violation: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class DraftReply(CompatBaseModel):
    lang: str
    text: str
    cited: list[str] = Field(default_factory=list)


class TriageResponse(CompatBaseModel):
    ticket_id: str
    lang: str
    category: str
    priority: str
    is_spam_or_duplicate: bool
    extracted: dict[str, Any]
    draft_reply: DraftReply
    needs_human: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    retrieval: list[dict[str, Any]] = Field(default_factory=list)
    processing: dict[str, Any] = Field(default_factory=dict)


class FeedbackRequest(CompatBaseModel):
    ticket_id: str
    action: Literal["edited", "escalated"]
    original_draft: str = ""
    edited_draft: str = ""
    note: str = ""
