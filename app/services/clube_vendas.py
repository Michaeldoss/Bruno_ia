"""Read sales and match fiscal items to their original DAV without exposing order text."""
import hashlib
import io
import json
import re
import time
import zipfile
from collections import Counter
from xml.etree import ElementTree

from fastapi import HTTPException
from app.services.crm_erp_bridge import ERP_ORIGIN

ISSUER = "45024640000170"
PAGE_SIZE = 12
CODE = re.compile(r"(?<![\w-])DOSS-RES-[A-F0-9]{16}(?![\w-])", re.I)
_schema_cache = {}


async def rows(client, headers, route, params):
    res = await client.get(ERP_ORIGIN + "/public-api/" + route, headers=headers, params=params)
    if res.status_code != 200:
        raise HTTPException(502, f"Consulta Uniplus {route} HTTP {res.status_code}.")
    body = res.json()
    if not isinstance(body, list) or len(body) > params.get("limit", 100) or any(not isinstance(r, dict) for r in body):
        raise ValueError("invalid_or_unbounded_page")
    return body


def field_names(records):
    return sorted({k for row in records for k in row if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,66}", k)})


def matching_key(row):
    key = str(row.get("chaveAcesso") or "")
    if not re.fullmatch(r"\d{44}", key) or key[6:20] != ISSUER or key[20:22] != "55":
        return None
    remainder = sum(int(v) * (2 + i % 8) for i, v in enumerate(reversed(key[:-1]))) % 11
    return key if int(key[-1]) == (0 if remainder < 2 else 11 - remainder) else None


def order_match(order):
    codes = set(CODE.findall(str(order.get("observacao") or "").upper()))
    cnpj = re.sub(r"[./\s-]", "", str(order.get("cnpjCpfCliente") or "")).upper()
    number = str(order.get("codigo", ""))
    if len(codes) != 1 or not re.fullmatch(r"[A-Z0-9]{12}\d{2}", cnpj) or not number.isdigit():
        return None
    return {"code": codes.pop(), "recipient": cnpj, "order": number}


async def sale_items(client, headers, sale_id, customer):
    items = []
    for offset in range(0, 1000, 100):
        part = await rows(client, headers, "v2/venda-item", {"idVenda.eq": sale_id, "codigoCliente.eq": customer, "limit": 100, "offset": offset})
        if any(str(i.get("idVenda")) != sale_id or str(i.get("codigoCliente")) != customer for i in part):
            raise ValueError("item_filter_not_honored")
        items.extend(part)
        if len(part) < 100:
            break
    else:
        raise ValueError("item_page_limit")
    identifiers = [str(i.get("iditem") or "") for i in items]
    if any(not v.isdigit() for v in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("item_id_ambiguous")
    return items


def homologated_xml(sale):
    from app.api.clube_fiscal import pilot_documents, NFE
    result = []
    for name in ("xmlNFe", "xmlNfe", "xmlAutorizado", "xml"):
        xml = sale.get(name)
        if not isinstance(xml, str) or "<" not in xml[:100] or len(xml.encode()) > 1048576:
            continue
        with zipfile.ZipFile(io.BytesIO(pilot_documents(xml.encode()))) as archive:
            for member in archive.namelist():
                data = archive.read(member)
                root = ElementTree.fromstring(data)
                if root.findtext(NFE+"protNFe/"+NFE+"infProt/"+NFE+"chNFe") == matching_key(sale):
                    result.append(data.decode())
        break
    return result


async def fiscal_routes(client, headers):
    # Account mapping utilities name these three possible fiscal resources.
    # Only expose HTTP status and field names; absence of a resource never stops DAV processing.
    if _schema_cache.get("until", 0) > time.monotonic():
        return _schema_cache["result"]
    result = {}
    for path in ("v1/notas", "v1/nota-fiscal", "v1/notas-fiscais"):
        try:
            res = await client.get(ERP_ORIGIN+"/public-api/"+path, headers=headers, params={"limit":1,"offset":0}, timeout=3)
            fields = []
            if res.status_code == 200 and len(res.content) <= 100000:
                body = res.json()
                fields = field_names([b for b in (body[:1] if isinstance(body,list) else [body]) if isinstance(b,dict)])
            result[path] = {"http":res.status_code,"fields":fields}
        except (ValueError, OSError): result[path] = {"http":None}
        except Exception: result[path] = {"http":None}
    _schema_cache.update(until=time.monotonic()+86400,result=result)
    return result


async def sales_page(client, headers, day, offset):
    import asyncio
    sales, orders = await asyncio.gather(
        rows(client, headers, "v2/venda", {"emissao.eq":day,"offset":offset,"limit":PAGE_SIZE}),
        rows(client, headers, "v1/davs", {"data.eq":day,"offset":offset,"limit":PAGE_SIZE}))
    if any(str(s.get("emissao",""))[:10]!=day for s in sales) or any(str(o.get("data",""))[:10]!=day for o in orders):
        raise ValueError("date_filter_not_honored")
    sale_ids = [str(s.get("idVenda","")) for s in sales]
    if any(not n.isdigit() for n in sale_ids) or len(set(sale_ids)) != len(sale_ids):
        raise ValueError("sale_id_ambiguous")
    by_number = {str(o.get("codigo")):o for o in orders}
    if len(by_number)!=len(orders) or any(not n.isdigit() for n in by_number):
        raise ValueError("order_id_ambiguous")
    events = [v for o in orders if (v:=order_match(o))]
    qualified = [s for s in sales if matching_key(s)]
    invoices, item_fields, linked_keys, states = [], set(), set(), Counter()
    count_items = 0
    for sale in qualified:
        customer, key = str(sale.get("codigoCliente","")), matching_key(sale)
        if not customer.isdigit(): continue
        items = await sale_items(client,headers,str(sale["idVenda"]),customer)
        item_fields.update(field_names(items))
        count_items += len(items)
        invoices.extend(homologated_xml(sale))
        status = str(sale.get("status",""))
        if re.fullmatch(r"[A-Za-z0-9]{1,4}",status): states[status] += 1
        grouped = {}
        for it in items:
            order_id=str(it.get("codigoDavOrigem") or "")
            if not order_id.isdigit() or int(order_id)==0 or str(it.get("chaveAcesso"))!=key: continue
            grouped.setdefault(order_id,[]).append(it)
        for number, lines in grouped.items():
            if number not in by_number:
                res=await client.get(ERP_ORIGIN+"/public-api/v1/davs/"+number,headers=headers)
                if res.status_code == 404: continue
                res.raise_for_status()
                o=res.json()
                if not isinstance(o,dict) or str(o.get("codigo"))!=number: raise ValueError("order_key_mismatch")
                by_number[number]=o
            order=by_number[number]
            if str(order.get("cliente"))!=customer or str(order.get("filial"))!=str(sale.get("codigoFilial")): continue
            if any(str(i.get("tipoDavOrigem"))!=str(order.get("tipoDocumento")) for i in lines): continue
            linked_keys.add(key)
            event=order_match(order)
            if event:
                events.append({**event,"key":key,"items":[{"sku":str(i.get("codigoProduto") or ""),
                    "unit":str(i.get("codigoUnidadeMedida") or ""),"quantity":i.get("quantidade")} for i in lines]})
    return {"date":day,"offset":offset,"sales":len(sales),"issuerInvoices":len(qualified),"orders":len(orders),
            "items":count_items,"orderCodes":sum(bool(order_match(o)) for o in orders),"linkedInvoices":len(linked_keys),
            "orderEvents":events,"documents":invoices,"statusCounts":dict(states),"fiscalRoutes":await fiscal_routes(client,headers) if qualified else {},
            "nextOffset":offset+PAGE_SIZE if max(len(sales),len(orders))==PAGE_SIZE else None,
            "fingerprint":hashlib.sha256(json.dumps([sale_ids,[str(d.get("codigo")) for d in orders]]).encode()).hexdigest(),
            "fields":{"sales":field_names(sales),"items":sorted(item_fields),"orders":field_names(orders)}}
