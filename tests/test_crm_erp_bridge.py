"""No network, no ERP writes, no secrets. Run via unittest discovery."""
import json
import unittest
from types import SimpleNamespace
import httpx
from app.services.crm_erp_bridge import BridgeWorker, BridgeFailure, code, scalar_stock, order_summary

class Values(unittest.TestCase):
    def test_codes(self):
        for v in ['1','2077','12345678901234567890']: self.assertEqual(code(v),v)
        for v in [None,2077,'../2077','1?foo=2','https://x','-2','1.2','',True]:
            with self.subTest(v=v),self.assertRaises(BridgeFailure): code(v)
    def test_scalar_stock(self):
        for v in ['8.000',8,8.25,'-3']: self.assertEqual(scalar_stock(v),float(v))
        for v in [None,True,'NaN','Infinity',{},[], 'unknown']:
            with self.subTest(v=v),self.assertRaises(BridgeFailure): scalar_stock(v)
    def test_allowlisted_fields(self):
        r=order_summary({'codigo':'1','secret':'x','itens':[{'produto':'12','token':'never'}]})
        self.assertEqual(r,{'codigo':'1','itens':[{'produto':'12'}]})

class Bridge(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls=[]; self.outcomes=[];self.override=None;self.claimed=False
        self.job={'id':'job','lease':'lease','org_id':'org','owner_id':'owner','customer_code':'9001','kind':'inspect','payload':{}}
        self.settings=SimpleNamespace(SUPABASE_URL='https://tenant.invalid',SUPABASE_SERVICE_ROLE_KEY='test-service',UNIPLUS_FILIAL='2',UNIPLUS_LOCAL_ESTOQUE='2')
        def handle(request):
            self.calls.append(request)
            if self.override:
                result=self.override(request)
                if result is not None:return result
            if request.url.host=='tenant.invalid':
                body=json.loads(request.content)
                if body['p_operation']=='claim':
                    if self.claimed:return httpx.Response(200,json=None)
                    self.claimed=True;return httpx.Response(200,json=self.job)
                if body['p_operation']=='complete':self.outcomes.append(body['p_data'])
                return httpx.Response(200,json={'ok':True})
            path=request.url.path
            if path=='/public-api/v1/entidades/9001':return httpx.Response(200,json={'codigo':'9001','nome':'Synthetic','secret':'hidden','inativo':0})
            if path=='/public-api/v1/davs':return httpx.Response(200,json=[{'codigo':'100','cliente':'9001','data':'2026-10-20'}])
            if path=='/public-api/v1/davs/100':return httpx.Response(200,json={'codigo':'100','cliente':'9001','itens':[{'produto':'7','quantidade':2}]})
            if path=='/public-api/v1/produtos/7':return httpx.Response(200,json={'codigo':'7','nome':'Ink','inativo':0})
            if path=='/public-api/v1/saldo-estoque':return httpx.Response(200,json='9.000')
            return httpx.Response(404,json={'error':'not found'})
        self.client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        async def token():return 'test-erp-token'
        self.worker=BridgeWorker(self.client,self.settings,token)
    async def asyncTearDown(self):await self.client.aclose()
    def erp_calls(self):return [r for r in self.calls if r.url.host!='tenant.invalid']
    async def test_live_adapter_contract(self):
        await self.worker.tick()
        outcome=self.outcomes[-1]
        self.assertEqual(outcome['state'],'succeeded')
        self.assertEqual(outcome['result']['order_count'],1)
        self.assertNotIn('secret',outcome['result']['customer'])
        self.assertTrue(all(r.method=='GET' for r in self.erp_calls()))
        self.assertTrue(all(r.headers['authorization']=='Bearer test-erp-token' for r in self.erp_calls()))
    async def test_empty_queue_does_not_contact_erp(self):
        self.claimed=True;await self.worker.tick();self.assertEqual(self.erp_calls(),[])
    async def test_issue_is_closed(self):
        self.job['kind']='issue';await self.worker.tick()
        self.assertEqual(self.outcomes[-1]['error_code'],'write_adapter_not_enabled')
        self.assertEqual(self.erp_calls(),[])
    async def test_no_other_customer_data(self):
        self.override=lambda r:httpx.Response(200,json=[{'codigo':'101','cliente':'9999'}]) if r.url.path=='/public-api/v1/davs' else None
        await self.worker.tick();self.assertEqual(self.outcomes[-1]['error_code'],'erp_customer_filter_not_honored')
    async def test_missing_customer_does_not_become_empty(self):
        self.override=lambda r:httpx.Response(404,json={}) if r.url.path.startswith('/public-api/v1/entidades') else None
        await self.worker.tick();self.assertEqual(self.outcomes[-1]['error_code'],'erp_read_http_404')
    async def test_redirect_never_followed(self):
        self.override=lambda r:httpx.Response(302,headers={'location':'https://attacker.invalid'}) if r.url.path.startswith('/public-api/v1/entidades') else None
        await self.worker.tick();self.assertEqual(self.outcomes[-1]['error_code'],'erp_read_http_302')
        self.assertTrue(all(r.url.host!='attacker.invalid' for r in self.calls))
    async def test_no_arbitrary_resource(self):
        for resource in ['../oauth/token','https://evil','titulos-receber']:
            with self.assertRaises(BridgeFailure):await self.worker.read(resource)
        self.assertEqual(self.calls,[])
    async def test_catalog_uses_server_branch(self):
        self.job.update(kind='catalog',payload={'product_codes':['7'],'branch':'999','url':'https://evil'})
        await self.worker.tick();self.assertEqual(self.outcomes[-1]['state'],'succeeded')
        request=next(r for r in self.erp_calls() if r.url.path.endswith('saldo-estoque'))
        self.assertEqual(request.url.params['filial'],'2');self.assertEqual(request.url.params['localestoque'],'2')
    async def test_missing_stock_blocks(self):
        self.job.update(kind='catalog',payload={'product_codes':['7']})
        self.override=lambda r:httpx.Response(200,json={}) if r.url.path.endswith('saldo-estoque') else None
        await self.worker.tick();self.assertEqual(self.outcomes[-1]['error_code'],'stock_response_not_scalar')
    async def test_invalid_list_blocks(self):
        self.job.update(kind='catalog',payload={'product_codes':['7','7']})
        await self.worker.tick();self.assertEqual(self.outcomes[-1]['error_code'],'invalid_product_list')
    async def test_lost_lease_stops_read(self):
        def handler(r):
            if r.url.host=='tenant.invalid' and json.loads(r.content)['p_operation']=='heartbeat':return httpx.Response(200,json=None)
        self.override=handler;await self.worker.tick();self.assertEqual(self.outcomes[-1]['error_code'],'lease_lost')
    async def test_repeated_pagination_stops(self):
        rows=[{'codigo':str(i),'cliente':'9001'} for i in range(1,101)]
        self.override=lambda r:httpx.Response(200,json=rows) if r.url.path=='/public-api/v1/davs' else None
        await self.worker.tick();self.assertEqual(self.outcomes[-1]['error_code'],'erp_pagination_repeated')
    async def test_credentials_not_persisted_on_failure(self):
        def handler(r):
            if r.url.host!='tenant.invalid':raise RuntimeError('Authorization: SECRET')
        self.override=handler;await self.worker.tick()
        self.assertEqual(self.outcomes[-1]['error_code'],'bridge_read_unavailable');self.assertNotIn('SECRET',json.dumps(self.outcomes))
    async def test_readback_wrong_customer_stops(self):
        self.override=lambda r:httpx.Response(200,json={'codigo':'100','cliente':'9999'}) if r.url.path.endswith('/100') else None
        await self.worker.tick();self.assertEqual(self.outcomes[-1]['error_code'],'erp_customer_mismatch')

if __name__=='__main__':unittest.main()
