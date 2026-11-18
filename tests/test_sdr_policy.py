from app.services.sdr_policy import SDRAction, SDRSignals, decide_next_action


def test_human_request_overrides_negotiation():
    assert decide_next_action(SDRSignals(explicitly_requests_human=True, negotiation_active=True)) == SDRAction.HANDOFF


def test_expertise_or_loss_risk_handoffs():
    assert decide_next_action(SDRSignals(needs_expertise=True)) == SDRAction.HANDOFF
    assert decide_next_action(SDRSignals(risk_of_loss=True)) == SDRAction.HANDOFF


def test_negotiation_stays_with_bruno():
    assert decide_next_action(SDRSignals(negotiation_active=True, qualified=True)) == SDRAction.CONTINUE


def test_followup_requires_consent():
    assert decide_next_action(SDRSignals(undecided=True)) == SDRAction.CONTINUE
    assert decide_next_action(SDRSignals(undecided=True, followup_consented=True)) == SDRAction.FOLLOW_UP


def test_opt_out_is_final():
    assert decide_next_action(SDRSignals(declined_contact=True, risk_of_loss=True)) == SDRAction.STOP
