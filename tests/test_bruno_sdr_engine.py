import asyncio
import time
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
        "O preço vigente da DTF ainda não está confirmado. Qual é sua cidade?",
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
    assert "preço vigente" in result.reply
    assert "Vou consultar" not in result.reply
    assert result.fallback_used
    assert result.suggested_action == SDRAction.HANDOFF


@pytest.mark.asyncio
async def test_provider_failure_produces_nonempty_reply_without_exception_text():
    class BrokenProvider:
        async def respond(self, **kwargs):
            raise RuntimeError("Authorization: secret-token")

    result = await BrunoSDREngine(BrokenProvider()).draft(
        history=[{"role": "user", "content": "Quanto custa a tinta?"}],
    )
    assert result.reply
    assert "secret-token" not in result.reply
    assert result.suggested_action == SDRAction.HANDOFF


@pytest.mark.asyncio
async def test_retries_share_one_total_deadline():
    class SlowProvider:
        calls = 0

        async def respond(self, **kwargs):
            self.calls += 1
            await asyncio.sleep(0.04)
            return "Vou consultar o preço."

    provider = SlowProvider()
    started = time.monotonic()
    result = await BrunoSDREngine(provider, timeout_seconds=0.06).draft(
        history=[{"role": "user", "content": "Quanto custa a tinta?"}],
    )
    assert provider.calls == 2
    assert time.monotonic() - started < 0.12
    assert result.reply
    assert result.fallback_used


@pytest.mark.asyncio
async def test_cancellation_propagates_without_reply():
    class CancelledProvider:
        async def respond(self, **kwargs):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await BrunoSDREngine(CancelledProvider()).draft(
            history=[{"role": "user", "content": "Preciso de tinta"}],
        )


@pytest.mark.asyncio
async def test_repair_feedback_reports_actual_quality_issue():
    provider = SequenceProvider(["Qual cidade? Qual seu nome?", "Qual aplicação você usa?"])
    await BrunoSDREngine(provider).draft(
        history=[{"role": "user", "content": "Preciso de tinta"}],
    )
    assert "too_many_questions" in provider.calls[1][1][-1]["content"]

