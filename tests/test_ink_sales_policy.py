import unittest
from app.services.ink_sales_policy import (
    InkDiscovery, InkTechnology, ink_next_question, can_assert_compatibility
)


class InkSalesPolicyTests(unittest.TestCase):
    def test_starts_with_technology_when_unknown(self):
        self.assertIn("sublimática", ink_next_question(InkDiscovery()))

    def test_asks_machine_before_price(self):
        question = ink_next_question(InkDiscovery(technology=InkTechnology.DTF_TEXTILE))
        self.assertIn("máquina", question)

    def test_asks_volume_when_ink_known(self):
        question = ink_next_question(InkDiscovery(
            technology=InkTechnology.SUBLIMATION,
            machine_model="F6370", current_ink="Outra marca",
        ))
        self.assertIn("litros", question)

    def test_compatibility_requires_explicit_technical_approval(self):
        case = InkDiscovery(technology=InkTechnology.ECO_SOLVENT, machine_model="Roland")
        self.assertFalse(can_assert_compatibility(case))
        approved = InkDiscovery(
            technology=InkTechnology.ECO_SOLVENT,
            machine_model="Roland",
            technical_compatibility_approved=True,
        )
        self.assertTrue(can_assert_compatibility(approved))
