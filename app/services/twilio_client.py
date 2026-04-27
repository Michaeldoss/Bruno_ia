import logging
from twilio.rest import Client
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

class TwilioClient:
    def __init__(self):
        # Allow stub bypass for local dev without keys
        if settings.TWILIO_ACCOUNT_SID != "stub" and settings.TWILIO_AUTH_TOKEN != "stub":
            self.client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        else:
            self.client = None

    async def send_whatsapp_message(self, to: str, body: str = "", media_url: str = None):
        if not self.client:
            logger.info(f"[STUB] enviando msg WhatsApp para {to}: {body} (Media: {media_url})")
            return

        try:
            # Padrão oficial exigido pelo Meta WhatsApp (+E.164)
            from_phone = f"whatsapp:{settings.TWILIO_PHONE_NUMBER}"
            if not from_phone.startswith("whatsapp:+"):
                from_phone = from_phone.replace("whatsapp:", "whatsapp:+")

            to_phone = f"whatsapp:{to}"
            if not to_phone.startswith("whatsapp:+"):
                to_phone = to_phone.replace("whatsapp:", "whatsapp:+")

            logger.error(f">>> DEBUG TWILIO: Enviando de [{from_phone}] para [{to_phone}]")

            params = {
                "from_": from_phone,
                "body": body,
                "to": to_phone
            }
            if media_url:
                params["media_url"] = [media_url]

            import asyncio
            message = await asyncio.to_thread(self.client.messages.create, **params)
            logger.info(f"Mensagem enviada com sucesso: SID {message.sid}")
            return message.sid
        except Exception as e:
            logger.error(f"Erro ao enviar mensagem via Twilio: {e}")

twilio_service = TwilioClient()
