import unittest
from app.services.conversation_continuity import ensure_continuity


class ConversationContinuityTests(unittest.TestCase):
    def test_rejects_dead_end_from_dtf_example(self):
        with self.assertRaisesRegex(ValueError, "untracked_future_action"):
            ensure_continuity("Vou consultar o preço vigente para te passar uma proposta real.")

    def test_accepts_conversation_with_immediate_next_step(self):
        self.assertEqual(
            ensure_continuity("Entendi seu volume. Você produz em qual cidade?"),
            "Entendi seu volume. Você produz em qual cidade?",
        )

    def test_only_confirmed_action_can_justify_future_promise(self):
        self.assertIn("Vou consultar", ensure_continuity(
            "Vou consultar o valor.", action_confirmed=True
        ))

    def test_empty_response_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty_sdr_reply"):
            ensure_continuity("  ")
