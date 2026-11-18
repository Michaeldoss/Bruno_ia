"""Deterministic SDR decision policy. No provider calls or CRM side effects.

This module is intentionally not wired to production until integration tests
verify existing pipeline and assignment semantics.
"""
from dataclasses import dataclass
from enum import Enum


class SDRAction(str, Enum):
    CONTINUE = "continue"
    FOLLOW_UP = "follow_up"
    HANDOFF = "handoff"
    STOP = "stop"


@dataclass(frozen=True)
class SDRSignals:
    explicitly_requests_human: bool = False
    needs_expertise: bool = False
    risk_of_loss: bool = False
    negotiation_active: bool = False
    undecided: bool = False
    followup_consented: bool = False
    declined_contact: bool = False
    qualified: bool = False


def decide_next_action(signals: SDRSignals) -> SDRAction:
    """Prioritize consent and human requests; never infer consent to follow up."""
    if signals.declined_contact:
        return SDRAction.STOP
    if signals.explicitly_requests_human or signals.needs_expertise or signals.risk_of_loss:
        return SDRAction.HANDOFF
    if signals.negotiation_active:
        return SDRAction.CONTINUE
    if signals.undecided and signals.followup_consented:
        return SDRAction.FOLLOW_UP
    return SDRAction.CONTINUE
