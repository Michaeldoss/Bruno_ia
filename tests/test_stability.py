import unittest
from unittest.mock import AsyncMock, patch

from app.services.twilio_client import _normalize_e164
from app.services.doss_crm_client import _normalize_phone, _valid_name


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


if __name__ == "__main__":
    unittest.main()
