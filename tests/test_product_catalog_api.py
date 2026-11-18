import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app.api import catalog
from app.services import uniplus_client


class ProductCatalogRead(unittest.IsolatedAsyncioTestCase):
    async def read(self, override=None):
        self.calls = []
        def handle(request):
            self.calls.append(request)
            if override:
                response = override(request)
                if response is not None:
                    return response
            if request.url.path.endswith("/produtos/221935"):
                return httpx.Response(200, json={
                    "codigo": "221935", "nome": "TINTA DTF PRIME WHITE - 1LT",
                    "preco": 160, "unidadeMedida": "UN", "inativo": 0,
                    "custo": 77, "secret": "must not leave server",
                })
            return httpx.Response(200, json="6.000")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch.object(uniplus_client, "_headers", AsyncMock(return_value={"Authorization": "Bearer test"})), \
             patch.object(uniplus_client.httpx, "AsyncClient", return_value=client):
            return await uniplus_client.read_product_catalog(["221935"])

    async def test_projects_current_price_and_server_stock_scope(self):
        result = await self.read()
        product = result["products"][0]
        self.assertEqual(product["product"]["preco"], 160)
        self.assertEqual(product["available"], 6)
        self.assertEqual(set(product["product"]), {"codigo", "nome", "preco", "unidadeMedida", "inativo"})
        self.assertEqual(result["price_scope"], "catalog")
        self.assertTrue(result["checked_at"])
        self.assertTrue(all(request.method == "GET" for request in self.calls))
        self.assertTrue(all(request.url.params["filial"] == uniplus_client.UNIPLUS_FILIAL for request in self.calls))
        self.assertEqual(self.calls[-1].url.params["localestoque"], uniplus_client.UNIPLUS_LOCAL_ESTOQUE)

    async def test_unknown_stock_does_not_become_zero(self):
        for payload in ({}, None, True, "NaN", "Infinity"):
            with self.subTest(payload=payload):
                result = await self.read(lambda request: httpx.Response(200, json=payload)
                                         if request.url.path.endswith("saldo-estoque") else None)
                self.assertIsNone(result["products"][0]["available"])

    async def test_price_unavailable_never_becomes_zero(self):
        for value in (None, True, 0, -1, "NaN", "Infinity"):
            with self.subTest(value=value):
                with self.assertRaises((ValueError, RuntimeError)):
                    await self.read(lambda request: httpx.Response(200, json={
                        "codigo": "221935", "nome": "Tinta", "preco": value,
                        "inativo": 0, "unidadeMedida": "UN",
                    }) if "/produtos/" in request.url.path else None)

    async def test_wrong_product_and_inactive_product_are_rejected(self):
        for fields in ({"codigo": "222353"}, {"inativo": 1}, {"inativo": None}):
            with self.subTest(fields=fields), self.assertRaises(RuntimeError):
                await self.read(lambda request: httpx.Response(200, json={
                    "codigo": "221935", "nome": "Tinta", "preco": 160,
                    "inativo": 0, "unidadeMedida": "UN", **fields,
                }) if "/produtos/" in request.url.path else None)

    async def test_redirect_is_rejected_without_following(self):
        with self.assertRaises(httpx.HTTPStatusError):
            await self.read(lambda request: httpx.Response(302, headers={"Location": "https://other.invalid"}))
        self.assertTrue(all(request.url.host != "other.invalid" for request in self.calls))

    async def test_invalid_codes_do_not_fetch_credentials(self):
        for codes in ([], ["7", "7"], ["../7"], ["٧"], [7], ["1", "2", "3", "4", "5"]):
            with self.subTest(codes=codes), patch.object(uniplus_client, "_headers", AsyncMock()) as headers:
                with self.assertRaises(ValueError):
                    await uniplus_client.read_product_catalog(codes)
                headers.assert_not_awaited()


class ProductCatalogAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = FastAPI()
        app.include_router(catalog.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.settings = patch.object(catalog, "get_settings", return_value=SimpleNamespace(BRUNO_API_KEY="test-key"))
        self.settings.start()

    async def asyncTearDown(self):
        self.settings.stop()
        await self.client.aclose()

    async def get(self, params, authorized=True):
        return await self.client.get("/api/uniplus/catalog", params=params,
                                     headers={"x-bruno-key": "test-key"} if authorized else {})

    async def test_authentication_precedes_every_lookup(self):
        with patch.object(catalog, "_catalog_result", AsyncMock()) as lookup:
            response = await self.get({"product_codes": "221935"}, authorized=False)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["cache-control"], "no-store")
        lookup.assert_not_awaited()

    async def test_invalid_combination_rejected_before_network(self):
        for params in ({}, {"product_codes": "1", "query": "tinta"}, {"product_codes": "1,1"}, {"query": "x" * 301}):
            with self.subTest(params=params), patch.object(catalog, "_catalog_result", AsyncMock()) as lookup:
                response = await self.get(params)
                self.assertEqual(response.status_code, 400)
                lookup.assert_not_awaited()

    async def test_total_deadline_returns_explicit_unavailable_state(self):
        async def never(*args):
            await asyncio.sleep(60)
        with patch.object(catalog, "_catalog_result", never), patch.object(catalog, "CATALOG_TIMEOUT_SECONDS", 0.01):
            response = await self.get({"product_codes": "221935"})
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json()["error"], "catalog_timeout")

    async def test_upstream_errors_do_not_expose_credentials(self):
        with patch.object(catalog, "_catalog_result", AsyncMock(side_effect=RuntimeError("secret-token"))):
            response = await self.get({"product_codes": "221935"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret-token", response.text)

    async def test_ambiguous_name_does_not_read_or_publish_a_price(self):
        with patch("app.services.sheets_client.lookup_supplies_candidates", AsyncMock(return_value=[
            {"codigo": "1", "nome": "TINTA DTF WHITE"}, {"codigo": "2", "nome": "TINTA DTF CYAN"},
        ])), patch.object(catalog, "read_product_catalog", AsyncMock()) as read:
            response = await self.get({"query": "procuro DTF"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["match_status"], "ambiguous")
        self.assertEqual(response.json()["result"]["products"], [])
        read.assert_not_awaited()

    async def test_query_binds_sheet_candidate_to_current_erp_product_name(self):
        with patch("app.services.sheets_client.lookup_supplies_candidates", AsyncMock(return_value=[
            {"codigo": "221935", "nome": "TINTA DTF PRIME WHITE"},
        ])), patch.object(catalog, "read_product_catalog", AsyncMock(return_value={
            "products": [{"product": {"codigo": "221935", "nome": "TINTA UV YELLOW"}}],
        })):
            response = await self.get({"query": "tinta DTF branca"})
        self.assertEqual(response.status_code, 502)

    async def test_resolved_line_and_packaging_cannot_change_in_erp(self):
        for actual_name in ("TINTA DTF PRIME WHITE - 5LT", "TINTA DTF PREMIUM WHITE - 1LT"):
            with self.subTest(actual_name=actual_name), \
                 patch("app.services.sheets_client.lookup_supplies_candidates", AsyncMock(return_value=[
                     {"codigo": "221935", "nome": "TINTA DTF PRIME WHITE - 1LT"},
                 ])), patch.object(catalog, "read_product_catalog", AsyncMock(return_value={
                     "products": [{"product": {"codigo": "221935", "nome": actual_name}}],
                 })):
                response = await self.get({"query": "tinta DTF branca"})
                self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
