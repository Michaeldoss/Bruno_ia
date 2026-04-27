"""
Diagnóstico de vídeos — verifica tamanho real baixando os primeiros bytes
Rode com: python testar_videos.py
"""
import requests
from dotenv import load_dotenv
import os, sys
load_dotenv()
sys.path.insert(0, '.')

_C = "q_auto,vc_h264,br_800k"

VIDEOS = {
    "1801":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776688349/V%C3%ADdeo_1801_d1fhdm.mp4",
    "1802":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776686175/1802_kbalek.mp4",
    "1902":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776686438/1902_2_dvxaqp.mp4",
    "1904":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776461517/DG1904i_V2_mrt95y.mp4",
    "1908":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776461533/Video_Michael_Maquina_1_ieduwg.mp4",
    "3202":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776461540/Em_busca_de_aumentar_a_produ%C3%A7%C3%A3o_de_forma_eficiente_e_r%C3%A1pida_sem_comprometer_a_qualidade_Plot_dlsllu.mp4",
    "3204":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776686633/3204_g4yf2k.mp4",
    "3003":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776685842/IMG_1993_lz5ohr.mov",
    "DTF30":     f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776689977/3002_1_mwm4ph.mp4",
    "DTF60":     f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776461555/NOVIDADE_NA_%C3%81REA_Apresentamos_a_AJ-6002_a_impressora_DTF_que_vai_revolucionar_suas_estampas_vns0hn.mp4",
    "Jinka1351": f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776685835/Jinka_ABJ_1351_y6dj9z.mov",
}

LIMITE_MB = 16

print(f"\n{'='*65}")
print(f"  DIAGNÓSTICO DE VÍDEOS (com download parcial)")
print(f"  Limite Twilio WhatsApp: {LIMITE_MB}MB")
print(f"{'='*65}")
print(f"{'Produto':<12} {'Tipo':<20} {'Tamanho':>10}  Status")
print(f"{'-'*65}")

for nome, url in VIDEOS.items():
    try:
        # Baixa os primeiros 512KB para detectar o tipo e estimar tamanho
        r = requests.get(url, stream=True, timeout=20)
        content_type = r.headers.get("Content-Type", "?")
        content_length = r.headers.get("Content-Length", "0")

        # Baixa até 1MB para verificar que a URL funciona
        downloaded = 0
        for chunk in r.iter_content(chunk_size=65536):
            downloaded += len(chunk)
            if downloaded >= 1024 * 1024:  # 1MB
                break
        r.close()

        size_mb = int(content_length) / (1024*1024) if content_length != "0" else None
        tipo = content_type.split(";")[0].replace("video/", "")

        if downloaded < 1000:
            status = "❌ URL não acessível"
        elif "quicktime" in content_type or ".mov" in url.lower():
            status = "⚠️  MOV — converter para mp4"
        elif size_mb and size_mb > LIMITE_MB:
            status = f"❌ {size_mb:.1f}MB — grande demais"
        else:
            tamanho_str = f"{size_mb:.1f}MB" if size_mb else "streaming"
            status = f"✅ OK ({tamanho_str})"

        tam_str = f"{size_mb:.1f}MB" if size_mb else "streaming"
        print(f"  {nome:<12} {tipo:<20} {tam_str:>10}  {status}")

    except Exception as e:
        print(f"  {nome:<12} {'erro':<20} {'?':>10}  ❌ {e}")

# Envia teste real para WhatsApp
print(f"\n{'='*65}")
print("  TESTE DE ENVIO REAL — DTF60 (menor vídeo)")
print(f"{'='*65}")

try:
    from twilio.rest import Client
    sid   = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    fone  = os.getenv("TWILIO_PHONE_NUMBER", "").strip()
    if not fone.startswith("+"): fone = "+" + fone

    client = Client(sid, token)
    msg = client.messages.create(
        from_=f"whatsapp:{fone}",
        to="whatsapp:+5547992307367",
        body="",
        media_url=[VIDEOS["DTF60"]]
    )
    print(f"  ✅ Enviado! SID: {msg.sid}")
    print(f"  Verifique o WhatsApp — o vídeo deve chegar em segundos.")
