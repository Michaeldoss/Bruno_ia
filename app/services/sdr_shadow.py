"""Read-only SDR shadow runner. No CRM or WhatsApp side effects.

Only invoke from an explicit QA command with synthetic or approved histories.
Never enable on inbound production traffic without consent and privacy review.
"""
import os
from dataclasses import dataclass
from app.services.bruno_sdr_engine import BrunoSDREngine, SDRResult
from app.services.llm_provider import LLMSettings, OpenAIProvider
from app.services.sdr_policy import SDRSignals
from app.services.sdr_context_sources import load_sales_dna, load_approved_knowledge


@dataclass(frozen=True)
class ShadowEvaluation:
    result: SDRResult
    sent: bool = False
    persisted: bool = False


async def evaluate_shadow(*, history, verified_product_context="",
                          approved_knowledge="", sales_dna="",
                          signals=SDRSignals(), org_id=None,
                          load_company_context=False) -> ShadowEvaluation:
    if os.getenv("BRUNO_SDR_SHADOW_ENABLED", "false").lower() != "true":
        raise RuntimeError("shadow_mode_disabled")
    if load_company_context:
        if not org_id:
            raise ValueError("org_id_required_for_company_context")
        question = next((str(m.get("content") or "") for m in reversed(history)
                         if m.get("role") == "user"), "")
        approved_knowledge = await load_approved_knowledge(question, org_id=org_id)
        sales_dna = load_sales_dna()
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
