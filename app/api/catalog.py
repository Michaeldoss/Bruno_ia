"""Authenticated, bounded read-only catalog used by the existing CRM worker."""
import asyncio
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.services.uniplus_client import read_product_catalog, validate_catalog_codes

router = APIRouter()
CATALOG_TIMEOUT_SECONDS = 7.0
NO_STORE = {"Cache-Control": "no-store"}


async def _catalog_result(product_codes, query):
    from app.services.sheets_client import (
        lookup_supplies_candidates, is_specific_supply_query, supply_name_matches_query,
    )

    candidates = []
    if query:
        candidates = await lookup_supplies_candidates(query, limit=4)
        if len(candidates) != 1 or not is_specific_supply_query(query):
            return {"source": "supplies_catalog", "price_scope": "catalog",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "match_status": "ambiguous" if candidates else "not_found",
                    "candidates": candidates, "products": []}
        product_codes = [candidates[0]["codigo"]]

    result = await read_product_catalog(product_codes)
    if query:
        actual_name = result["products"][0]["product"]["nome"]
        if (not supply_name_matches_query(query, actual_name)
                or not supply_name_matches_query(candidates[0]["nome"], actual_name)):
            raise RuntimeError("catalog_name_mismatch")
    return dict(result, match_status="exact", candidates=candidates)


@router.get("/api/uniplus/catalog")
async def uniplus_catalog(request: Request, product_codes: str = "", query: str = ""):
    """Only the existing installation credential may access product prices."""
    expected = get_settings().BRUNO_API_KEY
    supplied = request.headers.get("x-bruno-key", "")
    if not expected or expected == "stub" or not hmac.compare_digest(supplied.encode(), expected.encode()):
        return JSONResponse(status_code=401, headers=NO_STORE, content={"error": "Nao autorizado"})
    query = query.strip()
    try:
        if bool(product_codes) == bool(query) or len(query) > 300:
            raise ValueError("provide_product_codes_or_query")
        codes = validate_catalog_codes(product_codes.split(",")) if product_codes else []
    except ValueError as exc:
        return JSONResponse(status_code=400, headers=NO_STORE, content={"error": str(exc)})
    try:
        result = await asyncio.wait_for(_catalog_result(codes, query), timeout=CATALOG_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return JSONResponse(status_code=504, headers=NO_STORE, content={"error": "catalog_timeout"})
    except Exception:
        return JSONResponse(status_code=502, headers=NO_STORE, content={"error": "catalog_unavailable"})
    return JSONResponse(headers=NO_STORE, content={"ok": True, "result": result})
