from app.core.media_catalog import find_media_for_message, find_media_key_for_message
from fastapi import APIRouter, Form, Request, BackgroundTasks, Response
from twilio.twiml.messaging_response import MessagingResponse
from app.services.twilio_client import twilio_service
from app.services.openai_client import process_message_with_assistant, create_thread, transcribe_audio, get_typing_delay
from app.services.finance_service import finance_service
from app.services.buffer_service import message_buffer
from app.core.media_catalog import find_media_for_message
from app.services.followup_service import resetar_followup
from app.models.database import SessionLocal, Lead
import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Proteção contra reentrega duplicada do Twilio
# ---------------------------------------------------------------------------
_processed_sids: set = set()

def _is_duplicate(message_sid: str) -> bool:
    """Retorna True se este SID já foi processado. Limpa o set se ficar grande."""
    if message_sid in _processed_sids:
        return True
    _processed_sids.add(message_sid)
    # Limpa quando passar de 1000 entradas para não vazar memória
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
    """
    Webhook principal. Responde imediatamente ao Twilio e processa em background.
    """
    # FIX: proteção contra reentrega duplicada do Twilio
    if MessageSid and _is_duplicate(MessageSid):
        logger.warning(f"[WEBHOOK] Mensagem duplicada ignorada: {MessageSid}")
        resp = MessagingResponse()
        return Response(content=str(resp), media_type="application/xml")

    phone = From.replace("whatsapp:", "")
    print(f"\n>>> RECEBIDO DE: {From} | SID: {MessageSid}")

    # Se for áudio, processa imediatamente (não passa pelo buffer de texto)
    if MediaUrl0 and MediaContentType0 and "audio" in MediaContentType0:
        resetar_followup(phone)
        background_tasks.add_task(
            handle_async_response,
            phone,
            None,
            MediaUrl0,
            MediaContentType0
        )
    # Se for texto, envia para o buffer de agrupamento (debounce)
    elif Body:
        resetar_followup(phone)
        background_tasks.add_task(
            message_buffer.add_message,
            phone,
            Body,
            process_deferred_message
        )

    resp = MessagingResponse()
    return Response(content=str(resp), media_type="application/xml")


async def process_deferred_message(phone: str, combined_message: str):
    """
    Callback chamado pelo buffer_service.
    Lança o processamento normal da IA com o texto já agrupado.
    """
    logger.info(f"[WEBHOOK] Buffer liberado para {phone}. Processando: {combined_message}")
    await handle_async_response(phone, combined_message, None, None)


async def handle_async_response(
    phone: str,
    user_message: Optional[str],
    audio_url: Optional[str],
    content_type: Optional[str]
):
    """
    Orquestra o processamento em segundo plano:
    Transcrição -> IA -> Delays -> Envio de texto -> Envio de mídia (foto + vídeo)
    """
    print(f"\n>>> INICIANDO PROCESSAMENTO PARA: {phone}")
    db = SessionLocal()
    try:
        # 1. Busca ou registra Lead
        lead = db.query(Lead).filter(Lead.phone == phone).first()
        if not lead:
            thread_id = await create_thread()
            lead = Lead(phone=phone, thread_id=thread_id)
            db.add(lead)
            db.commit()
            db.refresh(lead)

        thread_id = lead.thread_id

        # 2. Tratamento de Áudio
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

        # 3. Chama o cérebro (Claude)
        logger.info(f"[WEBHOOK] Consultando IA para {phone}...")
        response_chunks = await process_message_with_assistant(thread_id, user_message)
        logger.info(f"[WEBHOOK] IA retornou {len(response_chunks)} chunks para {phone}.")

        # 4. Envio com comportamento humano (delay de digitação)
        first_message = True
        for chunk in response_chunks:
            if not first_message:
                await asyncio.sleep(3.0)

            delay = get_typing_delay(chunk)
            logger.info(f"Simulando digitação por {delay:.1f}s...")
            await asyncio.sleep(delay)

            await twilio_service.send_whatsapp_message(phone, chunk)
            first_message = False

        # 5. Envio de mídia real (foto + vídeo) se a mensagem mencionar um produto
        media = find_media_for_message(user_message)
        if media:
            logger.info(f"[MÍDIA] Produto detectado em mensagem de {phone}. Enviando foto e vídeo.")
            await asyncio.sleep(2.0)

            if media.get("image"):
                logger.info(f"[MÍDIA] Enviando imagem para {phone}...")
                await twilio_service.send_whatsapp_message(phone, media_url=media["image"])
                await asyncio.sleep(2.0)

            if media.get("video"):
                logger.info(f"[MÍDIA] Enviando vídeo para {phone}...")
                await twilio_service.send_whatsapp_message(phone, media_url=media["video"])

    except Exception as e:
        logger.error(f"Erro no processamento para {phone}: {e}", exc_info=True)
    finally:
        db.close()


@router.post("/finance/trigger")
async def trigger_finance_collection(background_tasks: BackgroundTasks):
    """Endpoint manual para disparar a régua de cobrança do dia."""
    logger.info("Disparo manual da régua de cobrança solicitado.")
    background_tasks.add_task(finance_service.run_daily_collection)
    return {"status": "Processamento da régua de cobrança iniciado em segundo plano."}
