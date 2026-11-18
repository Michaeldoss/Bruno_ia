"""End-to-end handoff dry-run: validates context, identity and CRM v3 request.

No RPC, database write, outbound WhatsApp send or human approval occurs.
The authorized CRM executor must independently re-read state and permissions.
"""
from dataclasses import dataclass
from app.services.sdr_crm_handoff_contract import (
    CRMContinuation, NextAction, continuation_idempotency_key,
)
from app.services.sdr_crm_projection import project_continuation, CRMWritePlan
from app.services.sdr_crm_transfer_gate import (
    TransferRequest, prepare_transfer_rpc,
)
from app.services.sdr_transfer_authorization import (
    TransferAuthorization, authorize_transfer,
)


@dataclass(frozen=True)
class HandoffDryRun:
    idempotency_key: str
    crm_plan: CRMWritePlan
    rpc_name: str
    rpc_arguments: dict
    simulated: bool = True


def simulate_ink_handoff(*, event: CRMContinuation, lead_id: str,
                         current_agent_id: str, target_agent_id: str,
                         authorization: TransferAuthorization) -> HandoffDryRun:
    if event.action != NextAction.HUMAN_HANDOFF:
        raise ValueError("handoff_action_required")
    if event.org_id != authorization.org_id:
        raise PermissionError("authorization_org_mismatch")
    if current_agent_id != authorization.actual_current_agent_id:
        raise PermissionError("stale_conversation_assignment")
    if target_agent_id != authorization.target_agent_id:
        raise PermissionError("authorization_target_mismatch")
    authorize_transfer(authorization)
    plan = project_continuation(
        event, lead_id=lead_id, expected_agent_id=current_agent_id,
        target_agent_id=target_agent_id,
    )
    rpc_name, rpc_args = prepare_transfer_rpc(TransferRequest(
        org_id=event.org_id,
        conversation_id=event.conversation_id,
        target_agent_id=target_agent_id,
        note=event.reason,
    ))
    return HandoffDryRun(
        idempotency_key=continuation_idempotency_key(event),
        crm_plan=plan,
        rpc_name=rpc_name,
        rpc_arguments=rpc_args,
    )
