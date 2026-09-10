# Memória privada e conhecimento revisado por empresa

## Implementação

Leitura de memória do cliente, conhecimento aprovado, transcrições e revisão noturna exigem organização explícita. Ausência ou UUID inválido falham; não se deduz empresa pelo telefone. Contatos e responsáveis da revisão são consultados pelo mesmo org_id, sem confiar apenas nos relacionamentos por ID.

A revisão noturna aceita CRM_MEMORY_ORG_IDS, lista de UUIDs administrada no servidor. Sem configuração adicional, mantém somente a organização Doss já existente. Não cadastra nem habilita outras empresas automaticamente. Organizações compartilham os limites e o provedor da instalação; cobrança separada e distribuição de orçamento por empresa ainda requerem infraestrutura própria.

Análises válidas e com cobertura completa classificadas como pedido autorizado ou suporte geram candidatos pendentes. São indícios para seleção humana, não comprovação de venda paga ou reparo resolvido. Há exigência de texto do cliente no lote de evidências. Categorias vendas/assistência não contêm regras de um segmento específico.

O candidato referencia IDs e hashes dos textos da origem, sempre na mesma organização. Não copia o resumo privado para a tabela de conhecimento: pendentes recebem título/texto genéricos. A sugestão de resumo é consultada somente pelo endpoint de revisão autenticado para gestores. Aprovar exige conferir evidências, confirmar resultado e revisar dados particulares. O registro guarda aprovador e data. O modelo não pode aprovar sua própria avaliação.

Inserção com ID determinístico e ignore-duplicates não sobrescreve revisão humana. Uma falha de criação pode ser retomada quando a conversa voltar ao ciclo. Conhecimentos da conversa são desativados quando a reconciliação de IDs/datas invalida a memória. Fontes editadas ou removidas bloqueiam aprovação no CRM. Rejeitados não são usados. Aprovação não certifica pagamento, estoque, preço ou diagnóstico atual.

## Implantação conjunta

Backend Python requer a revisão conhecimento-por-empresa-2026-09-10 no doss-crm (endpoint /api/company-learning e painel em IA Comercial). Não há migração de banco. A tabela bruno_knowledge existente possui org_id e estados pending/approved/rejected. O CRM resolve empresa e papel no perfil autenticado, nunca no corpo enviado pelo navegador; filtra fontes, mensagens e gravações pela organização.

## Limites que não devem ser apresentados como concluídos

- O atendente WhatsApp existente ainda é uma instalação Doss: prompt, ERP, canais, filas locais e modelos locais por telefone continuam específicos. Não reutilizar essa instalação como atendente de outra empresa. O núcleo de memória admite escopo explícito; isso não converte o restante do atendente em SaaS com múltiplas empresas.
- Não foram habilitadas empresas adicionais, configuradas credenciais/canais de terceiros ou compartilhadas memórias Doss.
- A assinatura de reconciliação de histórico não detecta edições que preservem ID/data. Aprovação confere hashes, mas edição posterior à aprovação exige revogação; não há transação conjunta entre mensagem e conhecimento.
- Fotos, vídeos e áudio sem transcrição não geram solução comprovada; cobertura parcial não entra na seleção automática. Isso pode produzir poucos candidatos até concluir a reconstrução.
- Evidências são o lote final, não todas as mensagens de uma negociação longa. O gestor deve consultar o histórico completo quando o lote não comprovar o resultado.
- RLS existente permite leitura de conhecimento por usuários da própria organização. Resumos privados não são gravados nos candidatos; a classificação interna/externa do restante das bases existentes ainda exige auditoria.

## Verificação

Sintaxe Python, conferência dos chamadores, revisão do diff, leitura das regras e campos existentes no banco. CRM: sintaxe de API, verificação de nomes usados e compilação Vite. Sem suíte de testes, mensagens a clientes, execução paga ou alterações no banco. Fluxo autenticado, isolamento entre duas empresas em execução e aprovação real ainda não foram verificados. Publicar revisão não comprova implantação nem aprendizado efetivo.
