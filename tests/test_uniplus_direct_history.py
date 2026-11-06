import unittest
from unittest.mock import AsyncMock, patch
import httpx

from app.services import uniplus_client


class DirectUniplusHistory(unittest.IsolatedAsyncioTestCase):
    async def test_sales_headers_are_customer_scoped_sorted_and_bounded(self):
        calls=[]
        def handle(request):
            calls.append(request)
            self.assertEqual(request.url.path,'/public-api/v2/venda')
            self.assertEqual(request.url.params['codigoCliente.eq'],'276')
            return httpx.Response(200,json=[
                {'idVenda':2,'documento':'000002','codigoCliente':'276','emissao':'2026-10-02','valorTotal':200},
                {'idVenda':3,'documento':'000003','codigoCliente':'276','emissao':'2026-10-03','valorTotal':300},
                {'idVenda':1,'documento':'000001','codigoCliente':'276','emissao':'2026-10-01','valorTotal':100},
            ],request=request)

        client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch.object(uniplus_client,'_headers',AsyncMock(return_value={'Authorization':'Bearer test'})),              patch.object(uniplus_client.httpx,'AsyncClient',return_value=client):
            result=await uniplus_client.list_customer_sales_headers('276',2)

        self.assertEqual(result['source'],'uniplus_direct_v2_sales_headers')
        self.assertEqual(result['sales_total'],3)
        self.assertEqual(result['sales_returned'],2)
        self.assertTrue(result['sales_truncated'])
        self.assertEqual([row['idVenda'] for row in result['sales']],[3,2])
        self.assertEqual(len(calls),1)

    async def test_sale_items_query_only_requested_sale(self):
        calls=[]
        def handle(request):
            calls.append(request)
            self.assertEqual(request.url.path,'/public-api/v2/venda-item')
            self.assertEqual(request.url.params['codigoCliente.eq'],'276')
            self.assertEqual(request.url.params['idVenda.eq'],'5556')
            return httpx.Response(200,json=[
                {'idVenda':5556,'iditem':2,'documento':'000887','codigoCliente':'276','codigoProduto':'B','descricaoProduto':'B','quantidade':1,'precoUnitario':20,'total':20},
                {'idVenda':5556,'iditem':1,'documento':'000887','codigoCliente':'276','codigoProduto':'A','descricaoProduto':'A','quantidade':1,'precoUnitario':10,'total':10},
            ],request=request)

        client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch.object(uniplus_client,'_headers',AsyncMock(return_value={'Authorization':'Bearer test'})),              patch.object(uniplus_client.httpx,'AsyncClient',return_value=client):
            result=await uniplus_client.list_customer_sale_items('276','5556')

        self.assertEqual(result['source'],'uniplus_direct_v2_sale_items')
        self.assertEqual(result['sale_id'],'5556')
        self.assertEqual(result['item_count'],2)
        self.assertEqual([row['iditem'] for row in result['items']],[1,2])
        self.assertEqual(len(calls),1)

    async def test_invalid_codes_never_contact_erp(self):
        with self.assertRaises(ValueError):
            await uniplus_client.list_customer_sales_headers('../276',40)
        with self.assertRaises(ValueError):
            await uniplus_client.list_customer_sale_items('276','x')


if __name__ == '__main__':
    unittest.main()
