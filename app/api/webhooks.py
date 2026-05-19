from fastapi import APIRouter, Form, Request, BackgroundTasks, Response
from twilio.twiml.messaging_response import MessagingResponse
from app.services.twilio_client import twilio_service
from app.services.openai_client import process_message_with_assistant, create_thread, transcribe_audio, get_typing_delay
from app.services.finance_service import finance_service
from app.services.buffer_service import message_buffer
from app.core.media_catalog import find_media_key_for_message
from app.services.followup_service import resetar_followup
from app.models.database import SessionLocal, Lead
import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter()

# Rastreia mídia já enviada por telefone — evita repetição na mesma conversa
# { "5547999999999": {"1802", "dtf30"} }
_media_sent: dict[str, set] = {}

# ---------------------------------------------------------------------------
# Proteção contra reentrega duplicada do Twilio
# ---------------------------------------------------------------------------
_processed_sids: set = set()

def _is_duplicate(message_sid: str) -> bool:
    if message_sid in _processed_sids:
        return True
    _processed_sids.add(message_sid)
    if len(_processed_sids) > 1000:
        _processed_sids.clear()
    return False


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
        resp = MessagingResponse()
        return Response(content=str(resp), media_type="application/xml")

    phone = From.replace("whatsapp:", "")
    print(f"\n>>> RECEBIDO DE: {From} | SID: {MessageSid}")

    if MediaUrl0 and MediaContentType0 and "audio" in MediaContentType0:
        resetar_followup(phone)
        background_tasks.add_task(handle_async_response, phone, None, MediaUrl0, MediaContentType0)
    elif Body:
        resetar_followup(phone)
        background_tasks.add_task(message_buffer.add_message, phone, Body, process_deferred_message)

    resp = MessagingResponse()
    return Response(content=str(resp), media_type="application/xml")


async def process_deferred_message(phone: str, combined_message: str):
    logger.info(f"[WEBHOOK] Buffer liberado para {phone}. Processando: {combined_message}")
    await handle_async_response(phone, combined_message, None, None)


async def handle_async_response(
    phone: str,
    user_message: Optional[str],
    audio_url: Optional[str],
    content_type: Optional[str]
):
    print(f"\n>>> INICIANDO PROCESSAMENTO PARA: {phone}")
    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.phone == phone).first()
        if not lead:
            thread_id = await create_thread()
            lead = Lead(phone=phone, thread_id=thread_id)
            db.add(lead)
            db.commit()
            db.refresh(lead)

        thread_id = lead.thread_id

        if audio_url and content_type and "audio" in content_type:
            logger.info(f"Processando áudio de {phone}...")
            transcription = await transcribe_audio(audio_url)
            if transcription:
                user_message = f"[ÁUDIO] {transcription}"
            else:
                await twilio_service.send_whatsapp_message(phone, "Desculpe, não consegui ouvir seu áudio. Pode repetir?")
                return

        if not user_message:
            logger.warning(f"[WEBHOOK] Mensagem vazia para {phone}. Abortando.")
            return

        logger.info(f"[WEBHOOK] Consultando IA para {phone}...")
        response_chunks = await process_message_with_assistant(thread_id, user_message)
        logger.info(f"[WEBHOOK] IA retornou {len(response_chunks)} chunks para {phone}.")

        first_message = True
        for chunk in response_chunks:
            if not first_message:
                await asyncio.sleep(3.0)
            delay = get_typing_delay(chunk)
            await asyncio.sleep(delay)
            await twilio_service.send_whatsapp_message(phone, chunk)
            first_message = False

        # ── Envio de mídia ────────────────────────────────────────────────
        # Detecta produto na mensagem do CLIENTE ou na RESPOSTA DO BRUNO
        # Nunca envia o mesmo produto duas vezes na mesma conversa
        texto_combinado = (user_message or "") + " " + " ".join(response_chunks)
        resultado_midia = find_media_key_for_message(texto_combinado)

        if resultado_midia:
            product_key, media = resultado_midia
            ja_enviados = _media_sent.get(phone, set())

            if product_key not in ja_enviados:
                logger.info(f"[MÍDIA] Enviando '{product_key}' para {phone}")
                await asyncio.sleep(2.0)

                if media.get("image"):
                    await twilio_service.send_whatsapp_message(phone, media_url=media["image"])
                    await asyncio.sleep(2.0)

                if media.get("video"):
                    await twilio_service.send_whatsapp_message(phone, media_url=media["video"])

                ja_enviados.add(product_key)
                _media_sent[phone] = ja_enviados

                if len(_media_sent) > 500:
                    _media_sent.clear()
            else:
                logger.info(f"[MÍDIA] '{product_key}' já enviado para {phone}. Ignorando.")

    except Exception as e:
        logger.error(f"Erro no processamento para {phone}: {e}", exc_info=True)
    finally:
        db.close()


@router.post("/finance/trigger")
async def trigger_finance_collection(background_tasks: BackgroundTasks):
    logger.info("Disparo manual da régua de cobrança solicitado.")
    background_tasks.add_task(finance_service.run_daily_collection)
    return {"status": "Processamento da régua de cobrança iniciado em segundo plano."}
