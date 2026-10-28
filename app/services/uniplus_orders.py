"""Poll ERP DAVs every ten seconds; only create missing operational cards in waiting."""
import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)
_task = None
_state = {"state": "stopped"}
ERP_ORIGIN = "https://vzan-getcard01.getcard.uniplusweb.com"


class SyncFailure(RuntimeError):
    pass


class UniplusOrders:
    def __init__(self, client, settings):
        self.client = client
        self.settings = settings
        self.endpoint = settings.SUPABASE_URL.rstrip('/') + '/rest/v1/rpc/crm_uniplus_sync_work'
        self.headers = {'apikey': settings.SUPABASE_SERVICE_ROLE_KEY,
                        'Authorization': 'Bearer ' + settings.SUPABASE_SERVICE_ROLE_KEY}
        self.token = None
        self.expires_at = 0

    async def rpc(self, operation, lease=None, data=None):
        response = await self.client.post(self.endpoint, headers=self.headers,
            json={'p_operation': operation, 'p_lease': lease, 'p_data': data or {}},
            timeout=20, follow_redirects=False)
        response.raise_for_status()
        return response.json()

    async def access_token(self):
        if self.token and time.monotonic() < self.expires_at:
            return self.token
        response = await self.client.post(ERP_ORIGIN + '/oauth/token',
            auth=httpx.BasicAuth(self.settings.UNIPLUS_CLIENT_ID, self.settings.UNIPLUS_CLIENT_SECRET),
            data={'grant_type': 'client_credentials', 'scope': 'public-api'},
            headers={'Accept': 'application/json'}, timeout=30, follow_redirects=False)
        response.raise_for_status()
        body = response.json()
        token = body.get('access_token')
        if not isinstance(token, str) or not token:
            raise SyncFailure('erp_token_missing')
        self.token = token
        self.expires_at = time.monotonic() + max(0, min(float(body.get('expires_in', 300)), 86400) - 60)
        return token

    async def tick(self):
        job = await self.rpc('claim')
        if not job:
            return
        lease = job['lease']
        counts = {key: 0 for key in ('checked', 'created', 'existing', 'blocked', 'ignored')}
        blocked_orders = []
        _state['state'] = 'syncing'
        try:
            token = await self.access_token()
            seen = set()
            for day in job['dates']:
                for offset in range(0, 20000, 100):
                    if not (await self.rpc('heartbeat', lease) or {}).get('ok'):
                        raise SyncFailure('lease_expired')
                    response = await self.client.get(ERP_ORIGIN + '/public-api/v1/davs',
                        params={'data.eq': day, 'limit': 100, 'offset': offset},
                        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'},
                        timeout=30, follow_redirects=False)
                    if response.status_code == 401:
                        self.token = None
                    response.raise_for_status()
                    rows = response.json()
                    if not isinstance(rows, list):
                        raise SyncFailure('erp_invalid_response')
                    for row in rows:
                        # Validate date locally even if the ERP ignores filters.
                        if not isinstance(row, dict) or row.get('data') != day:
                            continue
                        if str(row.get('tipoDocumento')) not in ('4', '6') or str(row.get('status')) not in ('1', '2', '4', '7', '8', '9'):
                            continue
                        number = str(row.get('codigo', ''))
                        if not number.isdigit() or number in seen:
                            continue
                        seen.add(number)
                        # Do not transmit products, values, notes or arbitrary ERP fields.
                        dav = {key: row.get(key) for key in ('codigo', 'cliente', 'tipoDocumento', 'status', 'cnpjCpfCliente', 'vendedor', 'data', 'filial')}
                        result = await self.rpc('import', lease, {'dav': dav})
                        if not result:
                            raise SyncFailure('lease_expired')
                        status = result.get('status')
                        if status not in counts or status == 'checked':
                            raise SyncFailure('import_invalid_response')
                        counts['checked'] += 1
                        counts[status] += 1
                        if status == 'blocked':
                            detail = {
                                'order_number': number,
                                'customer_code': str(row.get('cliente') or ''),
                                'seller_code': str(row.get('vendedor') or ''),
                                'reason': str(result.get('reason') or 'unknown'),
                            }
                            blocked_orders.append(detail)
                            logger.warning(
                                'Uniplus order blocked order=%s customer=%s seller=%s reason=%s',
                                detail['order_number'], detail['customer_code'],
                                detail['seller_code'], detail['reason'],
                            )
                    if len(rows) < 100:
                        break
                else:
                    raise SyncFailure('erp_pagination_limit')
            if blocked_orders:
                counts['blocked_orders'] = blocked_orders[:20]
            if not (await self.rpc('finish', lease, {'counts': counts}) or {}).get('ok'):
                raise SyncFailure('lease_expired')
            _state['state'] = 'ready'
        except asyncio.CancelledError:
            # Lease expires after a process stops. Already-created cards remain
            # idempotent, so an interrupted batch can safely be queried again.
            raise
        except Exception as error:
            reason = ('erp_http_' if error.request.url.host.endswith('uniplusweb.com') else 'crm_http_') + str(error.response.status_code) if isinstance(error, httpx.HTTPStatusError) else str(error) if isinstance(error, SyncFailure) else 'sync_unavailable'
            _state['state'] = 'error'
            logger.warning('Uniplus order sync failed (%s)', reason)
            await self.rpc('finish', lease, {'counts': counts, 'error': reason})


async def _loop():
    from app.config import get_settings
    settings = get_settings()
    required = (settings.SUPABASE_SERVICE_ROLE_KEY, settings.UNIPLUS_CLIENT_ID, settings.UNIPLUS_CLIENT_SECRET)
    if any(not value or value == 'stub' for value in required):
        _state['state'] = 'configuration_required'
        return
    async with httpx.AsyncClient() as client:
        worker = UniplusOrders(client, settings)
        while True:
            try:
                await worker.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                _state['state'] = 'unavailable'
                logger.warning('Uniplus order queue unavailable')
            # Database enforces a 10-second interval across processes/restarts.
            await asyncio.sleep(1)


def start_uniplus_orders():
    from app.services.crm_erp_bridge import start_erp_bridge
    start_erp_bridge()
    global _task
    if _task is None or _task.done():
        _state['state'] = 'starting'
        _task = asyncio.create_task(_loop())


async def stop_uniplus_orders():
    from app.services.crm_erp_bridge import stop_erp_bridge
    await stop_erp_bridge()
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    _state['state'] = 'stopped'


def uniplus_orders_status():
    from app.services.crm_erp_bridge import erp_bridge_status
    return {**_state, 'task_alive': bool(_task and not _task.done()), 'interval_seconds': 10,
            'erp_bridge': erp_bridge_status()}
