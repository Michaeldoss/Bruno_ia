# BRUNO IA — Linha contínua e plano de liberação

Atualização: 18/11/2026. Documento de direção, não atestado de implantação.

## Missão
Bruno é o agente comercial digital da Doss Group: recebe e qualifica oportunidades, usa conhecimento técnico aprovado, consulta informações comerciais autorizadas, dá continuidade à conversa e transfere ao humano com contexto. A prioridade comercial é recorrência de tintas (sublimação, eco solvente, DTF têxtil, UV), sem abandonar equipamentos.

## Linha contínua de relacionamento
Origem do contato → identificação e histórico → diagnóstico (máquina, tinta, volume, fornecedor, dor) → consulta técnica aprovada → consulta comercial Uniplus → proposta/transferência → acompanhamento → recompra → aprendizado gerencial.

Cada etapa registra: org_id, contact_id, conversation_id, responsável atual, etapa, necessidade, produto/código, evidência, próxima ação, prazo, autor e status. Nunca duplicar contatos por número pessoal versus número cadastrado; preservar identidade de cada canal e histórico.

## Responsabilidades e limites
- Bruno: qualificar, explicar informação aprovada, sugerir próxima ação, preparar contexto e rascunhos.
- Vendedor: exceções comerciais, descontos, negociação sensível e fechamento que exija autorização.
- Técnico: conversão Epson, troca de tintas, flush, compatibilidade e procedimentos de risco.
- CRM: identidade, atribuição, histórico, fila, SLA, auditoria e relatórios.
- Uniplus: preço/cadastro/estoque e histórico de compras quando disponíveis e autorizados.
- Gestor: aprovar políticas, medir resultado, corrigir desvios.

## Fase A — Confiabilidade e qualidade (bloqueadora)
1. Validar branch isolada e dependências reais do runtime.
2. Provar no ambiente de teste que não envia mensagem sem autorização, não duplica envio e não retoma conversa transferida.
3. Testar cenários: cliente novo, recompra, pergunta de preço, objeção, reclamação, mídia/áudio indisponível, consulta ERP lenta, contato duplicado, transferência e retorno.
4. Critérios: zero preço inventado, zero compatibilidade inventada, zero promessa de retorno sem tarefa rastreável; todo handoff deve ter dono e contexto.
5. Verificar mensagens e estado no CRM antes de ativar WhatsApp.

## Fase B — Catálogo e proposta de tintas
1. Confirmar rota autenticada de catálogo já existente no CRM, sem criar acesso paralelo.
2. Exigir correspondência exata por código, org_id e produto ativo.
3. Verificar preço, validade, estoque, política comercial e disponibilidade antes de ofertar.
4. Resolver divergência histórica do SKU 222353 (Yellow/White) com cadastro Uniplus; a consulta de 11/11 registrou White código 221935, mas é histórica.
5. Converter tabela Epson em conhecimento com revisão técnica; preços de chip/conversão da planilha são históricos.
6. Em falha do ERP, responder sem preço e transferir quando necessário, nunca prometer retorno automático sem fila e SLA.

## Fase C — Operação CRM
1. Histórico integral com autor identificado; transferências sem retorno indevido.
2. Etapas de funil, motivo de perda, pendências, próximas ações e SLA.
3. Registrar quem assumiu, quando e qual contexto foi entregue.
4. Métricas: leads atendidos, contatos qualificados, ofertas aprovadas, conversão em pedido, receita atribuída, tempo até resposta, handoffs sem dono, reclamações e perda por motivo.

## Fase D — Recorrência
1. Consumidor identificado por máquina/tecnologia e consumo médio mensal.
2. Estimar próxima reposição a partir de compras efetivas, não apenas calendário genérico.
3. Segmentos: ativos, atrasados, inativos, alto potencial, risco de perda.
4. Mensagens de recompra somente com consentimento/base apropriada, horários e limite de frequência.
5. Não competir com vendedor responsável; respeitar atribuição e opt-out.

## Fase E — Prospecção
1. Receber leads qualificados do God Eye; exigir evidência, origem e data.
2. Separar máquina própria de produção terceirizada.
3. Priorizar intenção, consumo e possibilidade de compra; evitar spam e dados não verificados.
4. Testar em lote pequeno antes de escalar.

## Critérios de liberação progressiva
- Laboratório: testes automatizados + casos reais anonimizados.
- Piloto interno: funcionários e contatos de teste, sem clientes reais.
- Piloto assistido: poucos contatos autorizados, supervisão e rollback.
- Produção gradual: aumentar volume apenas com métricas e ausência de incidentes críticos.

## Pendências verificadas
- O novo motor OpenAI não está ligado ao webhook de produção.
- O adaptador de tinta recebe snapshot no formato do CRM, mas ainda não dispara consulta autenticada em tempo real.
- O código de orquestração é isolado e não envia mensagens.
- O guard de preços detecta valores em reais sem validação, mas precisa de avaliação com cenários de múltiplos itens e valores históricos.
- Confirmar compatibilidade da versão do SDK OpenAI no requirements de produção.
- Verificar cobertura de testes e contrato de transferência/handoff.

## Princípio comercial
Bruno não termina na proposta. Toda conversa tem resultado, responsável e próxima ação rastreável. O objetivo não é maximizar mensagens, e sim margem, recompra e retenção com qualidade.
