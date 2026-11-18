"""Read-only projection into the observed Doss CRM schema (18 Nov 2026).

This is NOT an authorized database writer. Assignments must be re-read and
confirmed atomically by the existing CRM backend before any update.
"""
from dataclasses import dataclass
from app.services.sdr_crm_handoff_contract import CRMContinuation, NextAction, validate_crm_continuation


@dataclass(frozen=True)
class CRMWritePlan:
    lead_match: dict
    lead_fields: dict
    conversation_match: dict
    conversation_fields: dict
    requires_existing_crm_writer: bool = True


def project_continuation(event: CRMContinuation, *, lead_id: str,
                         expected_agent_id: str, target_agent_id: str = "") -> CRMWritePlan:
    validate_crm_continuation(event)
    if not lead_id.strip() or not expected_agent_id.strip():
        raise ValueError("missing_existing_crm_identity")
    lead_match = {
        "id": lead_id, "org_id": event.org_id,
        "contact_id": event.contact_id, "conversation_id": event.conversation_id,
    }
    conversation_match = {
        "id": event.conversation_id, "org_id": event.org_id,
        "contact_id": event.contact_id, "agent_id": expected_agent_id,
    }
    lead_fields = {"owner_id": event.owner_id}
    conversation_fields = {}
    if event.action == NextAction.HUMAN_HANDOFF:
        if not target_agent_id.strip() or target_agent_id == expected_agent_id:
            raise ValueError("invalid_handoff_target")
        # Pending transfer only. Do not silently change conversation.agent_id.
        conversation_fields = {"transfer_pending_agent_id": target_agent_id}
    elif event.action == NextAction.FOLLOW_UP:
        # No verified follow-up timestamp column on pipeline_leads.
        # A separate task writer must be used after its schema is verified.
        raise ValueError("followup_task_writer_required")
    return CRMWritePlan(lead_match, lead_fields, conversation_match, conversation_fields)
