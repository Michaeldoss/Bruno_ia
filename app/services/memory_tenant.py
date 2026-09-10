"""Explicit scope for memory reads/writes; never infer a company from a phone."""
from uuid import UUID


def require_org(value):
    if not value:
        raise ValueError('Organization is required for memory access')
    return str(UUID(str(value)))


def require_conversation_org(conversation):
    return require_org(conversation.get('org_id'))
