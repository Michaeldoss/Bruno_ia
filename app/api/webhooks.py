from fastapi import APIRouter, Form, Request, BackgroundTasks, Response
from twilio.twiml.messaging_response import MessagingResponse
from sqlalchemy.exc import IntegrityError

from app.services.twilio_client import twilio_service
from app.services.openai_client import process_message_with_assistant, create_thread, transcribe_audio, get_typing_delay
from app.services.finance_service import finance_service
from app.services.buffer_service import message_buffer
from app.core.media_catalog import MEDIA_CATALOG
from app.services.followup_service import resetar_followup
from app.services.satisfacao_service import verificar_resposta_satisfacao, _tick as _tick_satisfacao
from app.services.crm_inbox_client import log_message as log_message_to_crm, human_active_recently
from app.models.database import (
    SessionLocal,
    Lead,
    MediaSent,
    Conversation,
    LeadState,
    InboundWebhookEvent,
)

import logging
import asyncio
import re
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter()


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", str(raw or "").replace("whatsapp:", ""))
    if not digits.startswith("55") and len(digits) in (10, 11):
        digits = "55" + digits
    return digits


def _first(form: dict, *keys: str) -> Optional[str]:
    for key in keys:
        value = form.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()[:1000]
    return None


def _extract_attribution(form: dict, request: Request) -> dict:
    """Extrai campos conhecidos de Twilio, formulários e UTMs.

    Twilio não cria UTMs sozinho. Quando o anúncio/landing page repassa esses
    campos ao webhook, eles são preservados aqui até a entrega ao CRM.
    """
    referral_body = _first(form, "ReferralBody", "ReferralHeadline")
    channel = _first(form, "canal", "channel", "origem", "source", "utm_source")
    if not channel and referral_body:
        channel = "Meta Ads"
    if not channel:
        channel = "WhatsApp Direto"

    return {
        "origin_channel": channel,
        "campaign_name": _first(form, "campanha", "campaign", "campaign_name", "utm_campaign"),
        "adset_name": _first(form, "conjunto_anuncios", "adset", "adset_name"),
        "ad_name": _first(form, "anuncio", "ad", "ad_name", "ReferralHeadline"),
        "form_name": _first(form, "formulario", "form", "form_name"),
        "utm_source": _first(form, "utm_source"),
        "utm_medium": _first(form, "utm_medium"),
        "utm_campaign": _first(form, "utm_campaign"),
        "utm_content": _first(form, "utm_content"),
        "utm_term": _first(form, "utm_term"),
        "landing_page": _first(form, "pagina_origem", "landing_page", "ReferralSourceUrl"),
        "referrer": _first(form, "referrer", "referer") or request.headers.get("referer"),
        "twilio_from": _first(form, "From"),
        "twilio_to": _first(form, "To"),
    }


def _claim_event(message_sid: Optional[str], phone: str) -> bool:
    """Reserva o SID para um único worker.

    Eventos travados em processing por mais de 10 minutos podem ser retomados.
    """
    if not message_sid:
        return True

    db = SessionLocal()
    try:
        event = InboundWebhookEvent(
            message_sid=message_sid,
            phone=phone,
            status="processing",
        )
        db.add(event)
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        existing = db.query(InboundWebhookEvent).filter(
            InboundWebhookEvent.message_sid == message_sid
        ).first()
        if not existing:
            return False
        if existing.status == "failed":
            existing.status = "processing"
            existing.last_error = None
            db.commit()
            return True
        if existing.status == "processing" and existing.received_at:
            if existing.received_at < datetime.utcnow() - timedelta(minutes=10):
                existing.received_at = datetime.utcnow()
                existing.last_error = "recovered_stale_processing"
                db.commit()
                return True
        return False
    except Exception as exc:
        db.rollback()
        logger.error("Falha ao registrar idempotencia do SID %s: %s", message_sid, exc)
        # Falha aberta: não perde o atendimento por indisponibilidade do banco.
        return True
    finally:
        db.close()


def _mark_events(message_sids: list, status: str, error: Optional[str] = None):
    sids = [sid for sid in (message_sids or []) if sid]
    if not sids:
        return
    db = SessionLocal()
    try:
        rows = db.query(InboundWebhookEvent).filter(
            InboundWebhookEvent.message_sid.in_(sids)
        ).all()
        for row in rows:
            row.status = status
            row.last_error = (error or "")[:2000] or None
            if status == "processed":
                row.processed_at = datetime.utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Falha ao atualizar status dos SIDs %s: %s", sids, exc)
    finally:
        db.close()


