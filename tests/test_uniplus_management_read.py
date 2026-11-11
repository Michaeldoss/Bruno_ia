import unittest
import json
from types import SimpleNamespace
import httpx
from app.services.uniplus_management_read import read_resource, validate_query, matches, ManagementReadFailure

class Queries(unittest.TestCase):
    def test_rejects_unbounded_and_unapproved_queries(self):
        for patch in [{'limit':100},{'offset':-1},{'api_resource_read':'openapi_v3'},{'filters':{'url.eq':'https://external'}},{'filters':{'vencimento.gte':'bad'}},{'filters':{'tipo.eq':1}}]:
            with self.subTest(patch=patch),self.assertRaises(ManagementReadFailure):validate_query({'api_resource_read':'financial_documents_v1',**patch})
    def test_date_and_code_filter_verification(self):
        self.assertTrue(matches({'codigoFilial':2,'emissao':'2026-11-10T10:12:00'},{'codigoFilial.eq':'2','emissao.gte':'2026-11-01'}))
        self.assertFalse(matches({'codigoFilial':3},{'codigoFilial.eq':'2'}))
        self.assertFalse(matches({},{'emissao.gte':'2026-11-01'}))

class Reads(unittest.IsolatedAsyncioTestCase):
    async def run_read(self,rows,filters=None,authorized=True,resource='financial_documents_v1',offset=0):
        self.calls=[]
        def handle(r):
            self.calls.append(r)
            if r.url.host=='db.invalid':return httpx.Response(200,json=[{'org_id':'org'}] if authorized else [])
            return httpx.Response(200,json=rows)
        async def token():return 'secret'
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            w=SimpleNamespace(client=client,settings=SimpleNamespace(SUPABASE_URL='https://db.invalid'),headers={},access_token=token)
            return await read_resource(w,{'org_id':'org','owner_id':'owner','payload':{'api_resource_read':resource,'limit':2,'offset':offset,'filters':filters or {}}})
    async def test_projects_no_bank_credentials_and_keeps_pagination_explicit(self):
        r=await self.run_read([{'id':1,'valor':100,'saldo':50,'numeroContaCorrente':'private','token':'hidden'},{'id':2,'valor':200}])
        self.assertTrue(r['management_read']['possibly_more'])
        self.assertFalse(r['history_complete'])
        self.assertNotIn('private',json.dumps(r));self.assertNotIn('hidden',json.dumps(r))
        self.assertTrue(all(x.method=='GET' for x in self.calls))
    async def test_ignored_filters_fail_closed(self):
        with self.assertRaisesRegex(ManagementReadFailure,'filter_not_honored'):
            await self.run_read([{'id':1,'codigoFilial':3}],{'codigoFilial.eq':'2'})
    async def test_requires_global_authorization(self):
        with self.assertRaises(ValueError):await self.run_read([],authorized=False)
        self.assertEqual(len(self.calls),1)
    async def test_oversized_page_rejected(self):
        with self.assertRaisesRegex(ManagementReadFailure,'invalid_page'):await self.run_read([{}, {}, {}])

    async def test_financial_letter_codes_are_accepted_and_verified(self):
        r=await self.run_read([{'id':1,'tipo':'R','status':'Q'}],{'tipo.eq':'R','status.eq':'Q'})
        self.assertEqual(r['management_read']['returned'],1)
        with self.assertRaisesRegex(ManagementReadFailure,'filter_not_honored'):
            await self.run_read([{'id':2,'tipo':'P'}],{'tipo.eq':'R'})

    async def test_catalog_ignoring_pagination_is_sliced_locally(self):
        rows=[{'id':i,'codigo':str(i),'descricao':'Term '+str(i)} for i in range(217)]
        r=await self.run_read(rows,resource='payment_condition',offset=25)
        result=r['management_read']
        self.assertEqual([x['id'] for x in result['rows']],[25,26])
        self.assertEqual(result['snapshot_rows'],217)
        self.assertEqual(result['pagination_mode'],'local_catalog_snapshot')
        self.assertTrue(result['possibly_more'])
        self.assertEqual(str(self.calls[-1].url.query),'b\'\'')

    async def test_catalog_filters_are_applied_to_snapshot(self):
        r=await self.run_read([{'codigo':'1'},{'codigo':'2'}],{'codigo.eq':'2'},resource='financial_document_types')
        self.assertEqual(r['management_read']['rows'],[{'codigo':'2'}])
        self.assertFalse(r['management_read']['possibly_more'])

    async def test_catalog_maximum_is_enforced(self):
        with self.assertRaisesRegex(ManagementReadFailure,'invalid_page'):
            await self.run_read([{}]*1001,resource='payment_condition')
