import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
from anthropic import AsyncAnthropic

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
TZ = ZoneInfo("America/Sao_Paulo")
MODEL = os.getenv("MICHAEL_PERSONAL_MODEL", "claude-haiku-4-5-20251001")


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits and not digits.startswith("55") and len(digits) in (10, 11):
        digits = "55" + digits
    return digits


def personal_phone() -> str:
    return normalize_phone(os.getenv("MICHAEL_PERSONAL_PHONE", ""))


def is_personal_phone(phone: str) -> bool:
    expected = personal_phone()
    return bool(expected) and normalize_phone(phone) == expected


def is_configured() -> bool:
    return bool(
        personal_phone()
        and os.getenv("MICHEL_CONTENT_INGEST_SECRET", "").strip()
        and os.getenv("MICHEL_CONTENT_SUPABASE_URL", "").strip()
        and os.getenv("MICHEL_CONTENT_PUBLISHABLE_KEY", "").strip()
        and settings.ANTHROPIC_API_KEY
        and settings.ANTHROPIC_API_KEY != "stub"
    )


def _strip_json(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    first, last = text.find("{"), text.rfind("}")
    return text[first:last + 1] if first >= 0 and last > first else text


def _iso(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ).isoformat()
    except Exception:
        return None


def _default_reminder(kind: str, scheduled_at: Optional[str], remind_at: Optional[str]) -> Optional[str]:
    explicit = _iso(remind_at)
    if explicit:
        return explicit
    scheduled = _iso(scheduled_at)
    if not scheduled:
        return None
    dt = datetime.fromisoformat(scheduled)
    if kind in {"calendar", "content"}:
        return (dt - timedelta(minutes=10)).isoformat()
    if kind == "reminder":
        return dt.isoformat()
    return None


async def rpc(function_name: str, payload: dict[str, Any]) -> Any:
    base_url = os.getenv("MICHEL_CONTENT_SUPABASE_URL", "").rstrip("/")
    publishable_key = os.getenv("MICHEL_CONTENT_PUBLISHABLE_KEY", "").strip()
    secret = os.getenv("MICHEL_CONTENT_INGEST_SECRET", "").strip()
    if not base_url or not publishable_key or not secret:
        raise RuntimeError("Integração pessoal não configurada")
    data = {"p_secret": secret, **payload}
    headers = {
        "apikey": publishable_key,
        "Authorization": f"Bearer {publishable_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(f"{base_url}/rest/v1/rpc/{function_name}", headers=headers, json=data)
    if response.status_code >= 400:
        logger.error("[MICHAEL PESSOAL] RPC %s falhou %s", function_name, response.status_code)
        raise RuntimeError("Falha no banco pessoal")
    return response.json() if response.content else None


async def _classify(message: str) -> dict[str, Any]:
    now = datetime.now(TZ)
    prompt = f"""Você classifica comandos pessoais do Michael.
Agora: {now.isoformat()}, fuso America/Sao_Paulo.
Retorne SOMENTE JSON válido com os campos:
action(save|list|chat|clarify), kind(calendar|content|task|note|reminder|null), title, details,
scheduled_at, remind_at, query_from, query_to, query_kind, response, metadata.

Regras:
- ideia de vídeo/reel/story/tiktok/post/conteúdo => content.
- conteúdo com data/hora => content agendado; sem data/hora => ideia.
- reunião/ligar/visitar/compromisso com data/hora => calendar.
- "me lembra" => reminder.
- pendência sem horário => task.
- "anota" sem ação => note.
- "o que tenho hoje/amanhã/essa semana", "minha agenda", "pendências", "ideias" => list.
- conversa casual => chat.
- se faltar data/hora essencial, clarify.
- datas relativas devem ser resolvidas usando a data atual.
- para calendar/content com horário e sem lembrete explícito, remind_at=null; o sistema aplica 10 min antes.
- metadata de content pode conter platform, content_type, hook, objective, context, points, closing, cta, duration, recording, caption, tags, priority.
- nunca misture isso com Doss/CRM.

Mensagem: {message}"""
    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    result = await client.messages.create(
        model=MODEL,
        max_tokens=900,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(block.text for block in result.content if getattr(block, "type", "") == "text")
    parsed = json.loads(_strip_json(raw))
    return parsed if isinstance(parsed, dict) else {}


def _fmt_when(value: Optional[str]) -> str:
    iso = _iso(value)
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%d/%m às %H:%M")


def _format_list(rows: Any) -> str:
    if not isinstance(rows, list) or not rows:
        return "Não achei nada nesse período."
    lines = []
    for row in rows[:12]:
        when = _fmt_when(row.get("scheduled_at"))
        title = row.get("title") or "Item"
        lines.append(f"• {when + ' — ' if when else ''}{title}")
    return "Aqui está:\n" + "\n".join(lines)


async def handle_personal_text(phone: str, message: str, external_message_id: Optional[str] = None, media_type: Optional[str] = None) -> str:
    if not is_personal_phone(phone):
        raise PermissionError("Telefone não autorizado")
    if not is_configured():
        return "O modo pessoal ainda está sendo configurado."

    text = (message or "").strip()
    if not text:
        return "Me manda o que você quer anotar, agendar ou lembrar."

    try:
        intent = await _classify(text)
    except Exception:
        logger.exception("[MICHAEL PESSOAL] Falha ao classificar")
        return "Não consegui interpretar isso agora. Manda de novo em uma frase curta."

    action = str(intent.get("action") or "save").lower()
    kind = str(intent.get("kind") or "note").lower()

    if action == "clarify":
        return str(intent.get("response") or "Qual data ou horário?")[:1500]
    if action == "chat":
        return str(intent.get("response") or "Certo.")[:1500]
    if action == "list":
        try:
            rows = await rpc("bruno_personal_list", {
                "p_phone": phone,
                "p_from": _iso(intent.get("query_from")),
                "p_to": _iso(intent.get("query_to")),
                "p_kind": intent.get("query_kind") or None,
                "p_limit": 50,
            })
            return _format_list(rows)
        except Exception:
            logger.exception("[MICHAEL PESSOAL] Falha ao listar")
            return "Não consegui consultar sua lista agora."

    scheduled_at = _iso(intent.get("scheduled_at"))
    remind_at = _default_reminder(kind, scheduled_at, intent.get("remind_at"))
    metadata = intent.get("metadata") if isinstance(intent.get("metadata"), dict) else {}
    item = {
        "kind": kind if kind in {"calendar", "content", "task", "note", "reminder"} else "note",
        "title": str(intent.get("title") or text)[:500],
        "details": intent.get("details"),
        "scheduled_at": scheduled_at,
        "remind_at": remind_at,
        "metadata": metadata,
    }

    try:
        await rpc("bruno_personal_ingest", {
            "p_phone": phone,
            "p_external_message_id": external_message_id,
            "p_message_text": text,
            "p_media_type": media_type,
            "p_item": item,
        })
    except Exception:
        logger.exception("[MICHAEL PESSOAL] Falha ao salvar")
        return "Entendi, mas não consegui salvar agora. Tenta novamente em instantes."

    when = _fmt_when(scheduled_at)
    if kind == "content":
        return f"Salvei no Michel Content para {when}." if scheduled_at else "Salvei como ideia no Michel Content."
    if kind == "calendar":
        return f"Anotei na sua agenda pessoal para {when}."
    if kind == "reminder":
        return f"Lembrete salvo{f' para {when}' if when else ''}."
    if kind == "task":
        return "Pendência salva na sua lista pessoal."
    return "Anotado na sua área pessoal."
