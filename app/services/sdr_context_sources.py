"""Read-only Doss context sources for explicitly invoked SDR evaluations.

No ERP price, stock, payment or discount is inferred from historical documents.
"""
from pathlib import Path
from app.services.company_knowledge import approved_knowledge_context


DNA_PATH = Path(__file__).resolve().parents[2] / "docs" / "dna_vendas_michael.txt"


def load_sales_dna(max_chars: int = 6000) -> str:
    if not DNA_PATH.is_file():
        return ""
    return DNA_PATH.read_text(encoding="utf-8")[:max_chars]


async def load_approved_knowledge(question: str, *, org_id: str) -> str:
    if not question.strip():
        return ""
    return await approved_knowledge_context(question, org_id=org_id)
