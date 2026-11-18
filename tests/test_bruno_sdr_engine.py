import pytest
from app.services.bruno_sdr_engine import BrunoSDREngine
from app.services.sdr_policy import SDRSignals, SDRAction


class FakeProvider:
    def __init__(self):
        self.calls = []

    async def respond(self, instructions, messages):
        self.calls.append((instructions, messages))
        return "Qual material você pretende produzir?"


@pytest.mark.asyncio
async def test_uses_verified_context_without_sending():
    fake = FakeProvider()
    engine = BrunoSDREngine(fake)
    result = await engine.draft(
        history=[{"role": "user", "content": "Preciso de uma DTF"}],
        verified_product_context="DTF 6002, preço confirmado no ERP",
        approved_knowledge="Suporte técnico Doss",
        sales_dna="Abordagem consultiva",
    )
    assert result.suggested_action == SDRAction.CONTINUE
    assert "preço confirmado no ERP" in fake.calls[0][1][0]["content"]
    assert "preço confirmado no ERP" not in fake.calls[0][0]
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_opt_out_never_calls_model():
    fake = FakeProvider()
    result = await BrunoSDREngine(fake).draft(
        history=[{"role": "user", "content": "Não me procure"}],
        signals=SDRSignals(declined_contact=True),
    )
    assert result.reply == ""
    assert result.suggested_action == SDRAction.STOP
    assert fake.calls == []
