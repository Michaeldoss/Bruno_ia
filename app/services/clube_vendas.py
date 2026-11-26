"""Read sales and match fiscal items to their original DAV without exposing order text."""
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from xml.etree import ElementTree

from fastapi import HTTPException
from app.services.crm_erp_bridge import ERP_ORIGIN

ISSUER = "45024640000170"
PAGE_SIZE = 12
CODE = re.compile(r"(?<![\w-])DOSS-RES-[A-F0-9]{16}(?![\w-])", re.I)


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


async def stock_payment(client, headers, order, cache):
    label = "Clube Fidelidade"
    def matches(value):
        return isinstance(value, str) and " ".join(value.split()).casefold() == label.casefold()

    direct = order.get("descricaoFormaPagamento") or order.get("formaPagamento")
    if direct and not str(direct).isdigit():
        return label if matches(direct) else ""
    # The DAV may carry a catalog code rather than the payment label.
    for field, route in (("tipoDocumentoFinanceiro", "v1/tipo-documento-financeiro"),
                         ("condicaoPagamento", "v1/condicao-pagamento")):
        value = order.get(field)
        if matches(value):
            return label
        if not str(value).isdigit():
            continue
        key = (route, str(value))
        if key not in cache:
            found = await rows(client, headers, route, {"codigo.eq":str(value), "limit":2})
            cache[key] = len(found)==1 and str(found[0].get("codigo"))==str(value) and matches(found[0].get("descricao"))
        if cache[key]:
            return label
    return ""


def stock_event(sale, lines, event, payment):
    models = {str(i.get("modelo") or "").strip().zfill(2) for i in lines}
    if models != {"01"} or sale.get("chaveAcesso"):
        return None
    notes = " ".join(str(sale.get(k) or "") for k in ("observacao","informacoesComplementares","infCpl"))
    codes = set(CODE.findall(notes.upper()))
    issuer = re.sub(r"[./\s-]", "", str(sale.get("cnpjEmitente") or sale.get("cnpjFilial") or ""))
    number = str(sale.get("documento") or "")
    return {
        **event, "model":"01", "payment":payment, "documentCode":next(iter(codes)) if len(codes)==1 else "",
        "branch":str(sale.get("codigoFilial") or ""), "sale":str(sale["idVenda"]), "number":number,
        "issuer":issuer, "homologation":str(sale.get("tpAmb"))=="2",
        "posted":sale.get("estoqueBaixado") is True and not sale.get("cancelado")
                 and bool(number) and all(str(i.get("documento") or "")==number for i in lines),
        "items":[{"sku":str(i.get("codigoProduto") or ""), "unit":str(i.get("codigoUnidadeMedida") or ""),
                  "quantity":i.get("quantidade")} for i in lines],
    }


def scaled(value, scale):
    try:
        number = Decimal(str(value)) * scale
        if number.is_finite() and 0 <= number <= 2**53 - 1 and number == number.to_integral_value():
            return int(number)
    except (InvalidOperation, ValueError):
        pass
    return None


def line_amounts(item):
    """valorProdutos is the invoice subtotal echoed on every ERP item."""
    try:
        quantity = Decimal(str(item.get("quantidade")))
        price = Decimal(str(item.get("precoUnitario")))
        if not quantity.is_finite() or not price.is_finite() or quantity <= 0 or price < 0:
            return None, None
        gross = scaled((quantity * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), 100)
    except (ValueError, InvalidOperation):
        return None, None
    discount = scaled(item.get("desconto") or 0, 100)
    net = gross - discount if gross is not None and discount is not None and discount <= gross else None
    return gross, net


def purchase_lines(items, key, sale_day, order_days):
    result = []
    for item in items:
        origin = str(item.get("codigoDavOrigem") or "0")
        day = order_days.get(origin)
        if origin == "0" and "codigoDavOrigem" in item:
            day = sale_day
        if str(item.get("chaveAcesso")) != key:
            day = None
        _, net = line_amounts(item)
        result.append({"day":day,
                       "order":origin if day and origin in order_days else None,
                       "directSale":bool(day and "codigoDavOrigem" in item and origin == "0"),
                       "sku":str(item.get("codigoProduto") or ""),
                       "unit":str(item.get("codigoUnidadeMedida") or ""),
                       "qty":scaled(item.get("quantidade"), 10000),
                       "cents":net})
    return result


def document_id(value):
    return re.sub(r"[./\s-]", "", str(value or "")).upper()


async def sale_recipient(client, headers, sale, order_recipients):
    recipients = set(order_recipients)
    direct = document_id(sale.get("cnpjCpfCliente"))
    if direct:
        recipients.add(direct)
    if not recipients:
        customer = str(sale["codigoCliente"])
        res = await client.get(ERP_ORIGIN + "/public-api/v1/entidades/" + customer, headers=headers)
        if res.status_code == 404:
            return None
        res.raise_for_status()
        entity = res.json()
        if str(entity.get("codigo")) != customer:
            raise ValueError("customer_filter_not_honored")
        recipients.add(document_id(entity.get("cnpjCpf")))
    if len(recipients) != 1 or not next(iter(recipients)):
        return None
    return recipients.pop()


