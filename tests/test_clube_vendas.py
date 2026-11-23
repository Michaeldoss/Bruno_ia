import httpx
import pytest
from app.services.clube_vendas import sales_page,matching_key

def key():
    prefix='4226114502464000017055001000000017112345678'
    r=sum(int(c)*(2+i%8) for i,c in enumerate(reversed(prefix)))%11
    return prefix+str(0 if r<2 else 11-r)

@pytest.mark.asyncio
async def test_direct_sales_use_issuer_key_and_only_emit_limited_output():
    k=key()
    assert matching_key({"chaveAcesso":k})==k
    def handler(req):
        if req.url.path.endswith('/venda'):
            return httpx.Response(200,json=[{'idVenda':1,'codigoCliente':7,'chaveAcesso':k,'emissao':'2026-11-23','nomeCliente':'private_entity','codigoFilial':'1'}])
        if req.url.path.endswith('/venda-item'):
            return httpx.Response(200,json=[{'idVenda':1,'iditem':1,'codigoCliente':7,'codigoProduto':'TINTA-C','codigoUnidadeMedida':'UN','quantidade':1,'codigoDavOrigem':'20','tipoDavOrigem':4,'chaveAcesso':k}])
        return httpx.Response(200,json=[{'codigo':20,'filial':'1','cliente':'7','tipoDocumento':4,'cnpjCpfCliente':'11.222.333/0001-81','data':'2026-11-23','observacao':'DOSS-RES-AABB11223344CCDD private_message'}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page=await sales_page(client,{},'2026-11-23',0)
    assert (page['sales'],page['issuerInvoices'],page['items'],page['orders'],page['orderCodes'])==(1,1,1,1,1)
    assert page['linkedInvoices']==1 and page['orderEvents'][1]['key']==k
    assert page['documents']==[] and 'private_' not in str(page)

@pytest.mark.asyncio
async def test_rejects_ignored_date():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=[{'emissao':'2019-01-01'}]))) as client:
        with pytest.raises(ValueError,match='filter'): await sales_page(client,{},'2026-11-23',0)
