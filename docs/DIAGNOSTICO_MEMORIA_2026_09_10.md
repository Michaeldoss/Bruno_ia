# Diagnóstico da revisão noturna

Consulta em 10/09 confirmou 1.282 memórias e última análise em 07/09 às 22:00 UTC. PR8 já foi integrada externamente e a produção confirmou release b382bf4ba7c9d4edaee42547024e07457d932dbd com worker vivo. Isso não comprova execução da análise.

Esta revisão adiciona GET /api/memory-diagnostics, protegido pela chave de instalação x-bruno-key (BRUNO_API_KEY). Destinado ao operador do servidor, não ao navegador ou gestores de empresas. Informa próxima janela, disponibilidade do orçamento e último ciclo observado por organização configurada. Não aceita org_id do solicitante, não retorna mensagens e não dispara análises pagas.

Motivos distinguem limite de tentativas, reserva para atendimento, teto da memória, medidor indisponível e exceção. Estado é local ao processo, perdido em reinícios e não compartilhado entre workers. Ausência de observação não significa sucesso nem ausência histórica de execução. Não há recuperação automática de janela perdida durante indisponibilidade.

Corrige também o painel de custos, que somava serviços diferentes na rubrica Anthropic. Mantém os limites existentes. A causa da ausência de análises ainda não foi comprovada; diagnóstico deve ser consultado após implantação e após a janela das 19h Brasília.

Verificação: compilação de sintaxe Python e revisão do diff. Sem suíte, chamadas pagas, envio a clientes ou modificação de dados de produção. Isolamento completo de canais/ERP/estado local entre empresas continua pendente.
