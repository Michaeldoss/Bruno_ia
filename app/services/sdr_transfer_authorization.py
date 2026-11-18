"""Authorization preflight for a future trusted CRM transfer executor.

Bruno may suggest a transfer but must not impersonate a human or call a
SECURITY DEFINER function with service credentials. The executor validates
the active CRM session and re-reads current assignment before invoking v3.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TransferAuthorization:
    org_id: str
    actor_profile_id: str
    actor_org_id: str
    actor_active: bool
    actor_authenticated: bool
    conversation_org_id: str
    expected_current_agent_id: str
    actual_current_agent_id: str
    target_agent_id: str
    target_org_id: str
    target_active: bool
    human_approved: bool


def authorize_transfer(context: TransferAuthorization) -> None:
    if not context.actor_authenticated or not context.actor_active:
        raise PermissionError("authenticated_active_actor_required")
    if not context.human_approved:
        raise PermissionError("human_transfer_approval_required")
    if not context.actor_profile_id:
        raise PermissionError("actor_profile_missing")
    if not context.org_id or any(
        org != context.org_id for org in (
            context.actor_org_id, context.conversation_org_id, context.target_org_id,
        )
    ):
        raise PermissionError("cross_tenant_transfer")
    if not context.target_active or not context.target_agent_id:
        raise PermissionError("invalid_transfer_target")
    if not context.expected_current_agent_id or (
        context.expected_current_agent_id != context.actual_current_agent_id
    ):
        raise PermissionError("stale_conversation_assignment")
    if context.target_agent_id == context.actual_current_agent_id:
        raise PermissionError("already_assigned_to_target")
