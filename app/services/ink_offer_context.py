"""Build conservative customer-facing ink context from validated ERP snapshots.

An ERP adapter must provide exact product matches and approval. This module
performs no network calls and does not claim that the ERP is already connected.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from app.services.ink_catalog_validation import CatalogIssue, allow_erp_offer


@dataclass(frozen=True)
class VerifiedInkOffer:
    product_code: str
    product_name: str
    price_brl: str
    checked_at: str
    source: str = "uniplus"
    commercial_approved: bool = False


def build_ink_offer_context(offer: VerifiedInkOffer, *,
                            issues: tuple[CatalogIssue, ...] = ()) -> str:
    if not offer.product_code.strip() or not offer.product_name.strip():
        return "Oferta de tinta indisponível: produto não identificado."
    try:
        checked = datetime.fromisoformat(offer.checked_at.replace("Z", "+00:00"))
        if checked.tzinfo is None:
            return "Oferta de tinta indisponível: horário de consulta sem fuso."
        age_seconds = (datetime.now(timezone.utc) - checked.astimezone(timezone.utc)).total_seconds()
        if age_seconds < -300 or age_seconds > 900:
            return "Oferta de tinta indisponível: consulta desatualizada."
        price = Decimal(offer.price_brl)
        if not price.is_finite() or price <= 0:
            raise InvalidOperation()
    except (ValueError, InvalidOperation):
        return "Oferta de tinta indisponível: preço ou data inválidos."
    if not allow_erp_offer(
        source=offer.source, confirmed_at=offer.checked_at,
        commercial_approved=offer.commercial_approved, catalog_issues=issues,
    ):
        return "Oferta de tinta indisponível: validação comercial pendente."
    return (
        f"Produto ERP código {offer.product_code}: {offer.product_name}. "
        f"Preço aprovado consultado: R$ {price:.2f}. "
        f"Consulta UTC: {checked.astimezone(timezone.utc).isoformat()}. "
        "Não presume estoque, prazo, frete, desconto ou compatibilidade técnica."
    )
