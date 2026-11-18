"""Read-only verification of Doss CRM transfer postconditions.

CRM transfer v3 runs as an authenticated active profile (auth.uid) and writes
history atomically. Never invoke it using a service-role shortcut or fake actor.
A trusted CRM backend must execute RPC and re-read rows in the same org.
"""
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class TransferReceipt:
    org_id: str
    conversation_id: str
    contact_id: str
    target_agent_id: str
    target_instance: str
    handoff_recorded: bool


def verify_transfer_receipt(*, expected_org_id: str, expected_conversation_id: str,
                            expected_contact_id: str, expected_agent_id: str,
                            conversation: Mapping[str, object],
                            handoff: Mapping[str, object] | None) -> TransferReceipt:
    if not all((expected_org_id, expected_conversation_id,
                expected_contact_id, expected_agent_id)):
        raise ValueError("missing_transfer_identity")
    if conversation.get("org_id") != expected_org_id:
        raise ValueError("transfer_org_mismatch")
    if conversation.get("id") != expected_conversation_id:
        raise ValueError("transfer_conversation_mismatch")
    if conversation.get("contact_id") != expected_contact_id:
        raise ValueError("transfer_contact_mismatch")
    if conversation.get("agent_id") != expected_agent_id:
        raise ValueError("transfer_not_assigned")
    instance = conversation.get("whatsapp_instance")
    if not isinstance(instance, str) or not instance:
        raise ValueError("transfer_instance_missing")
    if not isinstance(handoff, Mapping) or (
        handoff.get("org_id") != expected_org_id
        or handoff.get("conversation_id") != expected_conversation_id
        or handoff.get("contact_id") != expected_contact_id
        or handoff.get("to_agent_id") != expected_agent_id
        or handoff.get("to_instance") != instance
    ):
        raise ValueError("transfer_history_not_confirmed")
    return TransferReceipt(
        expected_org_id, expected_conversation_id, expected_contact_id,
        expected_agent_id, instance, True,
    )
