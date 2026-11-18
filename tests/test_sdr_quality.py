import unittest
from app.services.sdr_quality import review_dtf_reply


class DTFQualityTests(unittest.TestCase):
    def test_does_not_ignore_price_question(self):
        result = review_dtf_reply("Quanto custa a máquina?", "Você é de qual cidade?")
        self.assertIn("price_question_ignored", result.issues)

    def test_can_acknowledge_unavailable_price_and_continue(self):
        result = review_dtf_reply(
            "Quanto custa a DTF?",
            "O preço vigente ainda não está confirmado neste atendimento. Qual é sua cidade?",
        )
        self.assertTrue(result.accepted)

    def test_does_not_promise_untracked_lookup(self):
        result = review_dtf_reply(
            "Quanto custa a DTF?",
            "Vou consultar o preço e te retorno.",
        )
        self.assertIn("untracked_future_action", result.issues)

    def test_limits_questions(self):
        result = review_dtf_reply(
            "Procuro DTF", "Qual sua cidade? Qual seu nome?"
        )
        self.assertIn("too_many_questions", result.issues)

    def test_no_unsupported_payback(self):
        result = review_dtf_reply(
            "Pago quatro reais por estampa",
            "Essa máquina se paga em 6 meses.",
        )
        self.assertIn("unverified_payback", result.issues)
