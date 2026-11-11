import json
import unittest
from types import SimpleNamespace
import httpx
from app.services.uniplus_access_audit import audit_access, schema_only

class Audit(unittest.IsolatedAsyncioTestCase):
    async def run_probe(self, resources, authorized=True, status=200, body=None):
        self.requests=[]
        def handle(r):
            self.requests.append(r)
            if r.url.host=='tenant.invalid':
                self.assertEqual(r.url.params['org_id'],'eq.org')
                self.assertEqual(r.url.params['owner_id'],'eq.owner')
                self.assertEqual(r.url.params['customer_code'],'eq.0')
                return httpx.Response(200,json=[{'org_id':'org'}] if authorized else [])
            return httpx.Response(status,json=body if body is not None else [{'codigo':123,'valor':987,'token':'secret'}])
        async def token():return 'ephemeral'
        async def rpc(*args):return {'ok':True}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            worker=SimpleNamespace(client=client,settings=SimpleNamespace(SUPABASE_URL='https://tenant.invalid'),headers={},rpc=rpc,access_token=token)
            return await audit_access(worker,{'org_id':'org','owner_id':'owner','payload':{'api_access_probe':resources}})

    async def test_schema_only_and_get_only(self):
        result=await self.run_probe(['service_orders'])
        self.assertTrue(all(r.method=='GET' for r in self.requests))
        self.assertEqual(self.requests[-1].url.params['limit'],'1')
        self.assertEqual(result['api_access_audit'][0]['state'],'json_response')
        self.assertNotIn('secret',json.dumps(result))
        self.assertNotIn('987',json.dumps(result))

    async def test_customer_only_access_fails_before_erp(self):
        with self.assertRaises(ValueError):await self.run_probe(['service_orders'],authorized=False)
        self.assertEqual(len(self.requests),1)

    async def test_arbitrary_paths_and_invalid_payload_rejected_before_network(self):
        for value in [['https://other.invalid'],['../oauth/token'],['service_orders']*7,[],{},['service_orders',{}]]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.run_probe(value)
            self.assertEqual(self.requests,[])

    async def test_http_failures_remain_distinct(self):
        for status,state in [(401,'unauthorized'),(403,'forbidden'),(404,'not_found'),(429,'rate_limited'),(500,'http_error')]:
            result=await self.run_probe(['service_orders'],status=status)
            self.assertEqual(result['api_access_audit'][0]['state'],state)

    def test_openapi_exposes_only_get_paths(self):
        result=schema_only({'paths':{'/public-api/v1/ordem-servico':{'get':{'example':'secret'}},'/oauth/token':{'get':{}},'/public-api/v1/davs':{'post':{}}},'secret':'never'})
        self.assertEqual(result['get_routes'],['/public-api/v1/ordem-servico'])
        self.assertNotIn('secret',json.dumps(result))
