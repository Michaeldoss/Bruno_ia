import unittest
from app.services.sdr_context_sources import load_sales_dna


class SDRContextSourceTests(unittest.TestCase):
    def test_sales_dna_exists(self):
        text = load_sales_dna()
        self.assertIn("DNA DE VENDAS", text)
        self.assertLessEqual(len(text), 6000)

    def test_bounded_dna(self):
        self.assertLessEqual(len(load_sales_dna(150)), 150)
