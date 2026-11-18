import unittest
from app.services.sdr_crm_handoff_contract import CRMContinuation, NextAction
from app.services.sdr_crm_projection import project_continuation


class CRMProjectionTests(unittest.TestCase):
    def event(self, action=NextAction.HUMAN_HANDOFF):
        return CRMContinuation(
            org_id="org", conversation_id="conv", contact_id="contact",
            message_id="msg", action=action, owner_id="seller",
            reason="Aprovação comercial", next_action_at="2026-11-19T14:00:00-03:00",
        )

    def test_transfer_is_pending_not_forced(self):
        plan = project_continuation(self.event(), lead_id="lead",
                                    expected_agent_id="bruno", target_agent_id="human")
        self.assertEqual(plan.conversation_match["agent_id"], "bruno")
        self.assertEqual(plan.conversation_fields, {"transfer_pending_agent_id": "human"})
        self.assertEqual(plan.lead_fields["owner_id"], "seller")
        self.assertTrue(plan.requires_existing_crm_writer)

    def test_rejects_unverified_followup_writer(self):
        with self.assertRaisesRegex(ValueError, "followup_task_writer_required"):
            project_continuation(self.event(NextAction.FOLLOW_UP),
                                 lead_id="lead", expected_agent_id="bruno")

    def test_cannot_transfer_to_same_agent(self):
        with self.assertRaisesRegex(ValueError, "invalid_handoff_target"):
            project_continuation(self.event(), lead_id="lead",
                                 expected_agent_id="bruno", target_agent_id="bruno")
