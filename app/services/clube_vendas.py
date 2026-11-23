"""Bounded live sale, item and DAV reads using the same OAuth connection as the CRM."""
from datetime import date
import hashlib
import json
import logging
import re

from fastapi import HTTPException
from app.services.crm_erp_bridge import ERP_ORIGIN

logger = logging.getLogger(__name__)
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


def day_of(row, field):
    return str(row.get(field, ""))[:10]


def field_names(records):
    return sorted({key for row in records for key in row if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,66}", key)})


def matching_key(row):
    key = str(row.get("chaveAcesso") or "")
    if not re.fullmatch(r"\d{44}", key) or key[6:20] != ISSUER or key[20:22] != "55":
        return None
    remainder = sum(int(v) * (2 + i % 8) for i, v in enumerate(reversed(key[:-1]))) % 11
    return key if int(key[-1]) == (0 if remainder < 2 else 11 - remainder) else None


def direct_codes(record):
    values = [v for k, v in record.items() if k.lower().startswith(("obs", "infcompl", "informac", "dadosadicion")) and isinstance(v, str)]
    return {c.upper() for value in values for c in CODE.findall(value)}


async def sales_page(client, headers, day, offset):
    sales = await rows(client, headers, "v2/venda", {"emissao.eq": day, "offset": offset, "limit": PAGE_SIZE})
    if any(day_of(s, "emissao") != day for s in sales):
        raise ValueError("sale_filter_not_honored")
    ids = [str(s.get("idVenda", "")) for s in sales]
    if any(not s.isdigit() for s in ids) or len(set(ids)) != len(ids):
        raise ValueError("sale_id_ambiguous")
    qualified = [s for s in sales if matching_key(s)]
    # For a preview outside the wallet only counts and field names leave the ERP service.
    found, invoices, item_fields, count_items = [], [], set(), 0
    for s in qualified:
        sale_id, customer = str(s["idVenda"]), str(s.get("codigoCliente", ""))
        if not customer.isdigit():
            continue
        items = []
        for item_offset in range(0, 1000, 100):
            part = await rows(client, headers, "v2/venda-item", {"idVenda.eq": sale_id, "codigoCliente.eq": customer, "limit": 100, "offset": item_offset})
            if any(str(i.get("idVenda")) != sale_id or str(i.get("codigoCliente")) != customer for i in part):
                raise ValueError("item_filter_not_honored")
            items.extend(part)
            if len(part) < 100:
                break
        else:
            raise ValueError("item_page_limit")
        ids_items = [str(i.get("iditem") or "") for i in items]
        if any(not v.isdigit() for v in ids_items) or len(set(ids_items)) != len(ids_items):
            raise ValueError("item_id_ambiguous")
        item_fields.update(field_names(items))
        count_items += len(items)
        found.append(matching_key(s))
        # If a sale embeds the processed fiscal document, pass only an authentic
        # homologation document to the existing reconciler, with its original protocol.
        for key in ("xmlNFe", "xmlNfe", "xmlAutorizado", "xml"):
            xml = s.get(key)
            if isinstance(xml, str) and "<" in xml[:100] and len(xml.encode()) <= 1048576:
                from app.api.clube_fiscal import pilot_documents, NFE
                import io
                import zipfile
                from xml.etree import ElementTree as ET
                with zipfile.ZipFile(io.BytesIO(pilot_documents(xml.encode()))) as archive:
                    for member in archive.namelist():
                        data = archive.read(member)
                        root = ET.fromstring(data)
                        if root.findtext(NFE+"protNFe/"+NFE+"infProt/"+NFE+"chNFe") == matching_key(s):
                            invoices.append(data.decode())
                break
    davs = await rows(client, headers, "v1/davs", {"data.eq": day, "offset": offset, "limit": PAGE_SIZE})
    if any(day_of(d, "data") != day for d in davs):
        raise ValueError("dav_filter_not_honored")
    order_fields = set(field_names(davs))
    dav_codes = 0
    for d in davs:
        dav_codes += bool(direct_codes(d))
    result = {"date": day, "offset":offset, "sales": len(sales), "issuerInvoices":len(qualified),
              "orders":len(davs), "items":count_items, "orderCodes":dav_codes, "documents": invoices,
              "nextOffset": offset+PAGE_SIZE if max(len(sales),len(davs))==PAGE_SIZE else None,
              "fingerprint":hashlib.sha256(json.dumps([ids,[str(d.get("codigo")) for d in davs]]).encode()).hexdigest(),
              "fields":{"sales":field_names(sales),"items":sorted(item_fields),"orders":sorted(order_fields)}}
    return result
