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
    async def test_purchase_history_limit_returns_bounded_order_details(self):
        self.job['payload']={'history_limit':12}
        def handler(r):
            if r.url.path=='/public-api/v1/davs':
                return httpx.Response(200,json=[
                    {'codigo':str(100+i),'cliente':'9001','data':'2026-10-%02d'%(20-i)}
                    for i in range(12)
                ])
            if r.url.path.startswith('/public-api/v1/davs/'):
                number=r.url.path.rsplit('/',1)[-1]
                return httpx.Response(200,json={
                    'codigo':number,'cliente':'9001','data':'2026-10-20',
                    'valor':100,'itens':[{'produto':'7','descricao':'Ink','quantidade':2,'precoUnitario':50,'precoTotal':100}]
                })
            return None
        self.override=handler
        await self.worker.tick()
        result=self.outcomes[-1]['result']
        self.assertEqual(result['order_count'],12)
        self.assertEqual(result['orders_returned'],12)
        self.assertFalse(result['orders_truncated'])
        self.assertEqual(len(result['orders']),12)

    async def test_purchase_history_limit_is_bounded(self):
        self.job['payload']={'history_limit':41}
        await self.worker.tick()
        self.assertEqual(self.outcomes[-1]['error_code'],'invalid_history_limit')

    async def test_sales_schema_probe_uses_documented_v2_routes_without_returning_values(self):
        self.job['payload']={'sales_schema_probe':True}
        def handler(r):
            if r.url.path=='/public-api/v2/venda':
                self.assertEqual(r.url.params['codigoCliente.eq'],'9001')
                return httpx.Response(200,json=[{
                    'idVenda':99,'documento':'000123','codigoCliente':'9001',
                    'emissao':'2026-11-06','status':1
                }])
            if r.url.path=='/public-api/v2/venda-item':
                self.assertEqual(r.url.params['codigoCliente.eq'],'9001')
                return httpx.Response(200,json=[{
                    'idVenda':99,'idItem':1,'codigoCliente':'9001',
                    'codigoProduto':'7','quantidade':2,'precoUnitario':50
                }])
            return None
        self.override=handler
        await self.worker.tick()
        outcome=self.outcomes[-1]
        self.assertEqual(outcome['state'],'succeeded')
        result=outcome['result']
        self.assertTrue(result['schema_probe'])
        self.assertFalse(result['history_complete'])
        self.assertEqual(result['sales_schema']['sale_fields'],
            ['codigoCliente','documento','emissao','idVenda','status'])
        self.assertEqual(result['sales_schema']['item_fields'],
            ['codigoCliente','codigoProduto','idItem','idVenda','precoUnitario','quantidade'])
        self.assertNotIn('000123',json.dumps(result))

    async def test_v2_sales_resources_do_not_accept_path_keys(self):
        with self.assertRaises(BridgeFailure):
            await self.worker.read('venda','99')
        self.assertEqual(self.calls,[])

    async def test_fiscal_purchase_history_returns_sales_and_product_rows(self):
        self.job['payload']={'purchase_history':True,'purchase_history_limit':2}
        def handler(r):
            if r.url.path=='/public-api/v2/venda':
                return httpx.Response(200,json=[
                    {'idVenda':2,'documento':'003288','codigoCliente':'9001','emissao':'2024-11-28',
                     'dataHoraEmissao':'2024-11-28T10:00:00','codigoFilial':'1','status':1,
                     'nomeCliente':'Cliente','nomeVendedor':'Venda','valorProdutos':2480,'desconto':0,'valorTotal':2480},
                    {'idVenda':1,'documento':'002880','codigoCliente':'9001','emissao':'2024-08-08',
                     'dataHoraEmissao':'2024-08-08T10:00:00','codigoFilial':'1','status':1,
                     'nomeCliente':'Cliente','nomeVendedor':'Venda','valorProdutos':1800,'desconto':100,'valorTotal':1800},
                ])
            if r.url.path=='/public-api/v2/venda-item':
                return httpx.Response(200,json=[
                    {'idVenda':2,'iditem':1,'documento':'003288','modelo':'55','codigoCliente':'9001',
                     'emissao':'2024-11-28','dataHoraEmissao':'2024-11-28T10:00:00',
                     'codigoProduto':'8360','descricaoProduto':'DESLOCAMENTO','codigoUnidadeMedida':'UN',
                     'quantidade':1220,'precoUnitario':1.5,'desconto':0,'total':1830,'valorTotal':1830,
                     'condicaoPagamento':'2','frete':0,'status':1,'tipoItem':1,'codigoFilial':'1'},
                    {'idVenda':2,'iditem':2,'documento':'003288','modelo':'55','codigoCliente':'9001',
                     'emissao':'2024-11-28','dataHoraEmissao':'2024-11-28T10:00:00',
                     'codigoProduto':'8357','descricaoProduto':'MANUTENCAO','codigoUnidadeMedida':'UN',
                     'quantidade':1,'precoUnitario':650,'desconto':0,'total':650,'valorTotal':650,
                     'condicaoPagamento':'2','frete':0,'status':1,'tipoItem':1,'codigoFilial':'1'},
                    {'idVenda':1,'iditem':1,'documento':'002880','modelo':'55','codigoCliente':'9001',
                     'emissao':'2024-08-08','dataHoraEmissao':'2024-08-08T10:00:00',
                     'codigoProduto':'221988','descricaoProduto':'TINTA DG DTF 1LT - WHITE',
                     'codigoUnidadeMedida':'LT','quantidade':5,'precoUnitario':380,'desconto':100,
                     'total':1800,'valorTotal':1800,'condicaoPagamento':'4','frete':78,'status':1,
                     'tipoItem':1,'codigoFilial':'1'},
                ])
            return None
        self.override=handler
        await self.worker.tick()
        outcome=self.outcomes[-1]
        self.assertEqual(outcome['state'],'succeeded')
        result=outcome['result']
        history=result['purchase_history']
        self.assertEqual(history['source'],'uniplus_v2_venda')
        self.assertEqual(history['sales_total'],2)
        self.assertEqual(history['sales_returned'],2)
        self.assertFalse(history['sales_truncated'])
        self.assertEqual(history['items_total_for_returned_sales'],3)
        self.assertEqual(history['sales'][0]['documento'],'003288')
        self.assertEqual(len(history['sales'][0]['items']),2)
        self.assertEqual(history['items'][0]['codigoProduto'],'8357')
        self.assertTrue(result['history_complete'])
        self.assertTrue(all(r.method=='GET' for r in self.erp_calls()))

    async def test_fiscal_purchase_history_limit_is_bounded(self):
        self.job['payload']={'purchase_history':True,'purchase_history_limit':201}
        await self.worker.tick()
        self.assertEqual(self.outcomes[-1]['error_code'],'invalid_purchase_history_limit')

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
