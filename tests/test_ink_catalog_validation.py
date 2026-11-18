import unittest
from app.services.ink_catalog_validation import audit_ink_rows, allow_erp_offer


class InkCatalogTests(unittest.TestCase):
    def test_duplicate_dtf_sku_from_2026_spreadsheet(self):
        issues = audit_ink_rows([
            {"codigo": "222353", "nome": "TINTA DTF PRIME YELLOW"},
            {"codigo": "222353", "nome": "TINTA DTF PRIME WHITE"},
        ])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "duplicate_code")
        self.assertFalse(allow_erp_offer(
            source="uniplus", confirmed_at="2026-11-18",
            commercial_approved=True, catalog_issues=issues
        ))

    def test_historical_sheet_is_not_live_erp(self):
        self.assertFalse(allow_erp_offer(
            source="sheet_2026", confirmed_at="2026-08-12", commercial_approved=True
        ))

    def test_requires_commercial_approval_and_timestamp(self):
        self.assertFalse(allow_erp_offer(source="uniplus", confirmed_at="", commercial_approved=True))
        self.assertFalse(allow_erp_offer(source="uniplus", confirmed_at="2026-11-18", commercial_approved=False))
