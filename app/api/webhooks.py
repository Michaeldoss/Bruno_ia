from fastapi import APIRouter, Form, Request, BackgroundTasks, Response, HTTPException
from twilio.twiml.messaging_response import MessagingResponse
from app.services.twilio_client import twilio_service
from app.services.openai_client import process_message_with_assistant, create_thread, transcribe_audio, get_typing_delay
from app.services.finance_service import finance_service
from app.services.buffer_service import message_buffer
from app.core.media_catalog import MEDIA_CATALOG
from app.services.followup_service import resetar_followup
from app.services.satisfacao_service import verificar_resposta_satisfacao, _tick as _tick_satisfacao
from app.services.crm_inbox_client import log_message as log_message_to_crm, human_active_recently
from app.models.database import SessionLocal, Lead, MediaSent, Conversation, LeadState
from app.config import get_settings
import logging
import asyncio
import base64
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

_processed_sids: set = set()


def _is_duplicate(message_sid: str) -> bool:
    if message_sid in _processed_sids:
        return True
    _processed_sids.add(message_sid)
    if len(_processed_sids) > 1000:
        _processed_sids.clear()
    return False


def _crm_media_type(content_type: Optional[str]) -> str:
    mime = (content_type or "").lower().split(";", 1)[0].strip()
    if mime.startswith("audio/"):
        return "audio"
    if mime == "image/gif":
        return "gif"
    if "sticker" in mime or mime == "image/webp":
        return "sticker"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    return "document"


def _crm_media_label(msg_type: str, body: Optional[str] = None) -> str:
    caption = (body or "").strip()
    labels = {
        "audio": "[ÁUDIO]",
        "image": "[IMAGEM]",
        "gif": "[GIF]",
        "video": "[VÍDEO]",
        "sticker": "[FIGURINHA]",
        "document": "[ARQUIVO]",
    }
    return f"{labels.get(msg_type, '[MÍDIA]')} {caption}".strip()


def _encode_twilio_media_url(media_url: str) -> str:
    return base64.urlsafe_b64encode(media_url.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_twilio_media_url(encoded_url: str) -> str:
    padding = "=" * (-len(encoded_url) % 4)
    try:
        return base64.urlsafe_b64decode(encoded_url + padding).decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="URL de mídia inválida") from exc


def _build_media_proxy_url(public_base_url: str, media_url: str) -> str:
    encoded = _encode_twilio_media_url(media_url)
    # O router é registrado em main.py com prefixo /webhooks.
    return f"{public_base_url.rstrip('/')}/webhooks/twilio-media/{encoded}"


@router.get("/twilio-media/{encoded_url}", name="twilio_media_proxy")
async def twilio_media_proxy(encoded_url: str, request: Request):
    media_url = _decode_twilio_media_url(encoded_url)
    parsed = urlparse(media_url)
    hostname = (parsed.hostname or "").lower()

    if parsed.scheme != "https" or hostname not in {"api.twilio.com", "media.twiliocdn.com"}:
        raise HTTPException(status_code=400, detail="Origem de mídia não permitida")

    account_sid = settings.TWILIO_ACCOUNT_SID
    auth_token = settings.TWILIO_AUTH_TOKEN
    if not account_sid or not auth_token or account_sid == "stub" or auth_token == "stub":
        logger.error("[MÍDIA] Credenciais Twilio ausentes no backend")
        raise HTTPException(status_code=503, detail="Mídia temporariamente indisponível")

    upstream_headers = {}
    range_header = request.headers.get("range")
    if range_header:
        upstream_headers["Range"] = range_header

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            result = await client.get(
                media_url,
                auth=(account_sid, auth_token),
                headers=upstream_headers,
            )
            result.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("[MÍDIA] Twilio respondeu %s ao buscar arquivo", exc.response.status_code)
        raise HTTPException(status_code=502, detail="Não foi possível obter a mídia") from exc
    except Exception as exc:
        logger.error("[MÍDIA] Falha ao buscar arquivo na Twilio: %s", exc)
        raise HTTPException(status_code=502, detail="Não foi possível obter a mídia") from exc

    content_type = (result.headers.get("content-type") or "application/octet-stream").split(";", 1)[0]
    response_headers = {
        "Cache-Control": "private, max-age=3600",
        "Content-Disposition": result.headers.get("content-disposition", "inline"),
        "X-Content-Type-Options": "nosniff",
        "Access-Control-Allow-Origin": "*",
        "Accept-Ranges": result.headers.get("accept-ranges", "bytes"),
    }
    for header_name in ("content-range", "content-length", "etag", "last-modified"):
        value = result.headers.get(header_name)
        if value:
            response_headers["-".join(part.capitalize() for part in header_name.split("-"))] = value

    return Response(
        content=result.content,
        status_code=result.status_code,
        media_type=content_type,
        headers=response_headers,
    )