def preview_document(sale, key, recipient, day, items, dated, redeemed_orders):
    lines = []
    for item, match in zip(items, dated):
        gross, net = line_amounts(item)
        cfop = re.match(r"^\s*(\d{4})(?!\d)", str(item.get("cfop") or ""))
        lines.append({**match, "description":str(item.get("descricaoProduto") or "")[:240],
                      "cfop":cfop.group(1) if cfop else "", "gross":gross, "cents":net,
                      "unitPrice":str(item.get("precoUnitario") or "0"),
                      "gift":item.get("isBrinde") in (True,1,"1","S"),
                      "redeemed":match["order"] in redeemed_orders,
                      "matchesKey":str(item.get("chaveAcesso")) == key})
    return {"key":key,"recipient":recipient,"day":day,"status":str(sale.get("status") or ""),
            "cancelled":sale.get("cancelado") in (True,1,"1","S"),
            "nature":str(sale.get("naturezaOperacao") or sale.get("natOp") or "")[:120],
            "voucher":bool(CODE.search(" ".join(str(sale.get(f) or "") for f in ("observacao","informacoesComplementares","infCpl")))),
            "gross":scaled(sale.get("valorProdutos"),100),"discount":scaled(sale.get("desconto") or 0,100),
            "lines":lines}


async def sales_page(client, headers, day, offset, members=None):
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
    purchase_dates, previews, unidentified = [], [], []
    count_items = 0
    stock_events, payment_cache = [], {}
    for sale in sales:
        if sale.get("chaveAcesso") and not matching_key(sale):
            continue
        customer, key = str(sale.get("codigoCliente","")), matching_key(sale)
        if not customer.isdigit(): continue
        items = await sale_items(client,headers,str(sale["idVenda"]),customer)
        item_fields.update(field_names(items))
        count_items += len(items)
        if key:
            invoices.extend(homologated_xml(sale))
        elif not items or any(str(it.get("modelo") or "").strip().zfill(2)!="01" for it in items):
            continue
        status = str(sale.get("status",""))
        if re.fullmatch(r"[A-Za-z0-9]{1,4}",status): states[status] += 1
        order_days, order_recipients, redeemed_orders = {}, set(), set()
        grouped = {}
        for it in items:
            order_id=str(it.get("codigoDavOrigem") or "")
            if not order_id.isdigit() or int(order_id)==0 or (key and str(it.get("chaveAcesso"))!=key): continue
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
            if key:
                linked_keys.add(key)
                recipient = document_id(order.get("cnpjCpfCliente"))
                if recipient:
                    order_recipients.add(recipient)
                if CODE.search(str(order.get("observacao") or "")):
                    redeemed_orders.add(number)
                raw_day = str(order.get("data") or "")[:10]
                try:
                    order_day = date.fromisoformat(raw_day).isoformat()
                    if order_day <= day:
                        order_days[number] = order_day
                except ValueError:
                    pass
            event=order_match(order)
            if event and not key:
                if len(lines) != len(items):
                    continue
                payment = await stock_payment(client, headers, order, payment_cache)
                stock = stock_event(sale, lines, event, payment)
                if stock:
                    stock_events.append(stock)
            elif event:
                events.append({**event,"key":key,"items":[{"sku":str(i.get("codigoProduto") or ""),
                    "unit":str(i.get("codigoUnidadeMedida") or ""),"quantity":i.get("quantidade")} for i in lines]})
        if key:
            dated = purchase_lines(items,key,day,order_days)
            if dated:
                purchase_dates.append({"key":key,"items":dated})
            if members:
                recipient = await sale_recipient(client,headers,sale,order_recipients)
                if not recipient:
                    unidentified.append(key)
                elif recipient in members:
                    previews.append(preview_document(sale,key,recipient,day,items,dated,redeemed_orders))
    return {"unidentified":unidentified,"memberInvoices":previews,"purchaseDates":purchase_dates,"date":day,"offset":offset,"sales":len(sales),"issuerInvoices":len(qualified),"orders":len(orders),
            "items":count_items,"orderCodes":sum(bool(order_match(o)) for o in orders),"linkedInvoices":len(linked_keys),
            "stockEvents":stock_events,"orderEvents":events,"documents":invoices,"statusCounts":dict(states),
            "nextOffset":offset+PAGE_SIZE if max(len(sales),len(orders))==PAGE_SIZE else None,
            "fingerprint":hashlib.sha256(json.dumps([sale_ids,[str(d.get("codigo")) for d in orders]]).encode()).hexdigest(),
            "fields":{"sales":field_names(sales),"items":sorted(item_fields),"orders":field_names(orders)}}
