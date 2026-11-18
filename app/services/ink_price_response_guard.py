"""Block monetary amounts not explicitly authorized by the current ERP offer.

This conservative guard applies to a single ink SKU offer, not general math.
"""
import re
from decimal import Decimal, InvalidOperation

BRL_AMOUNT = re.compile(r"R\$\s*(\d[\d.,]*)", re.IGNORECASE)
VERIFIED_PRICE = re.compile(r"Preço aprovado consultado:\s*R\$\s*(\d[\d.,]*)")


def _brl_decimal(value: str) -> Decimal:
    value = value.strip().rstrip(".")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    elif value.count(".") > 1:
        value = value.replace(".", "")
    return Decimal(value)


def ensure_verified_ink_price_reply(reply: str, *, price_confirmed: bool,
                                    verified_context: str = "") -> str:
    amounts = BRL_AMOUNT.findall(reply)
    if not amounts:
        return reply
    if not price_confirmed:
        raise ValueError("unverified_ink_price_in_reply")
    match = VERIFIED_PRICE.search(verified_context)
    if not match:
        raise ValueError("missing_approved_price_context")
    try:
        approved = _brl_decimal(match.group(1))
        if not approved.is_finite() or approved <= 0:
            raise InvalidOperation()
        if any(_brl_decimal(amount) != approved for amount in amounts):
            raise ValueError("ink_price_mismatch")
    except (InvalidOperation, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) == "ink_price_mismatch":
            raise
        raise ValueError("invalid_approved_price_context") from exc
    return reply
