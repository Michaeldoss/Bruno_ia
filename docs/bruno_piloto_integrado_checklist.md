# Bruno — protocolo de piloto integrado controlado

Data: 18/11/2026. Este documento é um roteiro de teste; não autoriza alterações em produção.

## Pré-condições obrigatórias
- Ambiente isolado ou organização de testes, com contatos e números explicitamente autorizados.
- Usuário humano autenticado e ativo para qualquer transferência; não usar service_role como identidade de usuário.
- Instância WhatsApp de teste, sem tráfego de clientes reais.
- Identificar e verificar o contrato real de consulta Uniplus e versão do SDK OpenAI.
- Confirmar que não há envios duplicados, inclusive em repetição de webhook.
- Logs com identificadores de correlação e sem tokens, CPF, dados sensíveis ou conteúdo integral de conversa.

## Cenário 1 — Venda assistida de tinta
1. Cliente de teste pergunta preço de tinta DTF.
2. Bruno identifica tecnologia e equipamento, sem presumir compatibilidade.
3. Consulta catálogo Uniplus por código exato e organização correta.
4. Somente apresentar valor com preço atual, aprovação comercial e política de estoque.
5. Registrar oportunidade de teste no pipeline correto.
6. Conferir contato, conversa, produto, etapa, responsável e próxima ação.
7. Reexecutar mesmo evento; confirmar ausência de duplicação.

## Cenário 2 — Transferência autorizada
1. Bruno sugere transferência; humano autenticado aprova.
2. Serviço CRM confere novamente org, conversa, atendente atual e alvo.
3. Serviço autorizado executa transfer_conversation_assignment_v3.
4. Conferir conversations.agent_id e whatsapp_instance, contato e organização.
5. Conferir registro doss_private.conversation_handoffs via backend autorizado.
6. Abrir conversa como vendedor destinatário e verificar histórico com autoria.
7. Testar concorrência: outra atribuição entre preparação e execução deve abortar.

## Cenário 3 — Falhas seguras
- ERP lento ou indisponível: não citar preço nem prometer retorno sem tarefa.
- ERP retorna produto duplicado, inativo, divergente ou preço antigo: bloquear oferta.
- Modelo responde valor diferente do autorizado: bloquear envio e encaminhar revisão.
- Solicitação de transferência sem sessão humana: bloquear.
- Histórico não acessível ao destinatário: não declarar transferência concluída.
- Cliente pede interrupção: parar mensagens automáticas.

## Critérios para liberar piloto assistido
- Testes automatizados verdes na branch de integração.
- 100% dos cenários críticos de segurança aprovados.
- Evidência de uma venda de teste completa com CRM, ERP e histórico, sem cliente real.
- Confirmação do responsável humano e procedimento de rollback testado.
- Nenhuma mudança em produção até revisão explícita da implantação.

## Plano de rollback
- Desabilitar o recurso do Bruno por feature flag, sem desligar o WhatsApp existente.
- Reverter apenas a integração nova, preservando histórico e atendimento humano.
- Revisar filas pendentes e assegurar que nenhuma resposta automática ficou agendada.
- Não apagar mensagens, leads ou históricos como método de rollback.

## Evidências a guardar
Commit e execução de CI, IDs de teste anonimizados, timestamps, status ERP,
resultados de consulta, responsável antes/depois, registro de handoff e screenshots
da conversa de teste. Não usar dados reais de clientes nos artefatos.
