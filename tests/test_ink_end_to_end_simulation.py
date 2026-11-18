import unittest
from datetime import datetime, timezone
from app.services.bruno_sdr_engine import BrunoSDREngine
from app.services.ink_turn_orchestrator import draft_ink_turn
from app.services.sdr_policy import SDRAction


class FakeProvider:
    def __init__(self, response):
        self.response = response
        self.messages = None

    async def respond(self, instructions, messages):
        self.messages = messages
        return self.response


async def fake_catalog(_code):
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "products": [{
            "product": {
                "codigo": "221935", "nome": "TINTA DTF PRIME WHITE - 1LT",
                "preco": 160.0, "inativo": 0,
            }, "available": 6,
        }],
    }


class InkEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_question_verified_erp_draft(self):
        provider = FakeProvider("A DTF Prime White de 1 litro está R$ 160,00. Qual máquina você usa?")
        outcome = await draft_ink_turn(
            engine=BrunoSDREngine(provider),
            history=[{"role": "user", "content": "Quanto custa a tinta branca DTF?"}],
            org_id="doss", product_code="221935", lookup=fake_catalog,
            commercial_approved=True,
        )
        self.assertEqual(outcome.lookup_status, "confirmed")
        self.assertEqual(outcome.result.suggested_action, SDRAction.CONTINUE)
        self.assertIn("160,00", outcome.result.reply)

    async def test_wrong_amount_handoffs_without_sending(self):
        provider = FakeProvider("A DTF Prime White custa R$ 190,00. Qual máquina você usa?")
        outcome = await draft_ink_turn(
            engine=BrunoSDREngine(provider),
            history=[{"role": "user", "content": "Quanto custa a tinta branca DTF?"}],
            org_id="doss", product_code="221935", lookup=fake_catalog,
            commercial_approved=True,
        )
        self.assertEqual(outcome.lookup_status, "unsafe_reply")
        self.assertEqual(outcome.result.reply, "")
        self.assertEqual(outcome.result.suggested_action, SDRAction.HANDOFF)
