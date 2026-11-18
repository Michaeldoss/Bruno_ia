"""Conservative conversation QA checks for SDR draft review, not live sending."""
from dataclasses import dataclass
import re
from app.services.conversation_continuity import ensure_continuity


@dataclass(frozen=True)
class QualityReview:
    accepted: bool
    issues: tuple[str, ...]


_PRICE_REQUEST = re.compile(r"\b(?:quanto\s+(?:custa|sai)|qual\s+(?:o\s+)?(?:valor|pre[cç]o)|pre[cç]o)\b", re.I)
_PRICE_REPLY = re.compile(r"(?:R\$\s*[\d.,]+|pre[cç]o|valor|or[cç]amento|condi[cç][aã]o)", re.I)
_QUESTION = re.compile(r"\?")
_FALSE_PAYBACK = re.compile(r"(?:se\s+paga\s+em\s+\d+|retorno\s+garantido)", re.I)


def review_dtf_reply(customer_message: str, reply: str) -> QualityReview:
    issues = []
    try:
        ensure_continuity(reply)
    except ValueError as exc:
        issues.append(str(exc))
    if _PRICE_REQUEST.search(customer_message) and not _PRICE_REPLY.search(reply):
        issues.append("price_question_ignored")
    if _FALSE_PAYBACK.search(reply):
        issues.append("unverified_payback")
    if len(_QUESTION.findall(reply)) > 1:
        issues.append("too_many_questions")
    if not reply.strip():
        issues.append("empty_sdr_reply")
    return QualityReview(accepted=not issues, issues=tuple(dict.fromkeys(issues)))
