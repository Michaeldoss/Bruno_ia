from fastapi import APIRouter, Form, Request, BackgroundTasks, Response
from twilio.twiml.messaging_response import MessagingResponse
from app.services.twilio_client import twilio_service
from app.services.openai_client import process_message_with_assistant, create_thread, transcribe_audio, get_typing_delay
from app.services.finance_service import finance_service
from app.services.buffer_service import message_buffer
from app.core.media_catalog import find_media_key_for_message, MEDIA_CATALOG
from app.services.followup_service import resetar_followup
from app.services.satisfacao_service import verificar_resposta_satisfacao, _tick as _tick_satisfacao
from app.services.crm_inbox_client import log_message as log_message_to_crm, human_active_recently
from app.models.database import SessionLocal, Lead, MediaSent, Conversation, LeadState
import logging
import asyncio
import re
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter()

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


def _crm_media_type(content_type: Optional[str]) -> str:
    """Converte o MIME recebido do Twilio para o tipo usado pelo CRM."""
    mime = (content_type or "").lower().split(";", 1)[0].strip()
    if mime.startswith("audio/"):
        return "audio"
    if mime == "image/gif":
        return "gif"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if "sticker" in mime or mime in {"image/webp"}:
        return "sticker"
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
    label = labels.get(msg_type, "[MÍDIA]")
    return f"{label} {caption}".strip()


async def _mirror_inbound_media(
    phone: str,
    body: Optional[str],
    media_items: list[tuple[str, str]],
    message_sid: Optional[str],
) -> None:
    """Espelha toda mídia recebida no Inbox, sem depender da resposta da IA.

    O Twilio pode enviar mais de um MediaUrlN na mesma mensagem. Cada arquivo
    vira uma linha própria no CRM para preservar URL e tipo corretamente.
    """
    for index, (media_url, content_type) in enumerate(media_items):
        msg_type = _crm_media_type(content_type)
        content = _crm_media_label(msg_type, body if index == 0 else None)
        whatsapp_id = f"{message_sid}:{index}" if message_sid else None
        await log_message_to_crm(
            phone,
            content,
            is_from_contact=True,
            msg_type=msg_type,
            media_url=media_url,
            whatsapp_id=whatsapp_id,
        )