except Exception as e:
    print(f"  ❌ Erro no envio: {e}")

print(f"{'='*65}\n")


_C = "q_auto,vc_h264,br_800k"

VIDEOS = {
    "1801":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776688349/V%C3%ADdeo_1801_d1fhdm.mp4",
    "1802":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776686175/1802_kbalek.mp4",
    "1902":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776686438/1902_2_dvxaqp.mp4",
    "1904":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776461517/DG1904i_V2_mrt95y.mp4",
    "1908":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776461533/Video_Michael_Maquina_1_ieduwg.mp4",
    "3202":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776461540/Em_busca_de_aumentar_a_produ%C3%A7%C3%A3o_de_forma_eficiente_e_r%C3%A1pida_sem_comprometer_a_qualidade_Plot_dlsllu.mp4",
    "3204":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776686633/3204_g4yf2k.mp4",
    "3003":      f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776685842/IMG_1993_lz5ohr.mov",
    "DTF30":     f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776689977/3002_1_mwm4ph.mp4",
    "DTF60":     f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776461555/NOVIDADE_NA_%C3%81REA_Apresentamos_a_AJ-6002_a_impressora_DTF_que_vai_revolucionar_suas_estampas_vns0hn.mp4",
    "Jinka1351": f"https://res.cloudinary.com/dbuwtsmsg/video/upload/{_C}/v1776685835/Jinka_ABJ_1351_y6dj9z.mov",
}

LIMITE_TWILIO_MB = 16

print(f"\n{'='*65}")
print(f"  DIAGNÓSTICO DE VÍDEOS — LIMITE TWILIO: {LIMITE_TWILIO_MB}MB")
print(f"{'='*65}")
print(f"{'Produto':<12} {'Tipo':<15} {'Tamanho':>10}  {'Status'}")
print(f"{'-'*65}")

problemas = []

for nome, url in VIDEOS.items():
    try:
        r = requests.head(url, timeout=10, allow_redirects=True)
        content_type = r.headers.get("Content-Type", "desconhecido")
        content_length = int(r.headers.get("Content-Length", 0))
        size_mb = content_length / (1024 * 1024)

        if size_mb > LIMITE_TWILIO_MB:
            status = f"❌ GRANDE DEMAIS ({size_mb:.1f}MB)"
            problemas.append((nome, size_mb, "tamanho"))
        elif ".mov" in url.lower() or "quicktime" in content_type.lower():
            status = f"⚠️  MOV — pode falhar ({size_mb:.1f}MB)"
            problemas.append((nome, size_mb, "formato_mov"))
        elif size_mb == 0:
            status = "⚠️  Tamanho desconhecido"
        else:
            status = f"✅ OK ({size_mb:.1f}MB)"

        tipo_curto = content_type.split(";")[0].replace("video/", "")
        print(f"  {nome:<12} {tipo_curto:<15} {size_mb:>8.1f}MB  {status}")

    except Exception as e:
        print(f"  {nome:<12} {'erro':<15} {'?':>10}  ❌ {e}")
        problemas.append((nome, 0, "erro"))

print(f"\n{'='*65}")
if problemas:
    print(f"\n  PROBLEMAS ENCONTRADOS:")
    for nome, mb, tipo in problemas:
        if tipo == "tamanho":
            print(f"  ❌ {nome}: {mb:.1f}MB — acima do limite de {LIMITE_TWILIO_MB}MB")
            print(f"     Solução: comprimir no Cloudinary adicionando na URL:")
            print(f"     /q_auto,vc_h264,br_1m/ antes do nome do arquivo")
        elif tipo == "formato_mov":
            print(f"  ⚠️  {nome}: formato .mov — WhatsApp não suporta")
            print(f"     Solução: converter para .mp4 no Cloudinary")
        elif tipo == "erro":
            print(f"  ❌ {nome}: URL inacessível")
else:
    print(f"\n  ✅ Todos os vídeos estão OK!")
print(f"{'='*65}\n")
