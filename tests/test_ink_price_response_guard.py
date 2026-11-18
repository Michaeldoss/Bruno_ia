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
            "O preço confirmado é R$ 160,00.", price_confirmed=True, verified_context="Preço aprovado consultado: R$ 160.00.",
        ))

    def test_different_amount_is_blocked_even_with_erp_approval(self):
        with self.assertRaisesRegex(ValueError, "ink_price_mismatch"):
            ensure_verified_ink_price_reply(
                "O valor é R$ 190,00.", price_confirmed=True,
                verified_context="Preço aprovado consultado: R$ 160.00.",
            )

    def test_no_approved_context_blocks_price(self):
        with self.assertRaisesRegex(ValueError, "missing_approved_price_context"):
            ensure_verified_ink_price_reply("R$ 160,00", price_confirmed=True)

    def test_same_amount_in_brazilian_format_is_allowed(self):
        result = ensure_verified_ink_price_reply(
            "A tinta sai por R$ 1.250,00.",
            price_confirmed=True,
            verified_context="Preço aprovado consultado: R$ 1250.00.",
        )
        self.assertIn("1.250,00", result)
