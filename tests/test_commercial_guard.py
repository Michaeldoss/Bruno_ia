from decimal import Decimal
import pytest
from app.services.commercial_guard import CommercialOffer, validate_offer


def offer(price, *, authorized=False, floor=None):
    return CommercialOffer("DTF6002", Decimal("100000"), Decimal(price),
                           approved_floor=Decimal(floor) if floor else None,
                           source="uniplus", authorized=authorized)


def test_list_price_allowed():
    validate_offer(offer("100000"))


def test_discount_requires_explicit_authorization():
    with pytest.raises(ValueError, match="discount_not_authorized"):
        validate_offer(offer("95000"))


def test_authorized_floor():
    validate_offer(offer("95000", authorized=True, floor="94000"))
    with pytest.raises(ValueError, match="below_approved_floor"):
        validate_offer(offer("93000", authorized=True, floor="94000"))


def test_invalid_price_rejected():
    with pytest.raises(ValueError, match="invalid_price"):
        validate_offer(offer("NaN"))
