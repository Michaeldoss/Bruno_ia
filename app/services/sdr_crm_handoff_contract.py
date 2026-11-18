"""Validate the proposed CRM continuation event before a trusted CRM writer acts.

No CRM writes or WhatsApp sends occur here. A future adapter must verify
the CRM's actual schema, current assignee, authorization and idempotency.
"""
from dataclasses import dataclass
from enum import Enum


class NextAction(str, Enum):
    CONTINUE = "continue"
    HUMAN_HANDOFF = "human_handoff"
    FOLLOW_UP = "follow_up"
    STOP = "stop"


@dataclass(frozen=True)
class CRMContinuation:
    org_id: str
    conversation_id: str
    contact_id: str
    message_id: str
    action: NextAction
    owner_id: str
    product_code: str = ""
    reason: str = ""
    next_action_at: str = ""


def validate_crm_continuation(event: CRMContinuation) -> CRMContinuation:
    for field in ("org_id", "conversation_id", "contact_id", "message_id", "owner_id"):
        if not getattr(event, field).strip():
            raise ValueError("missing_" + field)
    if event.action == NextAction.HUMAN_HANDOFF and not event.reason.strip():
        raise ValueError("missing_handoff_reason")
    if event.action == NextAction.FOLLOW_UP and not event.next_action_at.strip():
        raise ValueError("missing_followup_schedule")
    if event.product_code and not event.product_code.isdigit():
        raise ValueError("invalid_product_code")
    return event


def continuation_idempotency_key(event: CRMContinuation) -> str:
    validate_crm_continuation(event)
    # Stable business key, not a database primary key.
    return "|".join((event.org_id, event.conversation_id,
                     event.message_id, event.action.value))
