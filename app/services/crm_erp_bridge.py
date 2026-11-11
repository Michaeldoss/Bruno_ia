"""Durable, scoped ERP bridge. Credentials stay in the existing server process.

Read-only discovery ships first; order writes remain explicitly disabled until
live tenant contracts have been verified. No AI calls or WhatsApp sends here.
"""
import asyncio
import logging
import re
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

ERP_ORIGIN = 'https://vzan-getcard01.getcard.uniplusweb.com'
logger = logging.getLogger(__name__)
_task = None
_state = {'state': 'stopped', 'release': 'erp-bridge-4'}
ENTITY_FIELDS = ('codigo','nome','razaoSocial','tipo','inativo','creditoRestrito','limiteCredito','cnpjCpf','cep','cepEntrega','endereco','numeroEndereco','bairro','cidade','estado')
ORDER_FIELDS = ('codigo','cliente','tipoDocumento','status','data','vendedor','filial','condicaoPagamento','tipoDocumentoFinanceiro','codigoTransportadora','codigoTipoFrete','valor','valorFrete','desconto','percentualDescontoSubtotal','valorDescontoItens','codigoECommerce','cepEntrega','itens')
ITEM_FIELDS = ('produto','nomeProduto','descricao','quantidade','precoUnitario','precoTotal','desconto','isBrinde','codigoUnidadeMedida')
PRODUCT_FIELDS = ('codigo','nome','inativo','unidadeMedida','preco','pesoBruto','pesoLiquido','codigoGrupoProduto')
SALE_FIELDS = ('idVenda','documento','pdv','codigoCliente','nomeCliente','emissao','dataHoraEmissao','codigoFilial','status','nomeVendedor','valorProdutos','desconto','valorTotal','chaveAcesso')
SALE_ITEM_FIELDS = ('idVenda','iditem','documento','modelo','pdv','codigoCliente','nomeCliente','codigoVendedor','nomeVendedor','emissao','dataHoraEmissao','codigoProduto','descricaoProduto','codigoUnidadeMedida','quantidade','precoUnitario','desconto','total','valorProdutos','valorTotal','condicaoPagamento','frete','seguro','outrasDespesas','status','tipoItem','codigoFilial','cfop')
READ_RESOURCE_VERSIONS = {
    'entidades':'v1',
    'davs':'v1',
    'produtos':'v1',
    'saldo-estoque':'v1',
    'venda':'v2',
    'venda-item':'v2',
}


class BridgeFailure(RuntimeError):
    pass


def code(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,20}', value):
        raise BridgeFailure('invalid_code')
    return value


def project(record, fields):
    if not isinstance(record, dict):
        raise BridgeFailure('erp_object_invalid')
    return {key: record[key] for key in fields if key in record}


def order_summary(record):
    out = project(record, ORDER_FIELDS)
    if 'itens' in out:
        if not isinstance(out['itens'], list):
            raise BridgeFailure('erp_items_invalid')
        out['itens'] = [project(item, ITEM_FIELDS) for item in out['itens']]
    return out


def scalar_stock(value):
    # Missing/invalid stock must never become zero or a permission to sell.
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise BridgeFailure('stock_response_not_scalar')
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise BridgeFailure('stock_response_invalid') from None
    if not number.is_finite():
        raise BridgeFailure('stock_response_invalid')
    return float(number)


def duplicate_candidates(records, customer, requested_items, source_date):
    """Conservative duplicate gate: freight/price differences never hide a sale.

    A positive match requires review, not automatic adoption or an ERP rewrite.
    Call only with all recent customer orders, never a truncated history.
    """
    code(customer)
    try:
        if date.fromisoformat(source_date).isoformat() != source_date:
            raise ValueError()
    except (ValueError, TypeError):
        raise BridgeFailure('invalid_order_source_date') from None
    if not isinstance(requested_items, list) or not 1 <= len(requested_items) <= 12:
        raise BridgeFailure('invalid_requested_items')
    needed = {}
    for item in requested_items:
        product = code(item.get('product_code'))
        quantity = item.get('quantity')
        if type(quantity) is not int or not 1 <= quantity <= 1000 or product in needed:
            raise BridgeFailure('invalid_requested_quantity')
        needed[product] = quantity
    matches = []
    for record in records:
        if str(record.get('cliente')) != customer:
            raise BridgeFailure('erp_customer_mismatch')
        if str(record.get('data', '')) < source_date:
            continue
        if str(record.get('tipoDocumento')) not in ('4', '6') or str(record.get('status')) not in ('1','2','4','7','8','9'):
            continue
        if not isinstance(record.get('itens'), list):
            raise BridgeFailure('duplicate_check_items_missing')
        present = {}
        for item in record['itens']:
            product = str(item.get('produto', ''))
            quantity = scalar_stock(item.get('quantidade'))
            present[product] = present.get(product, 0) + quantity
        if all(present.get(product, 0) >= qty for product, qty in needed.items()):
            matches.append(project(record, ('codigo','data','tipoDocumento','status','vendedor','valor','valorFrete','condicaoPagamento')))
    return matches


