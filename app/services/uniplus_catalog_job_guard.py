"""Validate a completed CRM ERP catalog job before using its product data.

A catalog job may be customer-specific. Never reuse another customer's quote.
The CRM executor must fetch the row with server-side authorization/RLS.
"""
from dataclasses import dataclass
from typing import Mapping
from app.services.uniplus_ink_adapter import (
    ERPInkSnapshot, UniplusInkLookupError, normalize_crm_catalog_result,
)


@dataclass(frozen=True)
class CatalogRequest:
    org_id: str
    customer_code: str
    product_code: str


def validate_catalog_job(job: Mapping[str, object], *,
                         request: CatalogRequest) -> ERPInkSnapshot:
    if not request.org_id or not request.customer_code or not request.product_code.isdigit():
        raise UniplusInkLookupError("invalid_catalog_request")
    if job.get("org_id") != request.org_id:
        raise UniplusInkLookupError("tenant_mismatch")
    if str(job.get("customer_code") or "") != request.customer_code:
        raise UniplusInkLookupError("customer_mismatch")
    if job.get("kind") != "catalog" or job.get("state") != "succeeded":
        raise UniplusInkLookupError("catalog_job_not_succeeded")
    payload = job.get("payload")
    if not isinstance(payload, Mapping):
        raise UniplusInkLookupError("catalog_payload_missing")
    codes = payload.get("product_codes")
    if not isinstance(codes, list) or codes != [request.product_code]:
        raise UniplusInkLookupError("catalog_request_code_mismatch")
    result = job.get("result")
    if not isinstance(result, Mapping):
        raise UniplusInkLookupError("catalog_result_missing")
    snapshot = normalize_crm_catalog_result(result, org_id=request.org_id)
    if len(snapshot.rows) != 1 or str(snapshot.rows[0].get("codigo")) != request.product_code:
        raise UniplusInkLookupError("catalog_result_code_mismatch")
    return snapshot
