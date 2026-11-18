"""Pure validation for the existing Doss CRM transfer RPC.

The authorized CRM backend remains the only executor. This module neither
calls transfer_conversation_assignment_v3 nor modifies database rows.
"""
from dataclasses import dataclass
from typing import Mapping


RPC_NAME = "transfer_conversation_assignment_v3"


@dataclass(frozen=True)
class TransferRequest:
    org_id: str
    conversation_id: str
    target_agent_id: str
    note: str


def prepare_transfer_rpc(request: TransferRequest) -> tuple[str, dict]:
    if not all((request.org_id.strip(), request.conversation_id.strip(),
                request.target_agent_id.strip(), request.note.strip())):
        raise ValueError("incomplete_transfer")
    return RPC_NAME, {
        "p_conversation_id": request.conversation_id,
        "p_target_agent_id": request.target_agent_id,
        "p_note": request.note,
    }


def verify_transfer_result(result: Mapping[str, object], request: TransferRequest) -> bool:
    """Checks RPC output identity; caller must additionally re-read assignment.

    RPC result does not expose org_id or current conversation.agent_id, so
    it cannot independently prove authorization or durable completion.
    """
    if not isinstance(result, Mapping):
        return False
    return (
        result.get("conversation_id") == request.conversation_id
        and result.get("target_agent_id") == request.target_agent_id
        and isinstance(result.get("pipeline_leads_updated"), int)
        and not isinstance(result.get("pipeline_leads_updated"), bool)
        and result["pipeline_leads_updated"] >= 0
    )
