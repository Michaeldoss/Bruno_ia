"""Explicitly projected management reads, restricted to global ERP authorization."""
from datetime import date
import re
from app.services.uniplus_access_audit import ORIGIN, ROUTES, require_global_read

FIELDS = {
    'financial_documents_v1': ('id','documento','tipo','tipoOrigem','idOrigem','status','valor','saldo','juros','multa','emissao','vencimento','pagamento','baixa','entrada','registro','parcela','totalParcelas','codigoEntidade','nomeEntidade','codigoFilial','nomeFilial','codigoPlanoConta','nomePlanoConta','idTipoDocumentoFinanceiro','descricaoTipoDocumentoFinanceiro'),
    'service_orders': ('id','codigo','codigoCliente','nomeCliente','codigoFilial','codigoAtendente','nomeAtendente','status','data','dataOrdemServico','agendamento','inicioServico','dataUltmoEvento','descricaoTipoUltimoEvento','descricaoItem','valor','valorProdutos','valorServicos','descontoSubTotal','codigoCondicaoPagamento','descricaoCondicaoPagamento'),
    'financial_document_types': ('codigo','descricao','acao','inativo','tipoUso','localUso','formaPagamentoNfe'),
}
DATE_FIELDS = {'emissao','vencimento','pagamento','baixa','entrada','registro','data','dataOrdemServico','agendamento','inicioServico','dataUltmoEvento'}
CODE_FIELDS = {'id','codigo','tipo','status','codigoEntidade','codigoCliente','codigoFilial','codigoPlanoConta','idTipoDocumentoFinanceiro','codigoAtendente'}

class ManagementReadFailure(ValueError):
    pass


def validate_query(payload):
    name=payload.get('api_resource_read')
    if not isinstance(name,str) or name not in FIELDS:
        raise ManagementReadFailure('management_resource_not_allowed')
    limit=payload.get('limit',25);offset=payload.get('offset',0);filters=payload.get('filters',{})
    if type(limit) is not int or not 1<=limit<=25 or type(offset) is not int or not 0<=offset<=100000:
        raise ManagementReadFailure('invalid_management_page')
    if not isinstance(filters,dict) or len(filters)>8:
        raise ManagementReadFailure('invalid_management_filters')
    for key,value in filters.items():
        if not isinstance(key,str) or key.count('.')!=1 or not isinstance(value,str):
            raise ManagementReadFailure('invalid_management_filter')
        field,op=key.split('.')
        if field not in FIELDS[name]:raise ManagementReadFailure('invalid_management_filter')
        if field in DATE_FIELDS and op in ('eq','gte','lte'):
            try:
                if date.fromisoformat(value).isoformat()!=value:raise ValueError()
            except ValueError:raise ManagementReadFailure('invalid_management_date') from None
        elif field in CODE_FIELDS and op=='eq' and re.fullmatch(r'[0-9]{1,20}',value):pass
        else:raise ManagementReadFailure('invalid_management_filter')
    return name,limit,offset,filters


def matches(row,filters):
    for key,wanted in filters.items():
        field,op=key.split('.')
        actual=row.get(field)
        if actual is None or isinstance(actual,(dict,list,bool)):return False
        actual=str(actual)
        if field in DATE_FIELDS:
            actual=actual[:10]
            try:date.fromisoformat(actual)
            except ValueError:return False
        elif field in CODE_FIELDS:
            if not actual.isdigit():return False
            actual=str(int(actual));wanted=str(int(wanted))
        if (op=='eq' and actual!=wanted) or (op=='gte' and actual<wanted) or (op=='lte' and actual>wanted):return False
    return True


async def read_resource(worker,job):
    name,limit,offset,filters=validate_query(job.get('payload') or {})
    await require_global_read(worker,job)
    token=await worker.access_token()
    response=await worker.client.get(ORIGIN+ROUTES[name],params={**filters,'limit':limit,'offset':offset},
        headers={'Authorization':'Bearer '+token,'Accept':'application/json'},timeout=25,follow_redirects=False)
    if not response.is_success:raise ManagementReadFailure('management_http_'+str(response.status_code))
    if len(response.content)>2_000_000:raise ManagementReadFailure('management_response_too_large')
    try:rows=response.json()
    except ValueError:raise ManagementReadFailure('management_invalid_json') from None
    if not isinstance(rows,list) or len(rows)>limit or any(not isinstance(row,dict) for row in rows):
        raise ManagementReadFailure('management_invalid_page')
    if any(not matches(row,filters) for row in rows):
        raise ManagementReadFailure('management_filter_not_honored')
    projected=[]
    for row in rows:
        out={}
        for field in FIELDS[name]:
            value=row.get(field)
            if field in row and (value is None or type(value) in (str,int,float,bool)):
                if isinstance(value,str) and len(value)>1000:raise ManagementReadFailure('management_field_too_large')
                out[field]=value
        projected.append(out)
    return {'management_read':{'resource':name,'source':ROUTES[name],'rows':projected,'returned':len(rows),'limit':limit,'offset':offset,'possibly_more':len(rows)==limit,'filters':filters},'history_complete':False}
