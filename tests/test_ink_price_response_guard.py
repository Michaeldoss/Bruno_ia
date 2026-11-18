import unittest
from app.services.ink_price_response_guard import ensure_verified_ink_price_reply


class InkPriceGuardTests(unittest.TestCase):
    def test_unverified_price_rejected(self):
        with self.assertRaisesRegex(ValueError, "unverified_ink_price"):
            ensure_verified_ink_price_reply("A tinta custa R$ 160,00.", price_confirmed=False)

    def test_conversation_without_price_accepted(self):
        self.assertIn("máquina", ensure_verified_ink_price_reply(
            "Ainda não tenho preço confirmado. Qual sua máquina?",
            price_confirmed=False,
        ))

    def test_approved_price_allowed(self):
        self.assertIn("R$ 160", ensure_verified_ink_price_reply(
            "O preço confirmado é R$ 160,00.", price_confirmed=True,
        ))
