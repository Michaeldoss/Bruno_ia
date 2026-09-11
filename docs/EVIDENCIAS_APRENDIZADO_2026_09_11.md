# Conferência de evidências antes de responder

Conhecimento gerado a partir de conversa agora passa por nova conferência na leitura. A fonte deve ter versão reconhecida, empresa correspondente, conversa existente na empresa e evidências únicas com UUID/hash válidos. Cada mensagem precisa continuar na mesma conversa/empresa, sem exclusão e sem virar nota interna, com texto correspondente ao hash aprovado.

Somente o texto revisado do conhecimento entra no contexto do Bruno. As mensagens consultadas para comparar hashes não são enviadas ao modelo. Fontes manuais mantêm a política de aprovação existente. Registros inconsistentes são omitidos, sem alteração no banco. Erro ou timeout suprime o contexto de conhecimento e informa indisponibilidade ao atendente.

Mantém prazo total de quatro segundos, seis conhecimentos e 6.000 caracteres. Consultas adicionais são somente leitura, em lotes de até 80 IDs para limitar URL. Não há nova chamada de IA. Aumenta leituras no banco e pode reduzir disponibilidade do conhecimento se a consulta exceder o prazo.

Limites: verificação do texto, não do conteúdo binário de anexos; não certifica resultado comercial/técnico; leituras não são transação com o envio da resposta; mudanças após leitura só são detectadas na consulta seguinte. Não revoga permanentemente o registro nem resolve isolamento do ERP/canais/estado local. Nenhuma empresa adicional habilitada.

Validação: sintaxe Python, conferência de chamadores, limites e filtros. Sem suíte, mensagens a clientes, chamadas pagas ou mutações no banco. Cenário autenticado em produção não executado. Branch baseada na revisão #9 de diagnóstico; integração deve preservar ambas.
