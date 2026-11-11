"""Bounded, GET-only diagnostics for the existing globally authorized ERP owner.

No arbitrary URLs, ERP writes, response bodies, credentials or business values
are returned. Route candidates remain unverified until the live response passes.
"""
import re
import httpx

ORIGIN = 'https://vzan-getcard01.getcard.uniplusweb.com'
ROUTES = {
    'service_orders': '/public-api/v1/ordem-servico',
    'financial_document_types': '/public-api/v1/tipo-documento-financeiro',
    'financial_documents_v1': '/public-api/v1/documento-financeiro',
    'financial_documents_v2': '/public-api/v2/documento-financeiro',
    'chart_of_accounts': '/public-api/v1/plano-contas',
    'cost_centers': '/public-api/v1/centro-custo',
    'purchase_orders': '/public-api/v1/pedido-compra',
    'payment_terms': '/public-api/v1/condicoes-pagamento',
    'openapi_v3': '/v3/api-docs',
    'openapi_v2': '/v2/api-docs',
    'openapi_public': '/public-api/swagger.json',
}


def schema_only(body):
    if isinstance(body, list):
        return {'shape': 'list', 'sample_count': len(body),
                'fields': sorted(k for row in body[:1] if isinstance(row, dict)
                                 for k in row if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', k))}
    if isinstance(body, dict):
        paths = body.get('paths')
        if isinstance(paths, dict):
            routes = []
            for path, methods in paths.items():
                if re.fullmatch(r'/public-api/v[12]/[A-Za-z0-9_/{}/-]+', path) and isinstance(methods, dict) and 'get' in methods:
                    routes.append(path)
            return {'shape': 'openapi', 'get_routes': sorted(routes)[:300], 'routes_truncated': len(routes)>300}
        return {'shape': 'object', 'fields': sorted(k for k in body if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', k))}
    return {'shape': 'scalar'}


async def audit_access(worker, job):
    requested = (job.get('payload') or {}).get('api_access_probe')
    if not isinstance(requested, list) or not 1 <= len(requested) <= 6 or any(not isinstance(k, str) or k not in ROUTES for k in requested) or len(set(requested)) != len(requested):
        raise ValueError('invalid_api_probe')
    # Customer-scoped authorizations must never become installation-wide reads.
    response = await worker.client.get(
        worker.settings.SUPABASE_URL.rstrip('/')+'/rest/v1/crm_erp_bridge_config',
        headers=worker.headers,
        params={'org_id': 'eq.'+job['org_id'], 'owner_id': 'eq.'+job['owner_id'],
                'customer_code': 'eq.0', 'enabled': 'eq.true', 'select': 'org_id'},
        timeout=15, follow_redirects=False)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or len(rows)!=1 or rows[0].get('org_id') != job['org_id']:
        raise ValueError('global_read_authorization_required')
    results = []
    for name in requested:
        if not (await worker.rpc('heartbeat', job) or {}).get('ok'):
            raise ValueError('lease_lost')
        result = {'resource': name, 'path': ROUTES[name]}
        try:
            token = await worker.access_token()
            response = await worker.client.get(ORIGIN+ROUTES[name],
                params=None if name.startswith('openapi_') else {'limit':1, 'offset':0},
                headers={'Authorization':'Bearer '+token,'Accept':'application/json'},
                timeout=15, follow_redirects=False)
            result['http_status'] = response.status_code
            if not response.is_success:
                result['state'] = {401:'unauthorized',403:'forbidden',404:'not_found',429:'rate_limited'}.get(response.status_code,'http_error')
            elif len(response.content)>4_000_000:
                result['state'] = 'response_too_large'
            else:
                try:
                    result.update(schema_only(response.json()))
                    result['state'] = 'json_response'
                except (ValueError, TypeError):
                    result['state'] = 'invalid_json'
        except httpx.TimeoutException:
            result['state'] = 'timeout'
        except Exception:
            result['state'] = 'request_failed'
        results.append(result)
    return {'api_access_audit':results,'schema_probe':True,'history_complete':False}
