"""Deterministic DTF lead scenario; no network calls or messages sent."""
import unittest
from app.services.sdr_policy import SDRSignals, SDRAction, decide_next_action


class DTFInboundScenario(unittest.TestCase):
    def test_new_lead_stays_with_bruno(self):
        # Customer: "Procuro DTF". No qualification or negotiation yet.
        self.assertEqual(decide_next_action(SDRSignals()), SDRAction.CONTINUE)

    def test_negotiation_does_not_force_handoff(self):
        self.assertEqual(
            decide_next_action(SDRSignals(qualified=True, negotiation_active=True)),
            SDRAction.CONTINUE,
        )

    def test_difficult_objection_handoffs(self):
        self.assertEqual(
            decide_next_action(SDRSignals(qualified=True, risk_of_loss=True)),
            SDRAction.HANDOFF,
        )

    def test_followup_requires_permission(self):
        self.assertEqual(
            decide_next_action(SDRSignals(undecided=True)),
            SDRAction.CONTINUE,
        )
        self.assertEqual(
            decide_next_action(SDRSignals(undecided=True, followup_consented=True)),
            SDRAction.FOLLOW_UP,
        )


if __name__ == "__main__":
    unittest.main()
