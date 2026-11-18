import unittest
from datetime import datetime, timezone
from app.services.uniplus_ink_adapter import ERPInkSnapshot, select_ink_offer, UniplusInkLookupError, normalize_crm_catalog_result


class ERPInkAdapterTests(unittest.TestCase):
    def snapshot(self, rows, **kwargs):
        return ERPInkSnapshot(
            rows=tuple(rows), org_id=kwargs.get("org_id", "doss"),
            checked_at=datetime.now(timezone.utc).isoformat(),
        )

    def test_rejects_cross_tenant_lookup(self):
        with self.assertRaisesRegex(UniplusInkLookupError, "tenant_mismatch"):
            select_ink_offer(self.snapshot([]), org_id="other", product_code="8136")

    def test_rejects_duplicate_yellow_white_code(self):
        snap = self.snapshot([
            {"codigo": "222353", "nome": "DTF YELLOW", "preco_venda": "180"},
            {"codigo": "222353", "nome": "DTF WHITE", "preco_venda": "180"},
        ])
        with self.assertRaisesRegex(UniplusInkLookupError, "ambiguous_product_code"):
            select_ink_offer(snap, org_id="doss", product_code="222353", commercial_approved=True)

    def test_requires_approval(self):
        snap = self.snapshot([{"codigo": "8136", "nome": "DGTEX BLACK", "preco_venda": "180"}])
        self.assertIn("pendente", select_ink_offer(snap, org_id="doss", product_code="8136"))

    def test_approved_exact_match(self):
        snap = self.snapshot([{"codigo": "8136", "nome": "DGTEX BLACK", "preco_venda": "180"}])
        result = select_ink_offer(snap, org_id="doss", product_code="8136", commercial_approved=True)
        self.assertIn("R$ 180.00", result)


    def test_real_bridge_catalog_shape(self):
        result = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "products": [{"product": {
                "codigo": "221935", "nome": "TINTA DTF PRIME WHITE - 1LT",
                "preco": 160.0, "inativo": 0,
            }, "available": 6.0}],
        }
        snap = normalize_crm_catalog_result(result, org_id="doss")
        text = select_ink_offer(snap, org_id="doss", product_code="221935",
                                commercial_approved=True)
        self.assertIn("R$ 160.00", text)

    def test_inactive_product_rejected(self):
        snap = self.snapshot([{
            "codigo": "221935", "nome": "DTF WHITE",
            "preco_venda": 160, "inativo": 1,
        }])
        with self.assertRaisesRegex(UniplusInkLookupError, "inactive_product"):
            select_ink_offer(snap, org_id="doss", product_code="221935",
                             commercial_approved=True)