def find_all_media_for_text(text: str) -> list:
    """
    Retorna TODOS os produtos encontrados no texto, sem parar no primeiro.
    Retorna lista de (product_key, media_dict).
    """
    if not text:
        return []
    text_lower = text.lower()
    sorted_keys = sorted(MEDIA_CATALOG.keys(), key=len, reverse=True)
    found = {}
    for key in sorted_keys:
        pattern = r"\b" + re.escape(key) + r"\b"
        if re.search(pattern, text_lower):
            media = MEDIA_CATALOG[key]
            # Usa a URL do vídeo como identificador único do produto
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
        resp = MessagingResponse()
        return Response(content=str(resp), media_type="application/xml")

    phone = From.replace("whatsapp:", "")
    print(f"\n>>> RECEBIDO DE: {From} | SID: {MessageSid}")

    # Lê todas as mídias MediaUrl0..N. O código anterior só considerava áudio
    # em MediaUrl0 e descartava imagem, vídeo, GIF, figurinha e documentos.
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

    # ── Pesquisa de satisfação: intercepta nota 0-5 antes de qualquer
    # outra coisa. Se for resposta válida de uma pesquisa pendente,
    # encerra aqui — não reseta follow-up, não vai pro Bruno/IA, não
    # aparece na conversa normal (fica só no dashboard de satisfação).
    if Body and not media_items and await verificar_resposta_satisfacao(phone, Body):
        resp = MessagingResponse()
        return Response(content=str(resp), media_type="application/xml")

    if media_items:
        resetar_followup(phone)

        # Todas as mídias são espelhadas imediatamente no CRM, inclusive em
        # handoff. O processamento do Bruno continua separado para não gerar
        # duplicidade na Inbox.
        background_tasks.add_task(
            _mirror_inbound_media,
            phone,
            Body,
            media_items,
            MessageSid,
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
            # Legenda da mídia ainda pode ser analisada pelo Bruno, mas não é
            # espelhada uma segunda vez como mensagem de texto.
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

    resp = MessagingResponse()
    return Response(content=str(resp), media_type="application/xml")


async def process_deferred_message(phone: str, combined_message: str):
    logger.info(f"[WEBHOOK] Buffer liberado para {phone}. Processando: {combined_message}")
    await handle_async_response(phone, combined_message, None, None)


async def handle_async_response(
    phone: str,
    user_message: Optional[str],
    audio_url: Optional[str],
    content_type: Optional[str],
    already_mirrored: bool = False,
):
    print(f"\n>>> INICIANDO PROCESSAMENTO PARA: {phone}")
    db = SessionLocal()
    try:
        # ── Trava de handoff ────────────────────────────────────────────
        lead_state = db.query(LeadState).filter(LeadState.phone == phone).first()
        if lead_state and lead_state.stage == "closed":
            logger.info(f"[HANDOFF] Lead de {phone} ja foi entregue -- Bruno nao responde mais. So espelhando pro CRM.")
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

        # Só cria uma nova mensagem textual quando a mídia ainda não foi
        # espelhada. Em áudio já espelhado, a transcrição fica para a IA e a
        # linha original de mídia permanece única no CRM.
        if not already_mirrored:
            asyncio.create_task(log_message_to_crm(phone, user_message, is_from_contact=True))

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
            asyncio.create_task(log_message_to_crm(phone, chunk, is_from_contact=False))
            first_message = False

        # ── Envio de mídia — suporte a múltiplos produtos ─────────────────
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
                        MediaSent.product_key == product_key
                    ).first()

                    if not ja_enviou:
                        logger.info(f"[MÍDIA] Enviando '{product_key}' para {phone}")
                        await asyncio.sleep(2.0)

                        if media.get("image"):
                            await twilio_service.send_whatsapp_message(phone, media_url=media["image"])
                            asyncio.create_task(log_message_to_crm(phone, f"[imagem] {product_key}", is_from_contact=False, msg_type="image", media_url=media["image"]))
                            await asyncio.sleep(2.0)

                        if media.get("video"):
                            await twilio_service.send_whatsapp_message(phone, media_url=media["video"])
                            asyncio.create_task(log_message_to_crm(phone, f"[video] {product_key}", is_from_contact=False, msg_type="video", media_url=media["video"]))
                            await asyncio.sleep(2.0)

                        db_media.add(MediaSent(phone=phone, product_key=product_key))
                        db_media.commit()
                        logger.info(f"[MÍDIA] '{product_key}' registrado no banco para {phone}")
                    else:
                        logger.info(f"[MÍDIA] '{product_key}' já enviado para {phone}. Ignorando.")
            except Exception as e:
                logger.error(f"[MÍDIA] Erro ao controlar mídia: {e}")
            finally:
                db_media.close()

    except Exception as e:
        logger.error(f"Erro no processamento para {phone}: {e}", exc_info=True)
    finally:
        db.close()


@router.post("/finance/trigger")
async def trigger_finance_collection(background_tasks: BackgroundTasks):
    logger.info("Disparo manual da régua de cobrança solicitado.")
    background_tasks.add_task(finance_service.run_daily_collection)
    return {"status": "Processamento da régua de cobrança iniciado em segundo plano."}


@router.post("/satisfacao/trigger")
async def trigger_satisfacao(horas: int = 3):
    logger.info(f"[SATISFACAO] Disparo manual solicitado (janela={horas}h).")
    try:
        await _tick_satisfacao(horas_janela=horas)
        return {"status": "ok", "mensagem": f"Verificação executada (janela={horas}h). Confira os logs do Render pra detalhes."}
    except Exception as e:
        logger.error(f"[SATISFACAO] Erro no disparo manual: {e}")
        return {"status": "erro", "erro": str(e)}