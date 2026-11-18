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

    def test_plain_reais_and_bare_price_cannot_bypass_validation(self):
        for reply in ("A tinta custa 190 reais.", "A tinta custa 190.", "São BRL 190."):
            with self.subTest(reply=reply), self.assertRaisesRegex(ValueError, "ink_price_mismatch"):
                ensure_verified_ink_price_reply(reply, price_confirmed=True,
                                                verified_context="Preço aprovado consultado: R$ 160.00.")

    def test_numeric_reais_matches_approved_price(self):
        self.assertIn("160 reais", ensure_verified_ink_price_reply(
            "A tinta custa 160 reais.", price_confirmed=True,
            verified_context="Preço aprovado consultado: R$ 160.00.",
        ))

    def test_customer_price_is_allowed_only_as_attributed_history(self):
        history = [{"role": "user", "content": "Hoje pago R$ 190,00 por litro."}]
        reply = "Você paga R$ 190,00 por litro. O preço consultado da nossa tinta é R$ 160,00."
        self.assertEqual(reply, ensure_verified_ink_price_reply(
            reply, price_confirmed=True, verified_context="Preço aprovado consultado: R$ 160.00.",
            customer_history=history,
        ))
        with self.assertRaisesRegex(ValueError, "ink_price_mismatch"):
            ensure_verified_ink_price_reply(
                "Você paga R$ 190,00 por litro. Nossa tinta custa R$ 190,00.",
                price_confirmed=True, verified_context="Preço aprovado consultado: R$ 160.00.",
                customer_history=history,
            )

    def test_customer_attribution_does_not_authorize_invented_history(self):
        with self.assertRaisesRegex(ValueError, "unverified_ink_price"):
            ensure_verified_ink_price_reply("Você paga R$ 190,00.", price_confirmed=False)

    def test_customer_spend_can_be_acknowledged_when_erp_unavailable(self):
        reply = "Você gasta R$ 300,00 por mês. Ainda não tenho o preço vigente."
        self.assertEqual(reply, ensure_verified_ink_price_reply(
            reply, price_confirmed=False,
            customer_history=[{"role": "user", "content": "Gasto 300 reais por mês."}],
        ))

    def test_written_price_requires_numeric_verification(self):
        with self.assertRaisesRegex(ValueError, "unverifiable_written_ink_price"):
            ensure_verified_ink_price_reply("Custa cento e noventa reais.", price_confirmed=False)

    def test_brazilian_integer_thousands_are_not_decimal(self):
        self.assertIn("1.250", ensure_verified_ink_price_reply(
            "A tinta custa R$ 1.250.", price_confirmed=True,
            verified_context="Preço aprovado consultado: R$ 1250.00.",
        ))

