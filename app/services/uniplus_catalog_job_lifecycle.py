"""Pure contract for CRM-controlled Uniplus catalog job lifecycle.

Bruno does not read/write crm_erp_bridge_jobs. An authorized CRM backend
must submit and poll a job and return a scoped result to this validator.
"""
from dataclasses import dataclass
from typing import Mapping
from app.services.uniplus_catalog_job_guard import (
    CatalogRequest, validate_catalog_job,
)
from app.services.uniplus_ink_adapter import ERPInkSnapshot, UniplusInkLookupError


@dataclass(frozen=True)
class CatalogJobOutcome:
    status: str
    snapshot: ERPInkSnapshot | None = None


def evaluate_catalog_job(job: Mapping[str, object], *,
                         request: CatalogRequest) -> CatalogJobOutcome:
    if not isinstance(job, Mapping):
        raise UniplusInkLookupError("invalid_job")
    if job.get("org_id") != request.org_id:
        raise UniplusInkLookupError("tenant_mismatch")
    if str(job.get("customer_code") or "") != request.customer_code:
        raise UniplusInkLookupError("customer_mismatch")
    if job.get("kind") != "catalog":
        raise UniplusInkLookupError("wrong_job_kind")
    payload = job.get("payload")
    if not isinstance(payload, Mapping) or payload.get("product_codes") != [request.product_code]:
        raise UniplusInkLookupError("catalog_request_code_mismatch")
    state = job.get("state")
    if state in ("pending", "running"):
        return CatalogJobOutcome("pending")
    if state in ("blocked", "uncertain"):
        return CatalogJobOutcome("unavailable")
    if state != "succeeded":
        raise UniplusInkLookupError("unknown_job_state")
    return CatalogJobOutcome(
        "succeeded", validate_catalog_job(job, request=request),
    )
