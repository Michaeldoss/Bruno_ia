import unittest
from datetime import datetime, timezone
from app.services.uniplus_catalog_job_lifecycle import evaluate_catalog_job
from app.services.uniplus_catalog_job_guard import CatalogRequest
from app.services.uniplus_ink_adapter import UniplusInkLookupError


class CatalogLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.request = CatalogRequest("org", "2046", "221935")
        self.job = {
            "org_id": "org", "customer_code": "2046",
            "kind": "catalog", "state": "pending",
            "payload": {"product_codes": ["221935"]},
        }

    def test_pending_is_not_a_price(self):
        result = evaluate_catalog_job(self.job, request=self.request)
        self.assertEqual(result.status, "pending")
        self.assertIsNone(result.snapshot)

    def test_blocked_is_unavailable(self):
        result = evaluate_catalog_job({**self.job, "state": "blocked"}, request=self.request)
        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.snapshot)

    def test_succeeded_requires_exact_result(self):
        job = {**self.job, "state": "succeeded", "result": {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "products": [{"product": {
                "codigo": "221935", "nome": "DTF WHITE",
                "preco": 160, "inativo": 0,
            }}],
        }}
        self.assertEqual(evaluate_catalog_job(job, request=self.request).status, "succeeded")

    def test_cross_customer_rejected_even_pending(self):
        with self.assertRaisesRegex(UniplusInkLookupError, "customer_mismatch"):
            evaluate_catalog_job({**self.job, "customer_code": "771"}, request=self.request)
