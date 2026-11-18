import unittest
from datetime import datetime, timezone, timedelta
from app.services.ink_offer_context import VerifiedInkOffer, build_ink_offer_context


class InkOfferContextTests(unittest.TestCase):
    def offer(self, **overrides):
        data = dict(
            product_code="8136", product_name="DGTEX PREMIUM BLACK 1L",
            price_brl="180.00", checked_at=datetime.now(timezone.utc).isoformat(),
            source="uniplus", commercial_approved=True,
        )
        data.update(overrides)
        return VerifiedInkOffer(**data)

    def test_recent_approved_erp_offer(self):
        self.assertIn("R$ 180.00", build_ink_offer_context(self.offer()))

    def test_old_spreadsheet_is_not_current_price(self):
        self.assertIn("indisponível", build_ink_offer_context(self.offer(source="sheet_2026")))

    def test_expired_snapshot_rejected(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.assertIn("desatualizada", build_ink_offer_context(self.offer(checked_at=old)))

    def test_no_approval_rejected(self):
        self.assertIn("pendente", build_ink_offer_context(self.offer(commercial_approved=False)))

    def test_invalid_price_rejected(self):
        self.assertIn("inválidos", build_ink_offer_context(self.offer(price_brl="-1")))
