import asyncio
import json
import unittest
from types import SimpleNamespace

import httpx

from app.services.uniplus_orders import UniplusOrders

SETTINGS = SimpleNamespace(SUPABASE_URL='https://db.example', SUPABASE_SERVICE_ROLE_KEY='test-service',
                           UNIPLUS_CLIENT_ID='test-client', UNIPLUS_CLIENT_SECRET='test-secret')
DAY = '2026-10-20'
ROW = {'codigo': 10510, 'cliente': '357', 'vendedor': '315', 'tipoDocumento': 6,
       'status': 1, 'data': DAY, 'cnpjCpfCliente': '12345678000190', 'observacao': 'not sent to CRM'}


class UniplusOrderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.imports = []
        self.finishes = []
        self.erp_status = 200
        self.lease_valid = True
        self.rows = [ROW, ROW, {**ROW, 'codigo': 1, 'status': 3},
                     {**ROW, 'codigo': 2, 'tipoDocumento': 7}, {**ROW, 'codigo': 3, 'data': '2020-01-01'}]

        def handler(request):
            self.requests.append(request)
            if request.url.host == 'db.example':
                body = json.loads(request.content)
                op = body['p_operation']
                if op == 'claim':
                    return httpx.Response(200, json={'lease': 'lease', 'dates': [DAY, '2026-10-19']})
                if op == 'heartbeat':
                    return httpx.Response(200, json={'ok': self.lease_valid})
                if op == 'import':
                    self.imports.append(body['p_data']['dav'])
                    return httpx.Response(200, json={'status': 'created'})
                if op == 'finish':
                    self.finishes.append(body['p_data'])
                    return httpx.Response(200, json={'ok': True})
                raise AssertionError(op)
            self.assertEqual(request.url.host, 'vzan-getcard01.getcard.uniplusweb.com')
            if request.url.path == '/oauth/token':
                return httpx.Response(200, json={'access_token': 'test-token', 'expires_in': 3600})
            self.assertEqual(request.method, 'GET', 'ERP is read-only')
            self.assertEqual(request.url.path, '/public-api/v1/davs')
            if self.erp_status != 200:
                return httpx.Response(self.erp_status, json={'error': 'sensitive provider body'})
            self.assertIn('data.eq', request.url.params)
            self.assertNotIn('data.gte', request.url.params)
            return httpx.Response(200, json=self.rows if request.url.params['data.eq'] == DAY else [])
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.worker = UniplusOrders(self.client, SETTINGS)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_imports_only_active_matching_date_and_deduplicates(self):
        await self.worker.tick()
        self.assertEqual(len(self.imports), 1)
        self.assertEqual(self.imports[0]['codigo'], 10510)
        self.assertNotIn('observacao', self.imports[0])
        self.assertEqual(self.finishes[-1], {'counts': {'checked': 1, 'created': 1, 'existing': 0, 'blocked': 0, 'ignored': 0}})

    async def test_token_reused_between_cycles(self):
        await self.worker.tick()
        await self.worker.tick()
        self.assertEqual(sum(r.url.path == '/oauth/token' for r in self.requests), 1)

    async def test_rate_limit_releases_cycle_with_sanitized_error(self):
        self.erp_status = 429
        await self.worker.tick()
        self.assertEqual(self.finishes[-1]['error'], 'erp_http_429')
        self.assertFalse(self.imports)
        self.erp_status = 200
        await self.worker.tick()
        self.assertEqual(len(self.imports), 1, 'next scheduled cycle can recover')

    async def test_unauthorized_token_is_invalidated(self):
        self.erp_status = 401
        await self.worker.tick()
        self.assertIsNone(self.worker.token)
        self.assertEqual(self.finishes[-1]['error'], 'erp_http_401')

    async def test_expired_lease_prevents_erp_query_and_import(self):
        self.lease_valid = False
        await self.worker.tick()
        self.assertFalse(self.imports)
        self.assertFalse(any(r.url.path == '/public-api/v1/davs' for r in self.requests))

    async def test_cancellation_preserves_lease_for_safe_expiry(self):
        async def token():
            raise asyncio.CancelledError()
        self.worker.access_token = token
        with self.assertRaises(asyncio.CancelledError):
            await self.worker.tick()
        self.assertFalse(self.finishes)
