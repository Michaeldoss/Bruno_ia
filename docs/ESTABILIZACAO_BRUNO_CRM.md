# Estabilização Bruno IA + Doss CRM

## Objetivo

Aumentar tolerância a falhas sem remover prompts, catálogo, memória, campanhas ou regras comerciais existentes.

## Regras de segurança

- Nunca alterar `main` diretamente.
- Toda correção passa por branch e PR.
- Handoff só é considerado concluído após confirmação do CRM.
- Webhooks precisam ser idempotentes e reprocessáveis.
- Falhas 408/429/500/502/503/504 devem usar retry com backoff.
- Origem, campanha e UTMs devem permanecer vinculadas ao telefone até a entrega ao CRM.

## Correções aplicadas nesta branch

- idempotência persistente por `MessageSid` do Twilio;
- recuperação de evento preso em processamento;
- normalização E.164 de telefones;
- persistência de origem, campanha, anúncio, formulário e UTMs;
- contexto preservado durante o debounce;
- retries e timeout no Twilio;
- falha do Twilio agora é propagada, não tratada como sucesso;
- retries e respostas estruturadas na entrega ao Doss CRM;
- validação de nome antes da criação do lead;
- diferenciação entre qualificação incompleta, rejeição e indisponibilidade;
- retries no espelhamento do Inbox;
- workflow de compilação e testes básicos.

## Pendente antes do merge

- revisar fechamento forte para não marcar `closed` quando o CRM falhar;
- revisar follow-ups e cancelamento após resposta;
- revisar captura de assinatura do webhook Twilio;
- validar workflow n8n sanitizado;
- executar testes funcionais ponta a ponta com número de teste;
- revisar logs do Preview do CRM e do ambiente do Bruno.
