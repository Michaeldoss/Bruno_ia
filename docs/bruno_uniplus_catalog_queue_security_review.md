# Bruno / Uniplus — revisão da fila de catálogo (18/11/2026)

## Evidência observada (leitura apenas)
- public.crm_erp_bridge_jobs tem RLS ativado.
- authenticated não tem SELECT nem INSERT na tabela; service_role tem INSERT.
- public.crm_erp_bridge_work opera claim, heartbeat, submitting e complete; NÃO cria solicitações de catálogo.
- Jobs catalog usam payload.product_codes e payload.request_context, além de org_id, owner_id e customer_code.
- Índices observados: PK id; índice de pendentes; UNIQUE (org_id,owner_id,source_message_id) somente para kind='issue'; índice de recompra para kind='inspect'.
- **Não existe índice UNIQUE de catalog por mensagem/produto** no conjunto de índices consultado.
- A função de worker aceita config habilitada com código do cliente igual ao job ou configuração global customer_code='0' para inspect/catalog.
- O resultado catalog é histórico e pode conter preço, mas isso não comprova preço vigente nem tabela comercial adequada.

## Consequência
O identificador SHA-256 criado no Bruno é apenas uma identidade lógica; não garante deduplicação concorrente na fila real. Sem transação atômica, dois webhooks podem criar dois jobs.

## Requisitos para serviço autorizado do CRM
1. Receber pedido autenticado do Bruno, conferir organização, cliente, conversa e código Uniplus do cliente.
2. Verificar política de quem pode solicitar catalog e que owner_id pertence à organização.
3. Criar a solicitação pelo backend autorizado, nunca expor service_role ao Bruno/frontend.
4. Deduplicar atomicamente por org, owner, mensagem, cliente, SKU e tipo catalog; usar uma coluna/chave persistida e índice único apropriado ou lock transacional.
5. Definir limites de taxa, timeout, número de tentativas, auditoria e retorno assíncrono.
6. Retornar apenas resultado da solicitação específica; jamais consultar último preço de outro cliente.
7. Verificar preço comercial e validade antes de oferecer; não usar job histórico.
8. Em erro, transferir para humano quando necessário, sem prometer retorno não rastreável.

## Bloqueadores para ativação
- Localizar o serviço real que insere catalog jobs e seu contrato de autorização.
- Definir onde a idempotência persistente será implementada sem afetar jobs antigos.
- Provar operação em ambiente de teste com duas requisições concorrentes.
- Comprovar resposta ao cliente, criação/atualização de oportunidade e handoff no CRM.
- Revisar segurança da nova API, permissões, RLS e logs antes de produção.

Nenhuma modificação de esquema ou job foi feita durante esta revisão.