async def _mirror_inbound_media(
    phone: str,
    body: Optional[str],
    media_items: list[tuple[str, str]],
    message_sid: Optional[str],
    public_base_url: str,
) -> None:
    for index, (media_url, content_type) in enumerate(media_items):
        msg_type = _crm_media_type(content_type)
        content = _crm_media_label(msg_type, body if index == 0 else None)
        whatsapp_id = f"{message_sid}:{index}" if message_sid else None
        proxy_url = _build_media_proxy_url(public_base_url, media_url)
        await log_message_to_crm(
            phone,
            content,
            is_from_contact=True,
            msg_type=msg_type,
            media_url=proxy_url,
            whatsapp_id=whatsapp_id,
        )


def find_all_media_for_text(text: str) -> list:
    if not text:
        return []
    text_lower = text.lower()
    sorted_keys = sorted(MEDIA_CATALOG.keys(), key=len, reverse=True)
    found = {}
    for key in sorted_keys:
        pattern = r"\b" + re.escape(key) + r"\b"
        if re.search(pattern, text_lower):
            media = MEDIA_CATALOG[key]
            product_id = media.get("video") or media.get("image")
            if product_id not in found:
                found[product_id] = (key, media)
    return list(found.values())


@router.post("/twils")
async def twilio_webhook(
    background_tasks: BackgroundTasks,
    request: Request,
    From: str = Form(...),
    Body: Optional[str] = Form(None),
    MediaUrl0: Optional[str] = Form(None),
    MediaContentType0: Optional[str] = Form(None),
    MessageSid: Optional[str] = Form(None),
):
    if MessageSid and _is_duplicate(MessageSid):
        logger.warning(f"[WEBHOOK] Mensagem duplicada ignorada: {MessageSid}")
        return Response(content=str(MessagingResponse()), media_type="application/xml")

    phone = From.replace("whatsapp:", "")
    logger.info("[WEBHOOK] Recebido de %s | SID: %s", From, MessageSid)

    form = await request.form()
    try:
        num_media = int(form.get("NumMedia") or (1 if MediaUrl0 else 0))
    except (TypeError, ValueError):
        num_media = 1 if MediaUrl0 else 0

    media_items: list[tuple[str, str]] = []
    for index in range(max(0, num_media)):
        media_url = form.get(f"MediaUrl{index}")
        content_type = form.get(f"MediaContentType{index}") or "application/octet-stream"
        if media_url:
            media_items.append((str(media_url), str(content_type)))

    if Body and not media_items and await verificar_resposta_satisfacao(phone, Body):
        return Response(content=str(MessagingResponse()), media_type="application/xml")

    if media_items:
        resetar_followup(phone)
        public_base_url = str(request.base_url).rstrip("/")
        background_tasks.add_task(
            _mirror_inbound_media,
            phone,
            Body,
            media_items,
            MessageSid,
            public_base_url,
        )

        first_url, first_content_type = media_items[0]
        if _crm_media_type(first_content_type) == "audio":
            background_tasks.add_task(
                handle_async_response,
                phone,
                None,
                first_url,
                first_content_type,
                True,
            )
        elif Body:
            background_tasks.add_task(
                handle_async_response,
                phone,
                Body,
                None,
                None,
                True,
            )
    elif Body:
        resetar_followup(phone)
        background_tasks.add_task(message_buffer.add_message, phone, Body, process_deferred_message)

    return Response(content=str(MessagingResponse()), media_type="application/xml")


async def process_deferred_message(phone: str, combined_message: str):
    logger.info("[WEBHOOK] Buffer liberado para %s", phone)
    await handle_async_response(phone, combined_message, None, None)


