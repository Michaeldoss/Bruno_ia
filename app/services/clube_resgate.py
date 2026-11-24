"""Route a Club reservation to one of the designated sales contacts."""
import os
import re

import httpx
from fastapi import HTTPException

from app.services import uniplus_client
from app.services.crm_inbox_client import ORG_ID, SUPABASE_URL, _headers
from app.services.twilio_client import twilio_service

CNPJ = re.compile(r"[A-Z0-9]{12}[0-9]{2}")
CODE = re.compile(r"DOSS-RES-[0-9A-F]{16}")


# Stable CRM profile IDs keep new ERP sellers outside the delivery list.
MICHAELSIN = "aa5e61b1-a4dd-4905-9e77-6ab447b61a9f"
DAVID = "a570720f-74cf-4de0-bb52-d8fafde8031a"
RECEIVERS = (MICHAELSIN, DAVID)


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
    code = str(rows[0].get("codigoVendedor") or "").strip()
    seller = code if re.fullmatch(r"[1-9][0-9]{0,14}", code) else None
    mapping = await crm(client, "crm_uniplus_sync", "seller_map", {"limit": 1})
    owner = (mapping[0].get("seller_map") or {}).get(seller) if mapping else None
    allowed = "in.(" + ",".join(RECEIVERS) + ")"
    profiles = await crm(client, "profiles", "id,name", {"id": allowed, "is_active": "eq.true"})
    names = {row["id"]: row["name"] for row in profiles if row["id"] in RECEIVERS}
    if not names:
        return pending("mapping")
    instances = await crm(client, "whatsapp_instances", "agent_id,phone_number", {"agent_id": allowed})
    for agent in sorted(RECEIVERS, key=lambda agent: agent != owner):
        if agent not in names:
            continue
        numbers = {national_number(row["phone_number"]) for row in instances
                   if row.get("agent_id") == agent and row.get("phone_number")}
        if len(numbers) != 1 or "" in numbers:
            continue
        route = {"state": "resolved", "seller": names[agent], "phone": numbers.pop()}
        if agent != owner:
            route["handoff"] = (f"Repassar ao responsável pelo cliente (vendedor Uniplus {seller})."
                                if seller else "Conferir o responsável pelo CNPJ e encaminhar o resgate.")
        return route
    return pending("phone")


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
    description = data.get("description") or ""
    if not isinstance(description, str) or len(description) > 600:
        raise HTTPException(422, "Descrição do resgate inválida.")
    if not twilio_service.client:
        return pending("channel", seller=route["seller"], phone=route["phone"])
    lines = [
        "Clube Doss · Resgate em homologação",
        f"Cliente: {data['company']} · CNPJ {data['cnpj']}",
        f"Produto: {data['title']}",
    ]
    if description:
        lines.append(f"Descrição: {description}")
    lines.extend([
        f"Código do produto (ERP): {data['sku']}",
        f"Cor / variante: {data['color']}",
        f"Quantidade: {data['quantity']} {data['unit']}",
        f"Pontos reservados: {data['points']}",
        f"Código de resgate: {data['code']}",
        "Inclua o código de resgate na observação do pedido.",
    ])
    if route.get("handoff"):
        lines.append(route["handoff"])
    message = "\n".join(lines)
    from twilio.base.exceptions import TwilioRestException
    try:
        template = os.getenv("DOSS_CLUBE_RESGATE_TEMPLATE_SID", "").strip()
        if template:
            variables = {str(i): str(data[k]) for i, k in enumerate(
                ("company", "cnpj", "code", "title", "sku", "color", "quantity", "unit", "points"), 1)}
            if description:
                variables["4"] += " — " + " ".join(description.split())
            if route.get("handoff"):
                variables["4"] += " — " + route["handoff"]
            sid = await twilio_service.send_whatsapp_template_message(route["phone"], template, variables)
        else:
            sid = await twilio_service.send_whatsapp_message(route["phone"], message)
    except TwilioRestException as exc:
        if 400 <= (exc.status or 0) < 500:
            return pending("channel", seller=route["seller"], phone=route["phone"])
        sid = None
    except Exception:
        sid = None
    return {**route, "state": "accepted" if sid else "uncertain", "receipt": sid}
