"""
Teste de envio de mídia (foto + vídeo) - DG 1801
Rode com: python teste_midia.py
"""

import os
from dotenv import load_dotenv
from twilio.rest import Client

# Carrega o .env do projeto
load_dotenv()

# ── Configurações ──────────────────────────────────────────────────────────
ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN")
FROM_PHONE   = os.getenv("TWILIO_PHONE_NUMBER")  # ex: +14155238886
TO_PHONE     = "+554792307367"

# ── Mídias da DG 1801 ──────────────────────────────────────────────────────
IMAGE_URL = "https://res.cloudinary.com/dbuwtsmsg/image/upload/v1776436318/1801_x8qpbr.jpg"
VIDEO_URL = "https://res.cloudinary.com/dbuwtsmsg/video/upload/v1776688349/V%C3%ADdeo_1801_d1fhdm.mp4"

# ── Validação ──────────────────────────────────────────────────────────────
if not ACCOUNT_SID or ACCOUNT_SID == "stub":
    print("ERRO: TWILIO_ACCOUNT_SID não encontrado no .env")
    exit(1)
if not AUTH_TOKEN or AUTH_TOKEN == "stub":
    print("ERRO: TWILIO_AUTH_TOKEN não encontrado no .env")
    exit(1)
if not FROM_PHONE or FROM_PHONE == "stub":
    print("ERRO: TWILIO_PHONE_NUMBER não encontrado no .env")
    exit(1)

from_wa = f"whatsapp:{FROM_PHONE}" if not FROM_PHONE.startswith("whatsapp:") else FROM_PHONE
to_wa   = f"whatsapp:{TO_PHONE}"

client = Client(ACCOUNT_SID, AUTH_TOKEN)

print(f"\n{'='*50}")
print(f"  TESTE DE MÍDIA - DG 1801")
print(f"{'='*50}")
print(f"  De:   {from_wa}")
print(f"  Para: {to_wa}")
print(f"{'='*50}\n")

# ── Envia imagem ───────────────────────────────────────────────────────────
print("[ 1/2 ] Enviando IMAGEM...")
try:
    msg = client.messages.create(
        from_=from_wa,
        to=to_wa,
        body="",
        media_url=[IMAGE_URL]
    )
    print(f"        OK  SID: {msg.sid}")
except Exception as e:
    print(f"        ERRO: {e}")

# ── Envia vídeo ────────────────────────────────────────────────────────────
import time
print("\n[ 2/2 ] Enviando VÍDEO (aguarde 3s)...")
time.sleep(3)
try:
    msg = client.messages.create(
        from_=from_wa,
        to=to_wa,
        body="",
        media_url=[VIDEO_URL]
    )
    print(f"        OK  SID: {msg.sid}")
except Exception as e:
    print(f"        ERRO: {e}")

print(f"\n{'='*50}")
print("  Teste concluído. Verifique o WhatsApp.")
print(f"{'='*50}\n")
