import unittest
from app.services.sdr_crm_transfer_gate import (
    RPC_NAME, TransferRequest, prepare_transfer_rpc, verify_transfer_result
)


class TransferGateTests(unittest.TestCase):
    def request(self):
        return TransferRequest(
            org_id="doss", conversation_id="conversation",
            target_agent_id="human", note="Cliente pediu negociação com vendedor",
        )

    def test_uses_existing_v3_rpc(self):
        name, args = prepare_transfer_rpc(self.request())
        self.assertEqual(name, RPC_NAME)
        self.assertEqual(args["p_target_agent_id"], "human")

    def test_result_identity_must_match(self):
        result = {
            "conversation_id": "conversation", "target_agent_id": "human",
            "target_instance": "human-wa", "contact_id": "contact",
            "pipeline_leads_updated": 1,
        }
        self.assertTrue(verify_transfer_result(result, self.request()))
        self.assertFalse(verify_transfer_result({**result, "target_agent_id": "other"}, self.request()))
        self.assertFalse(verify_transfer_result({}, self.request()))

    def test_missing_note_rejected(self):
        req = self.request()
        with self.assertRaisesRegex(ValueError, "incomplete_transfer"):
            prepare_transfer_rpc(TransferRequest(req.org_id, req.conversation_id,
                                                 req.target_agent_id, ""))
