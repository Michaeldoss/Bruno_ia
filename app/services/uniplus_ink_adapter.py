"""Read-only ERP product adapter boundary for ink sales.

No URL, endpoint, token, or ERP schema is guessed. A trusted ERP bridge must
supply the snapshot; the adapter validates it and rejects ambiguous products.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping
from app.services.ink_catalog_validation import audit_ink_rows
from app.services.ink_offer_context import VerifiedInkOffer, build_ink_offer_context


class UniplusInkLookupError(ValueError):
    pass


@dataclass(frozen=True)
class ERPInkSnapshot:
    rows: tuple[Mapping[str, object], ...]
    checked_at: str
    org_id: str
    source: str = "uniplus"


def select_ink_offer(snapshot: ERPInkSnapshot, *,
                     org_id: str, product_code: str,
                     commercial_approved: bool = False) -> str:
    if not org_id or snapshot.org_id != org_id:
        raise UniplusInkLookupError("tenant_mismatch")
    if snapshot.source != "uniplus":
        raise UniplusInkLookupError("untrusted_source")
    if not product_code or not product_code.isdigit():
        raise UniplusInkLookupError("invalid_product_code")
    rows = []
    for raw in snapshot.rows:
        code = str(raw.get("codigo") or "").strip()
        name = str(raw.get("nome") or "").strip()
        rows.append({"codigo": code, "nome": name})
    conflicts = audit_ink_rows(rows)
    if any(issue.code == product_code for issue in conflicts):
        raise UniplusInkLookupError("ambiguous_product_code")
    matches = [raw for raw in snapshot.rows
               if str(raw.get("codigo") or "").strip() == product_code]
    if len(matches) != 1:
        raise UniplusInkLookupError("product_not_unique")
    row = matches[0]
    # No fuzzy match, no fallback to historical price.
    price = row.get("preco_venda")
    if price is None or isinstance(price, bool):
        raise UniplusInkLookupError("missing_sale_price")
    return build_ink_offer_context(
        VerifiedInkOffer(
            product_code=product_code,
            product_name=str(row.get("nome") or ""),
            price_brl=str(price),
            checked_at=snapshot.checked_at,
            source=snapshot.source,
            commercial_approved=commercial_approved,
        )
    )
