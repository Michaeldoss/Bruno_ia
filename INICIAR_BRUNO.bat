@echo off
title Bruno IA — Doss Group
color 0A

echo.
echo  ██████╗ ██████╗ ██╗   ██╗███╗   ██╗ ██████╗     ██╗ █████╗
echo  ██╔══██╗██╔══██╗██║   ██║████╗  ██║██╔═══██╗    ██║██╔══██╗
echo  ██████╔╝██████╔╝██║   ██║██╔██╗ ██║██║   ██║    ██║███████║
echo  ██╔══██╗██╔══██╗██║   ██║██║╚██╗██║██║   ██║    ██║██╔══██║
echo  ██████╔╝██║  ██║╚██████╔╝██║ ╚████║╚██████╔╝    ██║██║  ██║
echo  ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝     ╚═╝╚═╝  ╚═╝
echo.
echo  Doss Group — Sistema de Atendimento Inteligente
echo  ================================================
echo.
echo  LEMBRETE: Apos iniciar, copie a URL do ngrok
echo  e atualize no Twilio em:
echo  console.twilio.com ^> WhatsApp Senders
echo  ================================================
echo.

cd /d C:\Users\DELL\Desktop\Bruno_ia

:: Ativa o ambiente virtual
echo  [1/3] Ativando ambiente virtual...
call venv\Scripts\activate
if %errorlevel% neq 0 (
    echo  ERRO: Nao foi possivel ativar o venv.
    pause
    exit /b 1
)
echo  OK
echo.

:: Inicia o ngrok em nova janela
echo  [2/3] Iniciando ngrok...
start "ngrok — Bruno IA" cmd /k "ngrok http 8000"
timeout /t 3 /nobreak > nul
echo  OK — ngrok rodando. Copie a URL e atualize no Twilio!
echo.

:: Inicia o servidor uvicorn
echo  [3/3] Iniciando servidor Bruno IA...
echo.
echo  ================================================
echo   SERVIDOR ATIVO — Aguardando mensagens...
echo   Webhook: /webhooks/twils
echo   Painel:  http://127.0.0.1:4040
echo   Para parar: pressione CTRL+C
echo  ================================================
echo.

uvicorn app.main:app --reload --port 8000

echo.
echo  Servidor encerrado.
pause
