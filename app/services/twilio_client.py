import asyncio
import logging
from typing import Optional

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from app.config import get_settings
from app.services.usage_tracker import registrar_uso_twilio

settings = get_settings()
logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def _normalize_e164(raw: str) -> str:
    digits = "".join(c for c in str(raw or "") if c.isdigit())
    if not digits:
        raise ValueError("Telefone vazio ou invalido")
    if not digits.startswith("55") and len(digits) in (10, 11):
        digits = "55" + digits
    if len(digits) < 12 or len(digits) > 13:
        raise ValueError(f"Telefone fora do padrao E.164 brasileiro: {raw}")
    return "+" + digits


class TwilioClient:
    def __init__(self):
        if settings.TWILIO_ACCOUNT_SID != "stub" and settings.TWILIO_AUTH_TOKEN != "stub":
            self.client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        else:
            self.client = None

    async def _create_message_with_retry(self, *, attempts: int = 4, **params):
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self.client.messages.create, **params),
                    timeout=20.0,
                )
            except asyncio.TimeoutError as exc:
                last_error = exc
                retryable = True
                status = "timeout"
            except TwilioRestException as exc:
                last_error = exc
                status = exc.status
                retryable = exc.status in _RETRYABLE_STATUS
            except Exception as exc:
                last_error = exc
                status = getattr(exc, "status", None)
                retryable = status in _RETRYABLE_STATUS

            logger.warning(
                "Twilio falhou na tentativa %s/%s (status=%s): %s",
                attempt,
                attempts,
                status,
                last_error,
            )
            if not retryable or attempt >= attempts:
                break
            await asyncio.sleep(min(8.0, 1.5 * (2 ** (attempt - 1))))

        raise RuntimeError(f"Falha definitiva ao enviar pelo Twilio: {last_error}") from last_error

    async def send_whatsapp_message(self, to: str, body: str = "", media_url: str = None):
        if not self.client:
            logger.info("[STUB] enviando msg WhatsApp para %s: %s (Media: %s)", to, body, media_url)
            return "stub"

        from_phone = "whatsapp:" + _normalize_e164(settings.TWILIO_PHONE_NUMBER)
        to_phone = "whatsapp:" + _normalize_e164(to)

        params = {"from_": from_phone, "body": body or "", "to": to_phone}
        if media_url:
            params["media_url"] = [media_url]

        message = await self._create_message_with_retry(**params)
        logger.info("Mensagem Twilio enviada: SID %s para %s", message.sid, to_phone)
        registrar_uso_twilio(agente="bruno", quantidade_mensagens=1)
        return message.sid

    async def send_whatsapp_template_message(self, to: str, content_sid: str, content_variables: dict):
        if not self.client:
            logger.info("[STUB] enviando template WhatsApp para %s: %s vars=%s", to, content_sid, content_variables)
            return "stub"

        import json

        from_phone = "whatsapp:" + _normalize_e164(settings.TWILIO_PHONE_NUMBER)
        to_phone = "whatsapp:" + _normalize_e164(to)
        message = await self._create_message_with_retry(
            from_=from_phone,
            to=to_phone,
            content_sid=content_sid,
            content_variables=json.dumps(content_variables),
        )
        logger.info("Template Twilio enviado: SID %s para %s", message.sid, to_phone)
        registrar_uso_twilio(agente="bruno", quantidade_mensagens=1)
        return message.sid


twilio_service = TwilioClient()
