"""Route a Club reservation to the seller registered for its customer."""
import re

import httpx
from fastapi import HTTPException

from app.services import uniplus_client
from app.services.crm_inbox_client import ORG_ID, SUPABASE_URL, _headers
from app.services.twilio_client import twilio_service

CNPJ = re.compile(r"[A-Z0-9]{12}[0-9]{2}")
CODE = re.compile(r"DOSS-RES-[0-9A-F]{16}")


def pending(reason, **details):
    return {"state": "pending", "reason": reason, **details}


async def crm(client, table, fields, filters):
    response = await client.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=_headers(),
                                params={"select": fields, "org_id": f"eq.{ORG_ID}", **filters})
    response.raise_for_status()
    return response.json()


def national_number(value):
    digits = re.sub(r"[\s()+.-]", "", str(value or ""))
    if re.fullmatch(r"[1-9][0-9]{9,10}", digits):
        digits = "55" + digits
    return digits if re.fullmatch(r"55[1-9][0-9]{9,10}", digits) else ""


async def resolve(client, cnpj):
    document = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
    response = await client.get(uniplus_client.UNIPLUS_BASE_URL.rstrip("/") + "/public-api/v1/entidades",
                                headers=await uniplus_client._headers(), params={"cnpjCpf": document, "limit": 2})
    response.raise_for_status()
    rows = response.json()
    if isinstance(rows, dict):
        rows = [rows]
    if len(rows) != 1 or re.sub(r"[./\s-]", "", str(rows[0].get("cnpjCpf") or "")).upper() != cnpj:
        return pending("customer")
    entity = rows[0]
    # 'vendedor' is also an entity role flag. Only use the preferred seller code.
    seller = str(entity.get("codigoVendedor") or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,14}", seller):
        return pending("seller")
    mapping = await crm(client, "crm_uniplus_sync", "seller_map", {"limit": 1})
    agent = mapping[0]["seller_map"].get(seller) if mapping else None
    if not agent:
        return pending("mapping")
    profiles = await crm(client, "profiles", "id,name", {"id": f"eq.{agent}", "is_active": "eq.true"})
    if len(profiles) != 1:
        return pending("mapping")
    name = profiles[0]["name"]
    instances = await crm(client, "whatsapp_instances", "phone_number", {"agent_id": f"eq.{agent}"})
    numbers = {national_number(row["phone_number"]) for row in instances if row.get("phone_number")}
    if len(numbers) != 1 or "" in numbers:
        return pending("phone", seller=name)
    return {"state": "resolved", "seller": name, "phone": numbers.pop()}


async def handle_voucher(data):
    if not isinstance(data, dict) or not CNPJ.fullmatch(str(data.get("cnpj", ""))) or not CODE.fullmatch(str(data.get("code", ""))):
        raise HTTPException(422, "Código ou CNPJ inválido.")
    mode = data.get("mode")
    if mode == "status":
        sid = str(data.get("receipt") or "")
        if not re.fullmatch(r"SM[0-9a-fA-F]{32}", sid):
            raise HTTPException(422, "Comprovante inválido.")
        import asyncio
        receipt = await asyncio.to_thread(twilio_service.client.messages(sid).fetch)
        if receipt.status in ("failed", "undelivered", "canceled"):
            return pending("template" if str(receipt.error_code) == "63016" else "channel")
        return {"state": "delivered" if receipt.status in ("delivered", "read") else "accepted"}
    if mode not in ("resolve", "send"):
        raise HTTPException(422, "Operação inválida.")
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            route = await resolve(client, data["cnpj"])
    except Exception:
        return pending("integration")
    if mode == "resolve" or route["state"] != "resolved":
        return route
    fields = ("company", "title", "sku", "color", "unit")
    if not all(isinstance(data.get(k), str) and 0 < len(data[k]) <= 260 for k in fields) or not all(
        type(data.get(k)) is int and data[k] > 0 for k in ("quantity", "points")
    ):
        raise HTTPException(422, "Dados do resgate inválidos.")
    if not twilio_service.client:
        return pending("channel", seller=route["seller"], phone=route["phone"])
    message = (f"Clube Doss · Resgate em homologação\n{data['company']} · CNPJ {data['cnpj']}\n"
               f"Código: {data['code']}\n{data['title']} · SKU {data['sku']}\n"
               f"{data['color']} · {data['quantity']} {data['unit']} · {data['points']} pontos\n"
               "Inclua o código na observação do pedido do cliente.")
    from twilio.base.exceptions import TwilioRestException
    try:
        sid = await twilio_service.send_whatsapp_message(route["phone"], message)
    except TwilioRestException as exc:
        if 400 <= (exc.status or 0) < 500:
            return pending("channel", seller=route["seller"], phone=route["phone"])
        sid = None
    except Exception:
        sid = None
    return {**route, "state": "accepted" if sid else "uncertain", "receipt": sid}
