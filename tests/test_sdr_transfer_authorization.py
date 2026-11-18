import unittest
from dataclasses import replace
from app.services.sdr_transfer_authorization import (
    TransferAuthorization, authorize_transfer,
)


class TransferAuthorizationTests(unittest.TestCase):
    def base(self):
        return TransferAuthorization(
            org_id="doss", actor_profile_id="manager",
            actor_org_id="doss", actor_active=True, actor_authenticated=True,
            conversation_org_id="doss", expected_current_agent_id="bruno",
            actual_current_agent_id="bruno", target_agent_id="seller",
            target_org_id="doss", target_active=True, human_approved=True,
        )

    def test_valid_context(self):
        self.assertIsNone(authorize_transfer(self.base()))

    def test_human_approval_required(self):
        with self.assertRaisesRegex(PermissionError, "human_transfer_approval_required"):
            authorize_transfer(replace(self.base(), human_approved=False))

    def test_actor_must_be_authenticated(self):
        with self.assertRaisesRegex(PermissionError, "authenticated_active_actor_required"):
            authorize_transfer(replace(self.base(), actor_authenticated=False))

    def test_cross_tenant_rejected(self):
        with self.assertRaisesRegex(PermissionError, "cross_tenant_transfer"):
            authorize_transfer(replace(self.base(), target_org_id="other"))

    def test_concurrent_reassignment_rejected(self):
        with self.assertRaisesRegex(PermissionError, "stale_conversation_assignment"):
            authorize_transfer(replace(self.base(), actual_current_agent_id="other"))

    def test_no_duplicate_transfer(self):
        with self.assertRaisesRegex(PermissionError, "already_assigned_to_target"):
            authorize_transfer(replace(self.base(), target_agent_id="bruno"))
