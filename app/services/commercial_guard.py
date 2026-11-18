"""Provider-independent commercial authorization boundaries."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional


@dataclass(frozen=True)
class CommercialOffer:
    product_code: str
    list_price: Decimal
    offered_price: Decimal
    approved_floor: Optional[Decimal] = None
    source: str = ""
    authorized: bool = False


def validate_offer(offer: CommercialOffer) -> None:
    if not offer.product_code.strip() or not offer.source.strip():
        raise ValueError("unverified_product_or_source")
    for price in (offer.list_price, offer.offered_price):
        if not price.is_finite() or price <= 0:
            raise ValueError("invalid_price")
    if offer.offered_price > offer.list_price:
        raise ValueError("price_above_list_requires_review")
    if offer.offered_price < offer.list_price:
        if not offer.authorized or offer.approved_floor is None:
            raise ValueError("discount_not_authorized")
        if not offer.approved_floor.is_finite() or offer.approved_floor <= 0:
            raise ValueError("invalid_approved_floor")
        if offer.offered_price < offer.approved_floor:
            raise ValueError("below_approved_floor")


def decimal_price(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError):
        raise ValueError("invalid_price") from None
    if not value.is_finite() or value <= 0:
        raise ValueError("invalid_price")
    return value
