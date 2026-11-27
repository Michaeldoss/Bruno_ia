"""Product discovery must not turn a partial name into an incorrect ERP quote."""
import unittest
from unittest.mock import AsyncMock, patch

from app.services.sheets_client import (
    SheetsClient, is_specific_supply_query, supply_name_matches_query,
)


def row(code, name, **extra):
    return {"CÓDIGO": code, "PRODUTO": name, **extra}


class SupplyNames(unittest.TestCase):
    def test_dgtex_explicit_technology_overrides_brand_inference(self):
        for technology in ('DTF', 'UV', 'eco-solvente'):
            with self.subTest(technology=technology):
                self.assertTrue(supply_name_matches_query(
                    f'tinta {technology} branca 1L',
                    f'TINTA DGTEX {technology} WHITE 1000ml'))
        self.assertTrue(supply_name_matches_query(
            'tinta DG-tex DTF branca 1L', 'TINTA DGTEX DTF WHITE 1000ml'))

    def test_dgtex_keeps_implicit_subli_and_technology_fences(self):
        self.assertTrue(supply_name_matches_query(
            'tinta subli premium preta 1L', 'TINTA DGTEX PREMIUM BLACK 1000ml'))
        for query, name in (
            ('tinta subli branca 1L', 'TINTA DGTEX DTF WHITE 1000ml'),
            ('tinta DTF branca 1L', 'TINTA DGTEX UV DTF WHITE 1000ml'),
            ('tinta DTF branca 5L', 'TINTA DGTEX DTF WHITE 1000ml'),
            ('tinta DTF amarela 1L', 'TINTA DGTEX DTF WHITE 1000ml'),
        ):
            with self.subTest(query=query, name=name):
                self.assertFalse(supply_name_matches_query(query, name))

    def test_colour_aliases_and_accents(self):
        self.assertTrue(supply_name_matches_query(
            "Quanto sai a tinta DTF branca de 1 litro?", "TINTA DTF PRIME WHITE - 1LT"))
        self.assertTrue(supply_name_matches_query(
            "tinta sublimática preta premium 1L", "TINTA DGTEX PREMIUM BLACK 1000ML"))
        self.assertTrue(supply_name_matches_query(
            "tinta eco-solvente ciano", "TINTA DGECO CYAN 1L"))

    def test_technology_colour_line_and_size_must_match(self):
        mismatches = [
            ("tinta DTF branca", "TINTA UV FLEX WHITE 1L"),
            ("tinta DTF branca", "TINTA UV DTF WHITE 1L"),
            ("tinta UV branca", "TINTA DTF WHITE 1L"),
            ("tinta DTF branca", "TINTA DTF PRIME YELLOW 1L"),
            ("tinta subli premium preta", "TINTA DGTEX LITE BLACK 1L"),
            ("tinta DTF branca 5L", "TINTA DTF PRIME WHITE 1L"),
        ]
        for query, name in mismatches:
            with self.subTest(query=query, name=name):
                self.assertFalse(supply_name_matches_query(query, name))

    def test_observed_catalog_spelling_and_uv_family(self):
        self.assertTrue(supply_name_matches_query(
            "tinta DTF branca 1L", "TINTA DTF PRIME WITHE- 1LT"))
        self.assertTrue(supply_name_matches_query(
            "tinta UV Flex branca 1L", "TINTA DTF UV FLEX (S/R) WHITE-1.L"))
        self.assertFalse(supply_name_matches_query(
            "tinta DTF branca 1L", "TINTA DTF UV FLEX (S/R) WHITE-1.L"))

    def test_machine_model_is_not_product_or_compatibility_evidence(self):
        self.assertFalse(supply_name_matches_query("Epson F6370", "TINTA DGTEX PREMIUM BLACK 1L"))
        self.assertFalse(supply_name_matches_query("tinta para Epson F6370", "TINTA DGTEX PREMIUM BLACK 1L"))
        self.assertFalse(is_specific_supply_query("conversão Epson F6370"))

    def test_generic_query_does_not_authorize_single_quote(self):
        for query in ("DTF", "procuro tinta", "tinta branca", "tinta subli 1L", "DGtex"):
            with self.subTest(query=query):
                self.assertFalse(is_specific_supply_query(query))
        for query in ("tinta DTF branca", "tinta UV Flex", "DGtex Premium preta"):
            with self.subTest(query=query):
                self.assertTrue(is_specific_supply_query(query))

    def test_input_limits_and_no_numeric_sku_guessing(self):
        for query in (None, "", " ", "x" * 301, "221935", "procuro alguma coisa"):
            with self.subTest(query=query):
                self.assertFalse(supply_name_matches_query(query, "TINTA DTF WHITE 1L"))
                self.assertFalse(is_specific_supply_query(query))


