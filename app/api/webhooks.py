from fastapi import APIRouter, Form, Request, BackgroundTasks, Response
from twilio.twiml.messaging_response import MessagingResponse
from app.services.twilio_client import twilio_service
from app.services.openai_client import process_message_with_assistant, create_thread, transcribe_audio, get_typing_delay
from app.services.finance_service import finance_service
from app.services.buffer_service import message_buffer
from app.core.media_catalog import find_media_key_for_message, MEDIA_CATALOG
from app.services.followup_service import resetar_followup
from app.services.satisfacao_service import verificar_resposta_satisfacao, _tick as _tick_satisfacao
from app.services.crm_inbox_client import log_message as log_message_to_crm
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

    # ── Pesquisa de satisfação: intercepta nota 0-5 antes de qualquer
    # outra coisa. Se for resposta válida de uma pesquisa pendente,
    # encerra aqui — não reseta follow-up, não vai pro Bruno/IA, não
    # aparece na conversa normal (fica só no dashboard de satisfação).
    if Body and await verificar_resposta_satisfacao(phone, Body):
        resp = MessagingResponse()
        return Response(content=str(resp), media_type="application/xml")

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
        # ── Trava de handoff ────────────────────────────────────────────
        # Uma vez que o lead foi entregue (pipeline ou vendedor -- stage
        # "closed", setado no fechamento forte em openai_client.py), o
        # Bruno NUNCA MAIS fala com esse numero: nao responde, nao manda
        # midia, nao reabre followup. A partir daqui quem atende e humano,
        # pelo numero do agente -- o Bruno so continua espelhando a
        # mensagem recebida pro Inbox do CRM (pra o agente ver o que o
        # cliente mandou), e para por ai.
        lead_state = db.query(LeadState).filter(LeadState.phone == phone).first()
        if lead_state and lead_state.stage == "closed":
            logger.info(f"[HANDOFF] Lead de {phone} ja foi entregue -- Bruno nao responde mais. So espelhando pro CRM.")
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

        # Espelha a mensagem do cliente pro Inbox do CRM em tempo real.
        # asyncio.create_task (nao await direto) pra nao atrasar a
        # resposta do Bruno esperando o CRM responder.
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
        # 1. Busca na mensagem atual (cliente + resposta Bruno)
        texto_combinado = (user_message or "") + " " + " ".join(response_chunks)
        resultados = find_all_media_for_text(texto_combinado)

        # 2. Se não achou nada, busca no histórico recente das últimas 10 mensagens
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

        # 3. Envia mídia de cada produto encontrado — apenas os ainda não enviados
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
    """
    Roda manualmente uma verificação de OS finalizadas + envio de pesquisa,
    sem esperar o loop de 10 min. Uso: teste manual.

    Parâmetro opcional ?horas=N alarga a janela de "finalizada recentemente"
    pra testes (ex: ?horas=72 pega qualquer OS finalizada nos últimos 3 dias).
    Em produção o loop automático sempre usa o padrão (3h).

    Roda de forma síncrona (não em background) pra você ver o resultado
    direto na resposta, incluindo qualquer erro.
    """
    logger.info(f"[SATISFACAO] Disparo manual solicitado (janela={horas}h).")
    try:
        await _tick_satisfacao(horas_janela=horas)
        return {"status": "ok", "mensagem": f"Verificação executada (janela={horas}h). Confira os logs do Render pra detalhes."}
    except Exception as e:
        logger.error(f"[SATISFACAO] Erro no disparo manual: {e}")
        return {"status": "erro", "erro": str(e)}
