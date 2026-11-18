"""Same-turn ink SDR orchestration, without transport or database side effects.

The caller injects an authenticated, authorized ERP bridge lookup. No stale
fallback, no background promises, and no customer message is sent here.
"""
import asyncio
from dataclasses import dataclass
from typing import Callable, Awaitable, Mapping, Sequence
from app.services.bruno_sdr_engine import BrunoSDREngine, SDRResult
from app.services.uniplus_ink_adapter import (
    normalize_crm_catalog_result, select_ink_offer, UniplusInkLookupError,
)
from app.services.sdr_policy import SDRSignals, SDRAction
from app.services.ink_price_response_guard import ensure_verified_ink_price_reply


@dataclass(frozen=True)
class InkTurn:
    result: SDRResult
    price_confirmed: bool
    lookup_status: str


async def draft_ink_turn(*, engine: BrunoSDREngine,
                         history: Sequence[Mapping[str, str]],
                         org_id: str, product_code: str,
                         lookup: Callable[[str], Awaitable[Mapping[str, object]]],
                         commercial_approved: bool = False,
                         timeout_seconds: float = 8.0,
                         approved_knowledge: str = "",
                         sales_dna: str = "",
                         signals: SDRSignals = SDRSignals()) -> InkTurn:
    if not org_id or not product_code or not product_code.isdigit():
        raise ValueError("invalid_ink_lookup_request")
    context = "Preço vigente não confirmado. Não invente preço nem prometa consulta futura."
    status = "unavailable"
    confirmed = False
    try:
        raw = await asyncio.wait_for(lookup(product_code), timeout=timeout_seconds)
        snapshot = normalize_crm_catalog_result(raw, org_id=org_id)
        context = select_ink_offer(
            snapshot, org_id=org_id, product_code=product_code,
            commercial_approved=commercial_approved,
        )
        confirmed = context.startswith("Produto ERP código ")
        status = "confirmed" if confirmed else "unapproved"
    except asyncio.TimeoutError:
        status = "timeout"
    except (UniplusInkLookupError, ValueError, TypeError):
        status = "invalid"
    result = await engine.draft(
        history=history,
        verified_product_context=context,
        approved_knowledge=approved_knowledge,
        sales_dna=sales_dna,
        signals=signals,
    )
    try:
        ensure_verified_ink_price_reply(result.reply, price_confirmed=confirmed, verified_context=context)
    except ValueError:
        result = SDRResult(reply="", suggested_action=SDRAction.HANDOFF)
        status = "unsafe_reply"
    return InkTurn(result=result, price_confirmed=confirmed, lookup_status=status)
