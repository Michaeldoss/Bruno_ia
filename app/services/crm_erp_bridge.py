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
_state = {'state': 'stopped', 'release': 'erp-bridge-2'}
ENTITY_FIELDS = ('codigo','nome','razaoSocial','tipo','inativo','creditoRestrito','limiteCredito','cnpjCpf','cep','cepEntrega','endereco','numeroEndereco','bairro','cidade','estado')
ORDER_FIELDS = ('codigo','cliente','tipoDocumento','status','data','vendedor','filial','condicaoPagamento','tipoDocumentoFinanceiro','codigoTransportadora','codigoTipoFrete','valor','valorFrete','desconto','percentualDescontoSubtotal','valorDescontoItens','codigoECommerce','cepEntrega','itens')
ITEM_FIELDS = ('produto','nomeProduto','descricao','quantidade','precoUnitario','precoTotal','desconto','isBrinde','codigoUnidadeMedida')
PRODUCT_FIELDS = ('codigo','nome','inativo','unidadeMedida','preco','pesoBruto','pesoLiquido','codigoGrupoProduto')


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
        # Not a generic proxy: no caller-supplied URL, path traversal or verbs.
        if resource not in ('entidades','davs','produtos','saldo-estoque'):
            raise BridgeFailure('resource_not_allowed')
        path = '/public-api/v1/' + resource + ('/'+code(key) if key is not None else '')
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
        customer = code(job['customer_code'])
        entity = await self.read('entidades',customer)
        if str(entity.get('codigo')) != customer:
            raise BridgeFailure('erp_customer_mismatch')
        orders = await self.customer_orders(customer,job)
        duplicate_check = (job.get('payload') or {}).get('duplicate_check')
        selected = orders[:8]
        if duplicate_check is not None:
            duplicate_candidates([], customer, duplicate_check.get('items'), duplicate_check.get('source_date'))
            selected = [row for row in orders if str(row.get('data','')) >= duplicate_check['source_date']]
            if len(selected) > 30:
                raise BridgeFailure('duplicate_scan_too_large')
        details = []
        for row in selected:
            detail = await self.read('davs',str(row['codigo']))
            if str(detail.get('cliente')) != customer:
                raise BridgeFailure('erp_customer_mismatch')
            details.append(order_summary(detail))
        result = {'customer':project(entity,ENTITY_FIELDS),'history_complete':True,
            'order_count':len(orders),'orders':details,'configured_branch':self.settings.UNIPLUS_FILIAL,
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
        job = await self.rpc('claim')
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
                pause=5
            except asyncio.CancelledError:
                raise
            except Exception:
                _state['state']='unavailable'
                logger.warning('ERP bridge unavailable; no unconfirmed write retried')
                pause=30
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
