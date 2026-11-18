import unittest
from app.services.sdr_crm_transfer_receipt import verify_transfer_receipt


class ReceiptTests(unittest.TestCase):
    def fixtures(self):
        conv = dict(id="conv", org_id="org", contact_id="contact",
                    agent_id="human", whatsapp_instance="human-wa")
        handoff = dict(org_id="org", conversation_id="conv", contact_id="contact",
                       to_agent_id="human", to_instance="human-wa")
        return conv, handoff

    def verify(self, conv, handoff):
        return verify_transfer_receipt(
            expected_org_id="org", expected_conversation_id="conv",
            expected_contact_id="contact", expected_agent_id="human",
            conversation=conv, handoff=handoff,
        )

    def test_confirmed_transfer(self):
        conv, handoff = self.fixtures()
        self.assertTrue(self.verify(conv, handoff).handoff_recorded)

    def test_wrong_owner_or_tenant_rejected(self):
        conv, handoff = self.fixtures()
        with self.assertRaisesRegex(ValueError, "transfer_not_assigned"):
            self.verify({**conv, "agent_id": "bruno"}, handoff)
        with self.assertRaisesRegex(ValueError, "transfer_org_mismatch"):
            self.verify({**conv, "org_id": "other"}, handoff)

    def test_missing_history_rejected(self):
        conv, handoff = self.fixtures()
        with self.assertRaisesRegex(ValueError, "transfer_history_not_confirmed"):
            self.verify(conv, None)
        with self.assertRaisesRegex(ValueError, "transfer_history_not_confirmed"):
            self.verify(conv, {**handoff, "to_instance": "wrong"})
