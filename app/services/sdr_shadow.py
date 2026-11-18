"""Read-only SDR shadow runner. No CRM or WhatsApp side effects.

Only invoke from an explicit QA command with synthetic or approved histories.
Never enable on inbound production traffic without consent and privacy review.
"""
import os
from dataclasses import dataclass
from app.services.bruno_sdr_engine import BrunoSDREngine, SDRResult
from app.services.llm_provider import LLMSettings, OpenAIProvider
from app.services.sdr_policy import SDRSignals


@dataclass(frozen=True)
class ShadowEvaluation:
    result: SDRResult
    sent: bool = False
    persisted: bool = False


async def evaluate_shadow(*, history, verified_product_context="",
                          approved_knowledge="", sales_dna="",
                          signals=SDRSignals()) -> ShadowEvaluation:
    if os.getenv("BRUNO_SDR_SHADOW_ENABLED", "false").lower() != "true":
        raise RuntimeError("shadow_mode_disabled")
    provider = OpenAIProvider(LLMSettings.from_environment())
    engine = BrunoSDREngine(provider)
    result = await engine.draft(
        history=history,
        verified_product_context=verified_product_context,
        approved_knowledge=approved_knowledge,
        sales_dna=sales_dna,
        signals=signals,
    )
    return ShadowEvaluation(result=result)
