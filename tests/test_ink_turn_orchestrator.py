import asyncio
import unittest
import time
from datetime import datetime, timezone
from app.services.ink_turn_orchestrator import draft_ink_turn
from app.services.bruno_sdr_engine import SDRResult
from app.services.sdr_policy import SDRAction, SDRSignals


class CaptureEngine:
    def __init__(self):
        self.kwargs = None

    async def draft(self, **kwargs):
        self.kwargs = kwargs
        return SDRResult(reply="Qual é sua máquina?", suggested_action=SDRAction.CONTINUE)


class InkTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_erp_price_reaches_draft_in_same_turn(self):
        engine = CaptureEngine()

        async def lookup(code):
            self.assertEqual(code, "221935")
            return {
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "products": [{"product": {
                    "codigo": "221935", "nome": "TINTA DTF PRIME WHITE 1LT",
                    "preco": 160.0, "inativo": 0,
                }}],
            }

        outcome = await draft_ink_turn(
            engine=engine, history=[{"role": "user", "content": "Preço da branca?"}],
            org_id="doss", product_code="221935", lookup=lookup,
            commercial_approved=True,
        )
        self.assertTrue(outcome.price_confirmed)
        self.assertEqual(outcome.lookup_status, "confirmed")
        self.assertIn("R$ 160.00", engine.kwargs["verified_product_context"])

    async def test_timeout_keeps_conversation_going_without_fake_price(self):
        engine = CaptureEngine()

        async def lookup(code):
            await asyncio.sleep(0.1)
            return {}

        outcome = await draft_ink_turn(
            engine=engine, history=[{"role": "user", "content": "Preço da tinta?"}],
            org_id="doss", product_code="221935", lookup=lookup,
            timeout_seconds=0.001,
        )
        self.assertFalse(outcome.price_confirmed)
        self.assertEqual(outcome.lookup_status, "timeout")
        self.assertIn("não confirmado", engine.kwargs["verified_product_context"])

    async def test_opt_out_does_not_lookup_or_draft(self):
        engine = CaptureEngine()

        async def lookup(code):
            raise AssertionError("ERP must not be called after opt-out")

        outcome = await draft_ink_turn(
            engine=engine, history=[{"role": "user", "content": "Não me procure mais"}],
            org_id="doss", product_code="221935", lookup=lookup,
            signals=SDRSignals(declined_contact=True),
        )
        self.assertEqual(outcome.result.suggested_action, SDRAction.STOP)
        self.assertEqual(outcome.result.reply, "")
        self.assertIsNone(engine.kwargs)
        self.assertEqual(outcome.lookup_status, "skipped_opt_out")

    async def test_lookup_transport_failure_still_drafts_without_price(self):
        engine = CaptureEngine()

        async def lookup(code):
            raise OSError("Authorization: secret-token")

        outcome = await draft_ink_turn(
            engine=engine, history=[{"role": "user", "content": "Preço da tinta?"}],
            org_id="doss", product_code="221935", lookup=lookup,
        )
        self.assertEqual(outcome.lookup_status, "unavailable")
        self.assertTrue(outcome.result.reply)
        self.assertNotIn("secret-token", str(engine.kwargs))

    async def test_turn_deadline_includes_lookup_and_drafting(self):
        class SlowEngine:
            async def draft(self, **kwargs):
                await asyncio.sleep(10)

        async def lookup(code):
            await asyncio.sleep(0.02)
            return {}

        started = time.monotonic()
        outcome = await draft_ink_turn(
            engine=SlowEngine(), history=[{"role": "user", "content": "Preço da tinta?"}],
            org_id="doss", product_code="221935", lookup=lookup,
            timeout_seconds=0.03, turn_timeout_seconds=0.04,
        )
        self.assertLess(time.monotonic() - started, 0.15)
        self.assertTrue(outcome.result.reply)
        self.assertEqual(outcome.lookup_status, "draft_unavailable")

    async def test_cancellation_is_not_converted_to_a_customer_reply(self):
        async def lookup(code):
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await draft_ink_turn(
                engine=CaptureEngine(), history=[{"role": "user", "content": "Preço da tinta?"}],
                org_id="doss", product_code="221935", lookup=lookup,
            )

