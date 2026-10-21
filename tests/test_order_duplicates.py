import unittest
import httpx
from app.services.crm_erp_bridge import BridgeFailure, duplicate_candidates
import test_crm_erp_bridge as base

class Duplicates(unittest.TestCase):
    def sample(self):
        return {'codigo':'88001','cliente':'9001','data':'2026-10-21','tipoDocumento':6,'status':1,
                'valorFrete':70,'valor':1270,'itens':[{'produto':'7','quantidade':2,'precoUnitario':150}]}
    def check(self, rows):return duplicate_candidates(rows,'9001',[{'product_code':'7','quantity':2}],'2026-10-20')
    def test_freight_difference_does_not_hide_duplicate(self):
        self.assertEqual(self.check([self.sample()])[0]['codigo'],'88001')
    def test_price_difference_does_not_hide_duplicate(self):
        row=self.sample();row['itens'][0]['precoUnitario']=180;self.assertEqual(len(self.check([row])),1)
    def test_additional_items_still_require_review(self):
        row=self.sample();row['itens'].append({'produto':'8','quantidade':1});self.assertEqual(len(self.check([row])),1)
    def test_canceled_order_is_ignored(self):
        row=self.sample();row['status']=3;self.assertEqual(self.check([row]),[])
    def test_old_purchase_is_not_this_request(self):
        row=self.sample();row['data']='2026-09-01';self.assertEqual(self.check([row]),[])
    def test_wrong_customer_blocks(self):
        row=self.sample();row['cliente']='9999'
        with self.assertRaises(BridgeFailure):self.check([row])
    def test_missing_details_block_absence_claim(self):
        row=self.sample();row.pop('itens')
        with self.assertRaises(BridgeFailure):self.check([row])
    def test_bad_request_is_rejected(self):
        with self.assertRaises(BridgeFailure):duplicate_candidates([],'9001',[{'product_code':'7','quantity':True}],'2026-10-20')
        with self.assertRaises(BridgeFailure):duplicate_candidates([],'9001',[{'product_code':'7','quantity':2}],'2026-02-31')

class DuplicateIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await base.Bridge.asyncSetUp(self)
    async def asyncTearDown(self):
        await base.Bridge.asyncTearDown(self)
    async def test_candidate_scan_reaches_persisted_result(self):
        self.job['payload']={'duplicate_check':{'source_date':'2026-10-20','items':[{'product_code':'7','quantity':2}]}}
        self.override=lambda r:httpx.Response(200,json={'codigo':'100','cliente':'9001','data':'2026-10-20','tipoDocumento':6,'status':1,'valorFrete':70,'valor':370,'itens':[{'produto':'7','quantidade':2,'precoUnitario':150}]}) if r.url.path.endswith('/100') else None
        await self.worker.tick()
        result=self.outcomes[-1]['result']['duplicate_check']
        self.assertEqual(result['state'],'existing_order_requires_review')
        self.assertFalse(result['issuance_authorized'])
        self.assertEqual(str(result['candidates'][0]['codigo']),'100')
    async def test_overlarge_scan_cannot_claim_no_duplicate(self):
        self.job['payload']={'duplicate_check':{'source_date':'2026-10-20','items':[{'product_code':'7','quantity':2}]}}
        rows=[{'codigo':str(i),'cliente':'9001','data':'2026-10-21'} for i in range(31)]
        self.override=lambda r:httpx.Response(200,json=rows) if r.url.path=='/public-api/v1/davs' else None
        await self.worker.tick()
        self.assertEqual(self.outcomes[-1]['error_code'],'duplicate_scan_too_large')
    async def test_invalid_date_cannot_bypass_gate(self):
        self.job['payload']={'duplicate_check':{'source_date':'bad','items':[{'product_code':'7','quantity':2}]}}
        await self.worker.tick()
        self.assertEqual(self.outcomes[-1]['error_code'],'invalid_order_source_date')

if __name__=='__main__':unittest.main()
