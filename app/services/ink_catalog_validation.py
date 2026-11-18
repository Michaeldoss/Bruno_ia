"""Validate externally sourced ink catalogs before they become Bruno sales facts.

This validator never authorizes prices, conversions, or equipment compatibility.
"""
from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class CatalogIssue:
    code: str
    kind: str
    details: str


def audit_ink_rows(rows: Iterable[Mapping[str, str]]) -> list[CatalogIssue]:
    seen: dict[str, str] = {}
    issues: list[CatalogIssue] = []
    for row in rows:
        code = str(row.get("codigo") or "").strip()
        name = " ".join(str(row.get("nome") or "").upper().split())
        if not code or not name:
            continue
        if code in seen and seen[code] != name:
            issues.append(CatalogIssue(code, "duplicate_code", seen[code] + " | " + name))
        else:
            seen[code] = name
    return issues


def allow_erp_offer(*, source: str, confirmed_at: str, commercial_approved: bool,
                    catalog_issues: Iterable[CatalogIssue] = ()) -> bool:
    return bool(
        source == "uniplus"
        and confirmed_at.strip()
        and commercial_approved
        and not any(True for _ in catalog_issues)
    )
