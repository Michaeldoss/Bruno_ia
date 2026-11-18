"""Same-turn ink SDR orchestration, without transport or database side effects.

The caller injects an authenticated, authorized ERP bridge lookup. No stale
fallback, no background promises, and no customer message is sent here.
"""
import asyncio
import logging
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Awaitable, Mapping, Sequence
from app.services.bruno_sdr_engine import BrunoSDREngine, SDRResult, continuity_fallback
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
                         turn_timeout_seconds: float = 25.0,
                         approved_knowledge: str = "",
                         sales_dna: str = "",
                         signals: SDRSignals = SDRSignals()) -> InkTurn:
    if signals.declined_contact:
        return InkTurn(SDRResult("", SDRAction.STOP), False, "skipped_opt_out")
    if not org_id or not product_code or not product_code.isdigit():
        raise ValueError("invalid_ink_lookup_request")
    if any(not math.isfinite(value) or value <= 0
           for value in (timeout_seconds, turn_timeout_seconds)):
        raise ValueError("invalid_ink_turn_timeout")
    deadline = asyncio.get_running_loop().time() + turn_timeout_seconds
    context = "Preço vigente não confirmado. Não invente preço nem prometa consulta futura."
    status = "unavailable"
    confirmed = False
    confirmed_row = None
    try:
        raw = await asyncio.wait_for(lookup(product_code), timeout=min(timeout_seconds, turn_timeout_seconds))
        snapshot = normalize_crm_catalog_result(raw, org_id=org_id)
        context = select_ink_offer(
            snapshot, org_id=org_id, product_code=product_code,
            commercial_approved=commercial_approved,
        )
        confirmed = context.startswith("Produto ERP código ")
        status = "confirmed" if confirmed else "unapproved"
        if confirmed:
            confirmed_row = next(row for row in snapshot.rows
                                 if str(row.get("codigo")) == product_code)
    except asyncio.TimeoutError:
        status = "timeout"
    except (UniplusInkLookupError, ValueError, TypeError):
        status = "invalid"
    except Exception as exc:
        logging.getLogger(__name__).warning("ink_lookup_failure:%s", type(exc).__name__)
        status = "unavailable"

    def fallback() -> SDRResult:
        result = continuity_fallback(history, signals=signals)
        if confirmed_row is not None and not signals.explicitly_requests_human:
            # A verified offer is already available, even if drafting failed.
            name = " ".join(str(confirmed_row["nome"]).split())[:160]
            price = f"{Decimal(str(confirmed_row['preco_venda'])):.2f}".replace(".", ",")
            result = SDRResult(
                f"O preço consultado de {name} é R$ {price}. "
                "Esse valor é do produto; frete e condições de pagamento precisam de confirmação.",
                SDRAction.HANDOFF,
                fallback_used=True,
            )
            try:
                ensure_verified_ink_price_reply(result.reply, price_confirmed=True,
                                                verified_context=context)
            except ValueError:
                # A malformed product label must not smuggle another amount into
                # the deterministic fallback after the first guard rejected it.
                return continuity_fallback(history, signals=signals)
        return result

    try:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        result = await asyncio.wait_for(engine.draft(
            history=history,
            verified_product_context=context,
            approved_knowledge=approved_knowledge,
            sales_dna=sales_dna,
            signals=signals,
        ), timeout=remaining)
    except Exception as exc:
        logging.getLogger(__name__).warning("ink_draft_failure:%s", type(exc).__name__)
        result = fallback()
        status = "draft_unavailable"
    if not result.reply and result.suggested_action != SDRAction.STOP:
        result = fallback()
        status = "draft_unavailable"
    elif result.fallback_used:
        result = fallback()
        status = "draft_unavailable"
    try:
        ensure_verified_ink_price_reply(result.reply, price_confirmed=confirmed,
                                       verified_context=context, customer_history=history)
    except ValueError:
        result = fallback()
        status = "unsafe_reply"
    return InkTurn(result=result, price_confirmed=confirmed, lookup_status=status)

