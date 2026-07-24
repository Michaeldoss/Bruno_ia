import unittest
from unittest.mock import AsyncMock, patch

from app.services.twilio_client import _normalize_e164
from app.services.doss_crm_client import _normalize_phone, _valid_name, escalate_to_human


class PhoneNormalizationTests(unittest.TestCase):
    def test_normaliza_celular_brasileiro(self):
        self.assertEqual(_normalize_e164("(48) 99999-0000"), "+5548999990000")
        self.assertEqual(_normalize_phone("whatsapp:+55 (48) 99999-0000"), "5548999990000")

    def test_rejeita_telefone_invalido(self):
        with self.assertRaises(ValueError):
            _normalize_e164("123")


class LeadValidationTests(unittest.TestCase):
    def test_rejeita_telefone_como_nome(self):
        self.assertFalse(_valid_name("5548999990000", "5548999990000"))
        self.assertFalse(_valid_name("123456", "5548999990000"))

    def test_aceita_nome_real(self):
        self.assertTrue(_valid_name("João da Silva", "5548999990000"))


class HandoffGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_nao_chama_crm_enquanto_qualifica(self):
        with patch("app.services.doss_crm_client._post_with_retry", new=AsyncMock()) as post:
            result = await escalate_to_human(
                phone="5548999990000",
                name="João da Silva",
                summary="Conversa ainda em andamento",
                produto="DTF Têxtil",
                finalizado=False,
            )

        post.assert_not_awaited()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "qualification_in_progress")
        self.assertEqual(result["handoff_status"], "not_started")
        self.assertIsNone(result["agent_name"])


if __name__ == "__main__":
    unittest.main()
