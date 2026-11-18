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


class SequenceProvider:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    async def respond(self, instructions, messages):
        self.calls.append((instructions, messages))
        return next(self.replies)


@pytest.mark.asyncio
async def test_untracked_promise_is_rewritten_in_same_turn():
    provider = SequenceProvider([
        "Vou consultar o preço e te retorno.",
        "A DTF de 30 cm pode atender seu início. Qual é sua cidade?",
    ])
    result = await BrunoSDREngine(provider).draft(
        history=[{"role": "user", "content": "Quanto custa a DTF?"}],
    )
    assert len(provider.calls) == 2
    assert "Qual é sua cidade?" in result.reply
    assert result.suggested_action == SDRAction.CONTINUE


@pytest.mark.asyncio
async def test_two_untracked_promises_require_human_handoff():
    provider = SequenceProvider([
        "Vou consultar o preço.",
        "Vou verificar e já te passo.",
    ])
    result = await BrunoSDREngine(provider).draft(
        history=[{"role": "user", "content": "Quanto custa a DTF?"}],
    )
    assert len(provider.calls) == 2
    assert result.reply == ""
    assert result.suggested_action == SDRAction.HANDOFF