class SupplyLookup(unittest.IsolatedAsyncioTestCase):
    async def test_dgtex_dtf_is_discovered_without_crossing_technology(self):
        self.rows[:] = [
            row('20', 'TINTA DGTEX DTF WHITE 1000ml'),
            row('21', 'TINTA DGTEX PREMIUM WHITE 1000ml'),
            row('22', 'TINTA DGTEX UV DTF WHITE 1000ml'),
        ]
        result = await self.client.lookup_supplies_candidates('tinta DTF branca 1L')
        self.assertEqual(result, [{'codigo': '20', 'nome': 'TINTA DGTEX DTF WHITE 1000ml'}])

    async def asyncSetUp(self):
        self.client = SheetsClient()
        self.rows = [
            row("10", "TINTA DTF PRIME WHITE 1LT", CUSTO="SECRET", COMISSAO="SECRET", PRECO="999"),
            row("11", "TINTA DTF PRIME WHITE 5LT"),
            row("12", "TINTA DTF PRIME YELLOW 1LT"),
            row("13", "TINTA UV FLEX WHITE 1LT"),
            row("14", "TINTA DGTEX PREMIUM BLACK 1LT"),
        ]
        self.fetch = AsyncMock(return_value=self.rows)
        self.client.get_supplies = self.fetch

    async def test_natural_name_returns_only_code_and_name(self):
        result = await self.client.lookup_supplies_candidates("preciso tinta DTF branca 1L")
        self.assertEqual(result, [{"codigo": "10", "nome": "TINTA DTF PRIME WHITE 1LT"}])
        self.assertNotIn("SECRET", str(result))
        self.assertNotIn("999", str(result))

    async def test_missing_package_stays_ambiguous(self):
        result = await self.client.lookup_supplies_candidates("tinta DTF branca")
        self.assertEqual({item["codigo"] for item in result}, {"10", "11"})

    async def test_generic_technology_returns_choices(self):
        result = await self.client.lookup_supplies_candidates("procuro DTF")
        self.assertEqual(len(result), 3)
        self.assertNotIn("13", {item["codigo"] for item in result})

    async def test_conflicting_sku_excluded_before_matching(self):
        self.rows.extend([
            row("222353", "TINTA DTF PRIME WHITE 1LT"),
            row("222353", "TINTA DTF PRIME YELLOW 1LT"),
        ])
        result = await self.client.lookup_supplies_candidates("tinta DTF branca 1L")
        self.assertEqual([item["codigo"] for item in result], ["10"])

    async def test_identical_duplicate_deduplicates(self):
        self.rows.append(row("10", "  tinta dtf prime white 1lt  "))
        result = await self.client.lookup_supplies_candidates("tinta DTF branca 1L")
        self.assertEqual([item["codigo"] for item in result], ["10"])

    async def test_excel_integer_codes_are_normalized_without_rounding(self):
        self.rows[:] = [
            row("10.0", "TINTA DTF PRIME WHITE 1LT"),
            row("11.5", "TINTA DTF PRIME WHITE 1LT"),
            row("1e4", "TINTA DTF PRIME WHITE 1LT"),
        ]
        result = await self.client.lookup_supplies_candidates("tinta DTF branca 1L")
        self.assertEqual([item["codigo"] for item in result], ["10"])

    async def test_invalid_rows_and_conversion_notes_do_not_become_products(self):
        self.rows.extend([
            row("../14", "TINTA DTF WHITE 1LT"),
            row("Epson F6370", "CONVERSÃO DE PLACA"),
            row("", "TINTA DTF WHITE 1LT"),
            row("99", ""),
            {None: ["extra"], "CÓDIGO": "100", "PRODUTO": "TINTA DTF BLACK 1LT"},
            None,
        ])
        result = await self.client.lookup_supplies_candidates("tinta DTF branca 1L")
        self.assertEqual([item["codigo"] for item in result], ["10"])

    async def test_invalid_query_and_limit_do_not_fetch_sheet(self):
        for query, limit in (("", 4), ("x" * 301, 4), ("DTF", 0), ("DTF", 5), ("DTF", True)):
            self.assertEqual(await self.client.lookup_supplies_candidates(query, limit), [])
        self.fetch.assert_not_called()

    async def test_bounded_choices_and_no_match(self):
        self.assertEqual(len(await self.client.lookup_supplies_candidates("tinta", 4)), 4)
        self.assertEqual(await self.client.lookup_supplies_candidates("tinta DTF magenta"), [])

    async def test_limit_one_does_not_disguise_ambiguity(self):
        self.assertEqual(await self.client.lookup_supplies_candidates("tinta DTF branca", 1), [])
        result = await self.client.lookup_supplies_candidates("tinta DTF branca 1L", 1)
        self.assertEqual([item["codigo"] for item in result], ["10"])

    async def test_uses_configured_sheet_url(self):
        self.client = SheetsClient()
        self.client.csv_url_suprimentos = "https://configured.invalid/supplies.csv"
        with patch.object(self.client, "_fetch_csv", AsyncMock(return_value=self.rows)) as fetch:
            await self.client.lookup_supplies_candidates("tinta DTF branca 1L")
        fetch.assert_awaited_once_with("https://configured.invalid/supplies.csv", "SUPRIMENTOS")


if __name__ == "__main__":
    unittest.main()