async def handle_async_response(
    phone: str,
    user_message: Optional[str],
    audio_url: Optional[str],
    content_type: Optional[str],
    already_mirrored: bool = False,
):
    db = SessionLocal()
    try:
        lead_state = db.query(LeadState).filter(LeadState.phone == phone).first()
        if lead_state and lead_state.stage == "closed":
            if not already_mirrored:
                if user_message:
                    asyncio.create_task(log_message_to_crm(phone, user_message, is_from_contact=True))
                elif audio_url and content_type and "audio" in content_type:
                    transcription = await transcribe_audio(audio_url)
                    if transcription:
                        asyncio.create_task(log_message_to_crm(phone, f"[ÁUDIO] {transcription}", is_from_contact=True))
            return

        if await human_active_recently(phone):
            if not already_mirrored:
                if user_message:
                    asyncio.create_task(log_message_to_crm(phone, user_message, is_from_contact=True))
                elif audio_url and content_type and "audio" in content_type:
                    transcription = await transcribe_audio(audio_url)
                    if transcription:
                        asyncio.create_task(log_message_to_crm(phone, f"[ÁUDIO] {transcription}", is_from_contact=True))
            return

        lead = db.query(Lead).filter(Lead.phone == phone).first()
        if not lead:
            thread_id = await create_thread()
            lead = Lead(phone=phone, thread_id=thread_id)
            db.add(lead)
            db.commit()
            db.refresh(lead)

        thread_id = lead.thread_id

        if audio_url and content_type and "audio" in content_type:
            transcription = await transcribe_audio(audio_url)
            if transcription:
                user_message = f"[ÁUDIO] {transcription}"
            else:
                await twilio_service.send_whatsapp_message(phone, "Desculpe, não consegui ouvir seu áudio. Pode repetir?")
                return

        if not user_message:
            logger.warning("[WEBHOOK] Mensagem vazia para %s", phone)
            return

        if not already_mirrored:
            asyncio.create_task(log_message_to_crm(phone, user_message, is_from_contact=True))

        response_chunks = await process_message_with_assistant(thread_id, user_message)
        first_message = True
        for chunk in response_chunks:
            if not first_message:
                await asyncio.sleep(3.0)
            await asyncio.sleep(get_typing_delay(chunk))
            await twilio_service.send_whatsapp_message(phone, chunk)
            asyncio.create_task(log_message_to_crm(phone, chunk, is_from_contact=False))
            first_message = False

        texto_combinado = (user_message or "") + " " + " ".join(response_chunks)
        resultados = find_all_media_for_text(texto_combinado)

        if not resultados:
            ultimas = (
                db.query(Conversation)
                .filter(Conversation.phone == phone)
                .order_by(Conversation.created_at.desc())
                .limit(10)
                .all()
            )
            historico_texto = " ".join(m.content for m in ultimas if m.content)
            resultados = find_all_media_for_text(historico_texto)

        if resultados:
            db_media = SessionLocal()
            try:
                for product_key, media in resultados:
                    ja_enviou = db_media.query(MediaSent).filter(
                        MediaSent.phone == phone,
                        MediaSent.product_key == product_key,
                    ).first()
                    if ja_enviou:
                        continue

                    await asyncio.sleep(2.0)
                    if media.get("image"):
                        await twilio_service.send_whatsapp_message(phone, media_url=media["image"])
                        asyncio.create_task(log_message_to_crm(
                            phone,
                            f"[imagem] {product_key}",
                            is_from_contact=False,
                            msg_type="image",
                            media_url=media["image"],
                        ))
                        await asyncio.sleep(2.0)

                    if media.get("video"):
                        await twilio_service.send_whatsapp_message(phone, media_url=media["video"])
                        asyncio.create_task(log_message_to_crm(
                            phone,
                            f"[video] {product_key}",
                            is_from_contact=False,
                            msg_type="video",
                            media_url=media["video"],
                        ))
                        await asyncio.sleep(2.0)

                    db_media.add(MediaSent(phone=phone, product_key=product_key))
                    db_media.commit()
            except Exception as exc:
                logger.error("[MÍDIA] Erro ao controlar mídia: %s", exc)
            finally:
                db_media.close()

    except Exception as exc:
        logger.error("Erro no processamento para %s: %s", phone, exc, exc_info=True)
    finally:
        db.close()


@router.post("/finance/trigger")
async def trigger_finance_collection(background_tasks: BackgroundTasks):
    logger.info("Disparo manual da régua de cobrança solicitado.")
    background_tasks.add_task(finance_service.run_daily_collection)
    return {"status": "Processamento da régua de cobrança iniciado em segundo plano."}


@router.post("/satisfacao/trigger")
async def trigger_satisfacao(horas: int = 3):
    logger.info("[SATISFACAO] Disparo manual solicitado (janela=%sh).", horas)
    try:
        await _tick_satisfacao(horas_janela=horas)
        return {"status": "ok", "mensagem": f"Verificação executada (janela={horas}h). Confira os logs do Render pra detalhes."}
    except Exception as exc:
        logger.error("[SATISFACAO] Erro no disparo manual: %s", exc)
        return {"status": "erro", "erro": str(exc)}
