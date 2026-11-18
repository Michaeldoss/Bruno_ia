import unittest
from datetime import datetime, timezone
from app.services.uniplus_catalog_job_guard import CatalogRequest, validate_catalog_job
from app.services.uniplus_ink_adapter import UniplusInkLookupError


class CatalogJobGuardTests(unittest.TestCase):
    def setUp(self):
        self.request = CatalogRequest("org", "2046", "221935")
        self.job = {
            "org_id": "org", "customer_code": "2046",
            "kind": "catalog", "state": "succeeded",
            "payload": {"product_codes": ["221935"]},
            "result": {
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "products": [{"product": {
                    "codigo": "221935", "nome": "DTF WHITE",
                    "preco": 160, "inativo": 0,
                }}],
            },
        }

    def test_matching_customer_and_sku(self):
        snapshot = validate_catalog_job(self.job, request=self.request)
        self.assertEqual(snapshot.rows[0]["codigo"], "221935")

    def test_cross_customer_blocked(self):
        with self.assertRaisesRegex(UniplusInkLookupError, "customer_mismatch"):
            validate_catalog_job({**self.job, "customer_code": "771"}, request=self.request)

    def test_cross_tenant_blocked(self):
        with self.assertRaisesRegex(UniplusInkLookupError, "tenant_mismatch"):
            validate_catalog_job({**self.job, "org_id": "other"}, request=self.request)

    def test_other_product_blocked(self):
        with self.assertRaisesRegex(UniplusInkLookupError, "catalog_request_code_mismatch"):
            validate_catalog_job({**self.job, "payload": {"product_codes": ["222353"]}},
                                 request=self.request)

    def test_pending_job_blocked(self):
        with self.assertRaisesRegex(UniplusInkLookupError, "catalog_job_not_succeeded"):
            validate_catalog_job({**self.job, "state": "pending"}, request=self.request)
