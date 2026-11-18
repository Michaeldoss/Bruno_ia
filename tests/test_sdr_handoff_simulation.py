import unittest
from dataclasses import replace
from app.services.sdr_crm_handoff_contract import CRMContinuation, NextAction
from app.services.sdr_transfer_authorization import TransferAuthorization
from app.services.sdr_handoff_simulation import simulate_ink_handoff


class HandoffDryRunTests(unittest.TestCase):
    def setUp(self):
        self.event = CRMContinuation(
            org_id="doss", conversation_id="conv-123", contact_id="contact-123",
            message_id="msg-123", action=NextAction.HUMAN_HANDOFF,
            owner_id="seller-123", product_code="221935",
            reason="Cliente quer proposta de tinta DTF",
        )
        self.authorization = TransferAuthorization(
            org_id="doss", actor_profile_id="manager",
            actor_org_id="doss", actor_active=True, actor_authenticated=True,
            conversation_org_id="doss", expected_current_agent_id="bruno",
            actual_current_agent_id="bruno", target_agent_id="seller-123",
            target_org_id="doss", target_active=True, human_approved=True,
        )

    def run_dry(self, **kwargs):
        defaults = dict(
            event=self.event, lead_id="lead-123", current_agent_id="bruno",
            target_agent_id="seller-123", authorization=self.authorization,
        )
        defaults.update(kwargs)
        return simulate_ink_handoff(**defaults)

    def test_handoff_prepared_without_side_effects(self):
        plan = self.run_dry()
        self.assertTrue(plan.simulated)
        self.assertEqual(plan.rpc_name, "transfer_conversation_assignment_v3")
        self.assertEqual(plan.rpc_arguments["p_conversation_id"], "conv-123")
        self.assertEqual(plan.crm_plan.lead_match["contact_id"], "contact-123")
        self.assertEqual(plan.crm_plan.conversation_fields["transfer_pending_agent_id"], "seller-123")
        self.assertEqual(plan.idempotency_key, self.run_dry().idempotency_key)

    def test_stale_assignment_rejected(self):
        with self.assertRaisesRegex(PermissionError, "stale_conversation_assignment"):
            self.run_dry(authorization=replace(
                self.authorization, actual_current_agent_id="other",
            ))

    def test_unapproved_transfer_rejected(self):
        with self.assertRaisesRegex(PermissionError, "human_transfer_approval_required"):
            self.run_dry(authorization=replace(
                self.authorization, human_approved=False,
            ))

    def test_cross_tenant_rejected(self):
        with self.assertRaisesRegex(PermissionError, "cross_tenant_transfer"):
            self.run_dry(authorization=replace(
                self.authorization, target_org_id="other",
            ))