BRIDGE_CONTRACT = 'progressive_v1'


class BridgeWorker:
    def __init__(self, client, settings, access_token):
        self.client = client
        self.settings = settings
        self.access_token = access_token
        self.endpoint = settings.SUPABASE_URL.rstrip('/') + '/rest/v1/rpc/crm_erp_bridge_work'
        self.headers = {'apikey':settings.SUPABASE_SERVICE_ROLE_KEY,'Authorization':'Bearer '+settings.SUPABASE_SERVICE_ROLE_KEY}

    async def rpc(self, operation, job=None, data=None):
        job = job or {}
        response = await self.client.post(self.endpoint, headers=self.headers,
            json={'p_operation':operation,'p_job':job.get('id'),'p_lease':job.get('lease'),'p_data':data or {}},
            timeout=20, follow_redirects=False)
        response.raise_for_status()
        return response.json() if response.content else None

    async def read(self, resource, key=None, params=None):
        # Not a generic proxy: every readable ERP route is explicitly allowlisted.
        version = READ_RESOURCE_VERSIONS.get(resource)
        if version is None:
            raise BridgeFailure('resource_not_allowed')
        if resource in ('venda','venda-item') and key is not None:
            raise BridgeFailure('resource_key_not_allowed')
        path = '/public-api/' + version + '/' + resource + ('/'+code(key) if key is not None else '')
        token = await self.access_token()
        response = await self.client.get(ERP_ORIGIN+path, params=params,
            headers={'Authorization':'Bearer '+token,'Accept':'application/json'},
            timeout=25, follow_redirects=False)
        if not response.is_success:
            raise BridgeFailure('erp_read_http_'+str(response.status_code))
        try:
            return response.json()
        except ValueError:
            raise BridgeFailure('erp_read_invalid_json') from None

    async def sales_schema_probe(self, customer):
        # Schema-only diagnostic for the documented Uniplus v2 sales endpoints.
        # Values are intentionally not returned: this is used only to map the
        # current installation safely before exposing a purchase-history contract.
        params = {'codigoCliente.eq':customer,'limit':1,'offset':0}
        sales = await self.read('venda', params=params)
        items = await self.read('venda-item', params=params)
        if not isinstance(sales,list) or not isinstance(items,list):
            raise BridgeFailure('erp_sales_list_invalid')
        sale_fields = sorted(sales[0].keys()) if sales and isinstance(sales[0],dict) else []
        item_fields = sorted(items[0].keys()) if items and isinstance(items[0],dict) else []
        return {
            'sales_sample_count':len(sales),
            'sale_fields':sale_fields,
            'item_sample_count':len(items),
            'item_fields':item_fields,
        }

    async def customer_sales_rows(self, resource, customer, job):
        rows_by_key = {}
        for offset in range(0,5000,100):
            if not (await self.rpc('heartbeat',job) or {}).get('ok'):
                raise BridgeFailure('lease_lost')
            rows = await self.read(resource,params={
                'codigoCliente.eq':customer,
                'limit':100,
                'offset':offset,
            })
            if not isinstance(rows,list):
                raise BridgeFailure('erp_sales_list_invalid')
            for row in rows:
                if not isinstance(row,dict) or str(row.get('codigoCliente')) != customer:
                    raise BridgeFailure('erp_customer_filter_not_honored')
                if resource == 'venda':
                    key = str(row.get('idVenda',''))
                    if not key:
                        raise BridgeFailure('erp_sale_id_missing')
                else:
                    key = ':'.join([
                        str(row.get('idVenda','')),
                        str(row.get('iditem','')),
                        str(row.get('documento','')),
                        str(row.get('codigoProduto','')),
                    ])
                    if not str(row.get('idVenda','')) or not str(row.get('iditem','')):
                        raise BridgeFailure('erp_sale_item_id_missing')
                if key in rows_by_key:
                    raise BridgeFailure('erp_pagination_repeated')
                rows_by_key[key] = row
            if len(rows) < 100:
                return list(rows_by_key.values())
        raise BridgeFailure('erp_pagination_limit')

    async def purchase_history_headers(self, customer, job):
        payload = job.get('payload') or {}
        limit = payload.get('purchase_history_limit',40)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise BridgeFailure('invalid_purchase_history_limit')

        sales = await self.customer_sales_rows('venda',customer,job)
        sales.sort(
            key=lambda row:(str(row.get('dataHoraEmissao') or row.get('emissao') or ''),str(row.get('idVenda') or '')),
            reverse=True,
        )
        selected = [project(row,SALE_FIELDS) for row in sales[:limit]]
        return {
            'sales_total':len(sales),
            'sales_returned':len(selected),
            'sales_truncated':len(selected) < len(sales),
            'sales':selected,
            'source':'uniplus_v2_sales_headers',
        }

    async def sale_items(self, customer, sale_id, job):
        sale_id = str(sale_id or '')
        if not sale_id.isdigit():
            raise BridgeFailure('invalid_purchase_sale_id')

        if not (await self.rpc('heartbeat',job) or {}).get('ok'):
            raise BridgeFailure('lease_lost')

        rows_by_key = {}
        for offset in range(0,500,100):
            rows = await self.read('venda-item',params={
                'codigoCliente.eq':customer,
                'idVenda.eq':sale_id,
                'limit':100,
                'offset':offset,
            })
            if not isinstance(rows,list):
                raise BridgeFailure('erp_sales_list_invalid')
            for row in rows:
                if not isinstance(row,dict):
                    raise BridgeFailure('erp_object_invalid')
                if str(row.get('codigoCliente')) != customer or str(row.get('idVenda')) != sale_id:
                    raise BridgeFailure('erp_sale_item_filter_not_honored')
                key = ':'.join([
                    str(row.get('idVenda','')),
                    str(row.get('iditem','')),
                    str(row.get('documento','')),
                    str(row.get('codigoProduto','')),
                ])
                if not str(row.get('iditem','')):
                    raise BridgeFailure('erp_sale_item_id_missing')
                if key in rows_by_key:
                    raise BridgeFailure('erp_pagination_repeated')
                rows_by_key[key] = row
            if len(rows) < 100:
                items=[project(row,SALE_ITEM_FIELDS) for row in rows_by_key.values()]
                items.sort(key=lambda row:(str(row.get('documento') or ''),int(row.get('iditem') or 0)))
                return {
                    'sale_id':sale_id,
                    'item_count':len(items),
                    'items':items,
                    'source':'uniplus_v2_sale_items',
                }
        raise BridgeFailure('erp_sale_item_pagination_limit')

    async def purchase_history(self, customer, job):
        payload = job.get('payload') or {}
        limit = payload.get('purchase_history_limit',100)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise BridgeFailure('invalid_purchase_history_limit')

        headers = await self.purchase_history_headers(customer,{
            **job,
            'payload':{**payload,'purchase_history_limit':limit},
        })
        selected_sales = headers['sales']

        semaphore = asyncio.Semaphore(8)

        async def load_items(sale):
            async with semaphore:
                return await self.sale_items(customer,sale.get('idVenda'),job)

        items_by_sale = {}
        flat_items = []
        for batch_start in range(0,len(selected_sales),20):
            if not (await self.rpc('heartbeat',job) or {}).get('ok'):
                raise BridgeFailure('lease_lost')
            batch = selected_sales[batch_start:batch_start+20]
            batch_items = await asyncio.gather(*(load_items(sale) for sale in batch))
            for sale,item_result in zip(batch,batch_items):
                sale_id = str(sale.get('idVenda'))
                projected_items = item_result['items']
                items_by_sale[sale_id] = projected_items
                flat_items.extend(projected_items)

        sale_rows = []
        for sale in selected_sales:
            projected = dict(sale)
            sale_id = str(sale.get('idVenda'))
            projected['items'] = items_by_sale.get(sale_id,[])
            sale_rows.append(projected)

        flat_items.sort(
            key=lambda row:(str(row.get('dataHoraEmissao') or row.get('emissao') or ''),str(row.get('documento') or ''),int(row.get('iditem') or 0)),
            reverse=True,
        )

        return {
            'sales_total':headers['sales_total'],
            'sales_returned':len(sale_rows),
            'sales_truncated':headers['sales_truncated'],
            'items_total_for_returned_sales':len(flat_items),
            'sales':sale_rows,
            'items':flat_items,
            'source':'uniplus_v2_venda_by_sale',
        }

    async def customer_orders(self, customer, job):
        rows_by_code = {}
        for offset in range(0,2000,100):
            if not (await self.rpc('heartbeat',job) or {}).get('ok'):
                raise BridgeFailure('lease_lost')
            rows = await self.read('davs',params={'cliente.eq':customer,'limit':100,'offset':offset})
            if not isinstance(rows,list):
                raise BridgeFailure('erp_list_invalid')
            for row in rows:
                if not isinstance(row,dict) or str(row.get('cliente')) != customer:
                    raise BridgeFailure('erp_customer_filter_not_honored')
                number = code(str(row.get('codigo','')))
                if number in rows_by_code:
                    raise BridgeFailure('erp_pagination_repeated')
                rows_by_code[number] = row
            if len(rows)<100:
                return sorted(rows_by_code.values(),key=lambda r:(str(r.get('data','')),int(r['codigo'])),reverse=True)
        raise BridgeFailure('erp_pagination_limit')

    async def inspect(self, job):
        if 'api_resource_read' in (job.get('payload') or {}):
            from app.services.uniplus_management_read import read_resource, ManagementReadFailure
            try:
                return await read_resource(self, job)
            except ManagementReadFailure as exc:
                raise BridgeFailure(str(exc)) from None
        if 'api_access_probe' in (job.get('payload') or {}):
            from app.services.uniplus_access_audit import audit_access
            return await audit_access(self, job)
        customer = code(job['customer_code'])
        entity = await self.read('entidades',customer)
        if str(entity.get('codigo')) != customer:
            raise BridgeFailure('erp_customer_mismatch')
        payload = job.get('payload') or {}
        if payload.get('sales_schema_probe') is True:
            schema = await self.sales_schema_probe(customer)
            return {
                'customer':project(entity,ENTITY_FIELDS),
                'sales_schema':schema,
                'history_complete':False,
                'schema_probe':True,
            }
        if payload.get('purchase_history_headers') is True:
            headers = await self.purchase_history_headers(customer,job)
            return {
                'customer':project(entity,ENTITY_FIELDS),
                'purchase_history_headers':headers,
                'history_complete':not headers['sales_truncated'],
            }
        if payload.get('purchase_sale_items') is True:
            items = await self.sale_items(customer,payload.get('purchase_sale_id'),job)
            return {
                'customer':project(entity,ENTITY_FIELDS),
                'purchase_sale_items':items,
                'history_complete':True,
            }
        if payload.get('purchase_history') is True:
            history = await self.purchase_history(customer,job)
            return {
                'customer':project(entity,ENTITY_FIELDS),
                'purchase_history':history,
                'history_complete':not history['sales_truncated'],
            }
        orders = await self.customer_orders(customer,job)
        duplicate_check = payload.get('duplicate_check')
        history_limit = payload.get('history_limit')
        if history_limit is None:
            selected = orders[:8]
        else:
            if type(history_limit) is not int or not 1 <= history_limit <= 40:
                raise BridgeFailure('invalid_history_limit')
            selected = orders[:history_limit]
        if duplicate_check is not None:
            duplicate_candidates([], customer, duplicate_check.get('items'), duplicate_check.get('source_date'))
            selected = [row for row in orders if str(row.get('data','')) >= duplicate_check['source_date']]
            if len(selected) > 30:
                raise BridgeFailure('duplicate_scan_too_large')
        details = []
        for index,row in enumerate(selected):
            if index and index % 10 == 0:
                if not (await self.rpc('heartbeat',job) or {}).get('ok'):
                    raise BridgeFailure('lease_lost')
            detail = await self.read('davs',str(row['codigo']))
            if str(detail.get('cliente')) != customer:
                raise BridgeFailure('erp_customer_mismatch')
            details.append(order_summary(detail))
        result = {'customer':project(entity,ENTITY_FIELDS),'history_complete':True,
            'order_count':len(orders),'orders_returned':len(details),
            'orders_truncated':len(details) < len(orders),'orders':details,
            'configured_branch':self.settings.UNIPLUS_FILIAL,
            'configured_stock_location':self.settings.UNIPLUS_LOCAL_ESTOQUE}
        if duplicate_check is not None:
            matches = duplicate_candidates(details, customer, duplicate_check['items'], duplicate_check['source_date'])
            result['duplicate_check'] = {'complete':True,'candidates':matches,
                'state':'existing_order_requires_review' if matches else 'no_candidate_found',
                'issuance_authorized':False}
        return result

    async def catalog(self, job):
        payload = job.get('payload') or {}
        products = payload.get('product_codes',[])
        if not isinstance(products,list) or not 1<=len(products)<=12 or len(set(products))!=len(products):
            raise BridgeFailure('invalid_product_list')
        branch = code(str(self.settings.UNIPLUS_FILIAL))
        location = code(str(self.settings.UNIPLUS_LOCAL_ESTOQUE))
        results = []
        for product in products:
            code(product)
            record = await self.read('produtos',product,params={'filial':branch})
            if str(record.get('codigo')) != product:
                raise BridgeFailure('erp_product_mismatch')
            stock = await self.read('saldo-estoque',params={'produto':product,'filial':branch,'localestoque':location})
            results.append({'product':project(record,PRODUCT_FIELDS),'available':scalar_stock(stock)})
        entities = {}
        for field in ('seller_code','carrier_code'):
            if payload.get(field):
                key = code(payload[field])
                entity = await self.read('entidades',key)
                if str(entity.get('codigo')) != key:
                    raise BridgeFailure('erp_entity_mismatch')
                entities[field] = project(entity,ENTITY_FIELDS)
        return {'products':results,'entities':entities,'branch':branch,'stock_location':location}

    async def tick(self):
        job = await self.rpc('claim', data={'bridge_contract':BRIDGE_CONTRACT})
        if not job:
            _state['state']='idle'
            return
        start=time.monotonic()
        _state['state']='reading'
        try:
            if job['kind']=='inspect':
                result = await self.inspect(job)
            elif job['kind']=='catalog':
                result = await self.catalog(job)
            else:
                raise BridgeFailure('write_adapter_not_enabled')
            result.update(checked_at=datetime.now(timezone.utc).isoformat(),duration_ms=round((time.monotonic()-start)*1000))
            outcome={'state':'succeeded','result':result}
        except asyncio.CancelledError:
            raise
        except BridgeFailure as error:
            outcome={'state':'blocked','error_code':str(error)}
        except Exception:
            outcome={'state':'blocked','error_code':'bridge_read_unavailable'}
        saved = False
        for attempt in range(3):
            try:
                saved=(await self.rpc('complete',job,outcome) or {}).get('ok') is True
                if saved: break
            except Exception:
                if attempt<2: await asyncio.sleep(1+attempt)
        _state['state']=outcome['state'] if saved else 'result_persistence_failed'


async def _loop():
    from app.config import get_settings
    from app.services.uniplus_orders import UniplusOrders
    settings=get_settings()
    required=(settings.SUPABASE_SERVICE_ROLE_KEY,settings.UNIPLUS_CLIENT_ID,settings.UNIPLUS_CLIENT_SECRET)
    if any(not value or value=='stub' for value in required):
        _state['state']='configuration_required'
        return
    async with httpx.AsyncClient() as client:
        erp=UniplusOrders(client,settings)
        worker=BridgeWorker(client,settings,erp.access_token)
        while True:
            try:
                await worker.tick()
                pause=4
            except asyncio.CancelledError:
                raise
            except Exception:
                _state['state']='unavailable'
                logger.warning('ERP bridge unavailable; no unconfirmed write retried')
                pause=60
            await asyncio.sleep(pause)


def start_erp_bridge():
    global _task
    if _task is None or _task.done():
        _state['state']='starting'
        _task=asyncio.create_task(_loop())


async def stop_erp_bridge():
    global _task
    if _task:
        _task.cancel()
        try: await _task
        except asyncio.CancelledError: pass
        _task=None
    _state['state']='stopped'


def erp_bridge_status():
    return {**_state,'task_alive':bool(_task and not _task.done())}
