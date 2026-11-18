"""Validate prices for a single ink offer, including Brazilian notation.

Customer spending can be repeated only with explicit attribution and the same
amount in a customer message. It never authorizes a new Doss selling price.
"""
import re
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

_NUMBER = r"\d+(?:[.,]\d+)*"
_AMOUNT = re.compile(
    rf"(?:R\$|BRL)\s*(?P<prefix>{_NUMBER})"
    rf"|(?P<suffix>{_NUMBER})\s*(?:reais|real|BRL)\b"
    rf"|\b(?:custa|custam|sai\s+por|fica\s+por|pre[cç]o\s+(?:é|de)|valor\s+(?:é|de))\s+(?P<bare>{_NUMBER})",
    re.IGNORECASE,
)
BRL_AMOUNT = re.compile(rf"R\$\s*({_NUMBER})", re.IGNORECASE)
VERIFIED_PRICE = re.compile(rf"Preço aprovado consultado:\s*R\$\s*({_NUMBER})")
_CUSTOMER_ATTRIBUTION = re.compile(
    r"(?:voc[eê]\s+(?:j[aá]\s+)?(?:paga|pagou|gasta|gastou)"
    r"|(?:voc[eê]\s+)?(?:informou|comentou|disse)\s+(?:que\s+)?(?:paga|gasta|pagar|gastar))"
    r"\s+(?:(?:hoje|atualmente|cerca\s+de|aproximadamente|uns|em\s+torno\s+de)\s+)*$",
    re.IGNORECASE,
)
_SPELLED_MONEY = re.compile(
    r"\b(?:um|dois|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|"
    r"vinte|trinta|quarenta|cinquenta|sessenta|setenta|oitenta|noventa|cem|cento|mil)"
    r"(?:\s+(?:e\s+)?[a-zçãéê]+){0,8}\s+reais\b", re.IGNORECASE,
)


def _brl_decimal(value: str) -> Decimal:
    value = value.strip().rstrip(".")
    if "," in value:
        if value.count(",") != 1 or ("." in value and value.rfind(".") > value.rfind(",")):
            raise InvalidOperation()
        value = value.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", value):
        value = value.replace(".", "")
    return Decimal(value)


def _amount_value(match: re.Match) -> Decimal:
    return _brl_decimal(next(value for value in match.groups() if value is not None))


def ensure_verified_ink_price_reply(reply: str, *, price_confirmed: bool,
                                    verified_context: str = "",
                                    customer_history: Sequence[Mapping[str, str]] = ()) -> str:
    if not isinstance(reply, str):
        raise ValueError("invalid_ink_price_reply")
    amounts = list(_AMOUNT.finditer(reply))
    if _SPELLED_MONEY.search(reply):
        raise ValueError("unverifiable_written_ink_price")
    if not amounts:
        return reply
    customer_amounts = set()
    for message in customer_history:
        if message.get("role") == "user":
            for match in _AMOUNT.finditer(str(message.get("content") or "")):
                try:
                    customer_amounts.add(_amount_value(match))
                except InvalidOperation:
                    pass
    unverified = []
    try:
        for match in amounts:
            amount = _amount_value(match)
            attribution = _CUSTOMER_ATTRIBUTION.search(reply[max(0, match.start() - 140):match.start()])
            if attribution and amount in customer_amounts:
                continue
            unverified.append(amount)
        if not unverified:
            return reply
        if not price_confirmed:
            raise ValueError("unverified_ink_price_in_reply")
        match = VERIFIED_PRICE.search(verified_context)
        if not match:
            raise ValueError("missing_approved_price_context")
        approved = _brl_decimal(match.group(1))
        if not approved.is_finite() or approved <= 0:
            raise InvalidOperation()
        if any(amount != approved for amount in unverified):
            raise ValueError("ink_price_mismatch")
    except InvalidOperation as exc:
        raise ValueError("invalid_approved_price_context") from exc
    return reply

