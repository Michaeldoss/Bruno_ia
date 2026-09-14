import re
from html import escape
from urllib.parse import parse_qs

from app.main import app as base_app
from app.services.personal_assistant import handle_personal_text, is_personal_phone


class MichaelPersonalMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != "/webhooks/twils":
            await self.app(scope, receive, send)
            return

        body = b""
        more = True
        while more:
            message = await receive()
            if message.get("type") != "http.request":
                continue
            body += message.get("body", b"")
            more = bool(message.get("more_body"))

        form = parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
        from_value = (form.get("From") or [""])[0]
        phone = from_value.replace("whatsapp:", "")
        if phone and not phone.startswith("+"):
            phone = "+" + re.sub(r"[^\d]", "", phone)

        if is_personal_phone(phone):
            text = (form.get("Body") or [""])[0]
            sid = (form.get("MessageSid") or [""])[0] or None
            media_count = int((form.get("NumMedia") or ["0"])[0] or 0)
            if not text and media_count:
                answer = "Recebi a mídia. Por enquanto, no modo pessoal, me manda o pedido em texto."
            else:
                answer = await handle_personal_text(phone, text, external_message_id=sid, media_type="text")

            xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escape(answer)}</Message></Response>'.encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/xml; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                    (b"content-length", str(len(xml)).encode("ascii")),
                ],
            })
            await send({"type": "http.response.body", "body": xml, "more_body": False})
            return

        sent = False

        async def replay_receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)


base_app.add_middleware(MichaelPersonalMiddleware)
app = base_app
