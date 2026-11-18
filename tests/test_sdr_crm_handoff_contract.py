import unittest
from app.services.sdr_crm_handoff_contract import (
    CRMContinuation, NextAction, validate_crm_continuation, continuation_idempotency_key
)


class CRMContinuationTests(unittest.TestCase):
    def event(self, **changes):
        fields = dict(
            org_id="doss", conversation_id="conv-1", contact_id="contact-1",
            message_id="msg-1", owner_id="sales-1",
            action=NextAction.HUMAN_HANDOFF, reason="Preço precisa aprovação",
            product_code="221935",
        )
        fields.update(changes)
        return CRMContinuation(**fields)

    def test_handoff_has_owner_and_reason(self):
        self.assertEqual(validate_crm_continuation(self.event()).owner_id, "sales-1")
        with self.assertRaisesRegex(ValueError, "missing_owner_id"):
            validate_crm_continuation(self.event(owner_id=""))
        with self.assertRaisesRegex(ValueError, "missing_handoff_reason"):
            validate_crm_continuation(self.event(reason=""))

    def test_followup_needs_schedule(self):
        with self.assertRaisesRegex(ValueError, "missing_followup_schedule"):
            validate_crm_continuation(self.event(action=NextAction.FOLLOW_UP))
        self.assertEqual(validate_crm_continuation(
            self.event(action=NextAction.FOLLOW_UP, next_action_at="2026-11-19T14:00:00-03:00")
        ).action, NextAction.FOLLOW_UP)

    def test_idempotency_key_is_stable(self):
        self.assertEqual(continuation_idempotency_key(self.event()),
                         continuation_idempotency_key(self.event()))
        self.assertNotEqual(continuation_idempotency_key(self.event()),
                            continuation_idempotency_key(self.event(message_id="msg-2")))
