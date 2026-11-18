"""Immutable request contract for CRM-mediated Uniplus catalog lookup.

Only the existing CRM backend may enqueue crm_erp_bridge_jobs. This contract
contains no tokens and does not write to the ERP queue.
"""
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID


@dataclass(frozen=True)
class CatalogLookupIntent:
    org_id: str
    owner_id: str
    customer_code: str
    conversation_id: str
    contact_id: str
    source_message_id: str
    product_code: str


def validate_catalog_intent(intent: CatalogLookupIntent) -> CatalogLookupIntent:
    for field in ("org_id", "owner_id", "conversation_id", "contact_id",
                  "source_message_id"):
        try:
            UUID(getattr(intent, field))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("invalid_" + field) from exc
    if not intent.customer_code.isdigit() or not intent.product_code.isdigit():
        raise ValueError("invalid_erp_code")
    return intent


def catalog_request_key(intent: CatalogLookupIntent) -> str:
    validate_catalog_intent(intent)
    fields = (
        intent.org_id, intent.owner_id, intent.customer_code,
        intent.conversation_id, intent.contact_id,
        intent.source_message_id, intent.product_code,
    )
    return sha256("\x1f".join(fields).encode("utf-8")).hexdigest()


def catalog_job_payload(intent: CatalogLookupIntent) -> dict:
    validate_catalog_intent(intent)
    return {
        "product_codes": [intent.product_code],
        "request_context": "bruno_ink_sdr",
    }