def _persist_attribution(phone: str, attribution: dict):
    db = SessionLocal()
    try:
        state = db.query(LeadState).filter(LeadState.phone == phone).first()
        if not state:
            state = LeadState(phone=phone, stage="active")
            db.add(state)
            db.flush()
        for field, value in attribution.items():
            if value and not getattr(state, field, None):
                setattr(state, field, value)
        if not state.telefone:
            state.telefone = phone
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Falha ao persistir origem do lead %s: %s", phone, exc)
    finally:
        db.close()


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
    form = dict(await request.form())
    phone = _normalize_phone(From)
    if len(phone) < 12:
        logger.warning("[WEBHOOK] Telefone invalido recebido: %s", From)
        return Response(content=str(MessagingResponse()), media_type="application/xml", status_code=400)

    if not _claim_event(MessageSid, phone):
        logger.warning("[WEBHOOK] SID duplicado/em processamento ignorado: %s", MessageSid)
        return Response(content=str(MessagingResponse()), media_type="application/xml")

    attribution = _extract_attribution(form, request)
    _persist_attribution(phone, attribution)
    context = {**attribution, "message_sids": [MessageSid] if MessageSid else []}

    logger.info("[WEBHOOK] Recebido phone=%s sid=%s media=%s", phone, MessageSid, bool(MediaUrl0))

    if Body and await verificar_resposta_satisfacao(phone, Body):
        _mark_events(context["message_sids"], "processed")
        return Response(content=str(MessagingResponse()), media_type="application/xml")

    if MediaUrl0 and MediaContentType0 and "audio" in MediaContentType0:
        resetar_followup(phone)
        background_tasks.add_task(
            handle_async_response,
            phone,
            None,
            MediaUrl0,
            MediaContentType0,
            context,
        )
    elif Body and Body.strip():
        resetar_followup(phone)
        background_tasks.add_task(
            message_buffer.add_message,
            phone,
            Body.strip(),
            process_deferred_message,
            context,
        )
    else:
        logger.info("[WEBHOOK] Evento sem texto/audio processavel: SID %s", MessageSid)
        _mark_events(context["message_sids"], "processed")

    return Response(content=str(MessagingResponse()), media_type="application/xml")


async def process_deferred_message(phone: str, combined_message: str, context: Optional[dict] = None):
    logger.info("[WEBHOOK] Buffer liberado para %s", phone)
    await handle_async_response(phone, combined_message, None, None, context)


async def handle_async_response(
    phone: str,
    user_message: Optional[str],
    audio_url: Optional[str],
    content_type: Optional[str],
    context: Optional[dict] = None,
):
    message_sids = (context or {}).get("message_sids", [])
    db = SessionLocal()
    completed = False
    try:
        lead_state = db.query(LeadState).filter(LeadState.phone == phone).first()
        if lead_state and lead_state.stage == "closed":
            logger.info("[HANDOFF] Lead %s já entregue; Bruno apenas espelha", phone)
            if user_message:
                await log_message_to_crm(phone, user_message, is_from_contact=True)
            elif audio_url and content_type and "audio" in content_type:
                transcription = await transcribe_audio(audio_url)
                if transcription:
                    await log_message_to_crm(phone, f"[ÁUDIO] {transcription}", is_from_contact=True)
            completed = True
            return

        if await human_active_recently(phone):
            logger.info("[HANDOFF] Humano ativo para %s; Bruno permanece silencioso", phone)
            if user_message:
                await log_message_to_crm(phone, user_message, is_from_contact=True)
            elif audio_url and content_type and "audio" in content_type:
                transcription = await transcribe_audio(audio_url)
                if transcription:
                    await log_message_to_crm(phone, f"[ÁUDIO] {transcription}", is_from_contact=True)
            completed = True
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
                completed = True
                return

        if not user_message:
            logger.warning("[WEBHOOK] Mensagem vazia para %s", phone)
            completed = True
            return

        # O espelhamento de entrada é aguardado. Se o CRM estiver temporariamente
        # indisponível, a exceção mantém o SID como failed para reprocessamento.
        await log_message_to_crm(phone, user_message, is_from_contact=True)

        response_chunks = await process_message_with_assistant(thread_id, user_message)
        if not response_chunks:
            raise RuntimeError("IA retornou resposta vazia")

        first_message = True
        for chunk in response_chunks:
            if not first_message:
                await asyncio.sleep(3.0)
            await asyncio.sleep(get_typing_delay(chunk))
            await twilio_service.send_whatsapp_message(phone, chunk)
            await log_message_to_crm(phone, chunk, is_from_contact=False)
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

                    if media.get("image"):
                        await asyncio.sleep(2.0)
                        await twilio_service.send_whatsapp_message(phone, media_url=media["image"])
                        await log_message_to_crm(
                            phone,
                            f"[imagem] {product_key}",
                            is_from_contact=False,
                            msg_type="image",
                            media_url=media["image"],
                        )
                    if media.get("video"):
                        await asyncio.sleep(2.0)
                        await twilio_service.send_whatsapp_message(phone, media_url=media["video"])
                        await log_message_to_crm(
                            phone,
                            f"[video] {product_key}",
                            is_from_contact=False,
                            msg_type="video",
                            media_url=media["video"],
                        )

                    db_media.add(MediaSent(phone=phone, product_key=product_key))
                    db_media.commit()
            finally:
                db_media.close()

        completed = True

    except Exception as exc:
        logger.error("Erro no processamento para %s: %s", phone, exc, exc_info=True)
        _mark_events(message_sids, "failed", str(exc))
    finally:
        if completed:
            _mark_events(message_sids, "processed")
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
        return {"status": "ok", "mensagem": f"Verificação executada (janela={horas}h)."}
    except Exception as exc:
        logger.error("[SATISFACAO] Erro no disparo manual: %s", exc, exc_info=True)
        return {"status": "erro", "erro": str(exc)}
