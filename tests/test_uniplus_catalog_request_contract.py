import unittest
from dataclasses import replace
from app.services.uniplus_catalog_request_contract import (
    CatalogLookupIntent, catalog_request_key, catalog_job_payload,
)


class CatalogIntentTests(unittest.TestCase):
    def intent(self):
        return CatalogLookupIntent(
            org_id="dafa7ea5-08c4-44dd-886e-d58905fca38c",
            owner_id="aa5e61b1-a4dd-4905-9e77-6ab447b61a9f",
            customer_code="2046",
            conversation_id="11111111-1111-4111-8111-111111111111",
            contact_id="22222222-2222-4222-8222-222222222222",
            source_message_id="33333333-3333-4333-8333-333333333333",
            product_code="221935",
        )

    def test_same_message_same_key(self):
        self.assertEqual(catalog_request_key(self.intent()), catalog_request_key(self.intent()))

    def test_new_message_new_key(self):
        original = self.intent()
        self.assertNotEqual(catalog_request_key(original), catalog_request_key(
            replace(original, source_message_id="44444444-4444-4444-8444-444444444444")
        ))

    def test_payload_uses_observed_crm_schema(self):
        self.assertEqual(catalog_job_payload(self.intent()), {
            "product_codes": ["221935"], "request_context": "bruno_ink_sdr",
        })

    def test_invalid_identity_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_org_id"):
            catalog_request_key(replace(self.intent(), org_id="not-uuid"))
