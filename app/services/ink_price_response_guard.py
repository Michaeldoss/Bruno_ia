"""Block unsolicited BRL amounts when no live, approved price was supplied.

This conservative guard is only for ink-offer turns, not general calculations.
"""
import re

BRL_AMOUNT = re.compile(r"R\$\s*\d[\d.,]*", re.IGNORECASE)


def ensure_verified_ink_price_reply(reply: str, *, price_confirmed: bool) -> str:
    if not price_confirmed and BRL_AMOUNT.search(reply):
        raise ValueError("unverified_ink_price_in_reply")
    return reply
