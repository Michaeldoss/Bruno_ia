# Base da memória coletiva — 07/09/2026

## Evidências da revisão do código

- O ciclo consultava apenas conversas abertas com mensagem no dia. Conversas encerradas e pendências antigas podiam ficar fora da revisão.
- A janela de mensagens recentes podia excluir mensagens intermediárias e mesmo assim atualizar o marcador até a última mensagem da conversa.
- Resposta inválida ou truncada da IA podia resultar em um resumo de contingência com avanço do marcador.
- Gravações das memórias não verificavam sistematicamente o status HTTP.
- A memória do cliente era substituída pela análise da conversa atual, sem consolidar as outras conversas.
- O atendimento automático em openai_client.py não consulta as tabelas de conhecimento aprovado, exemplos e memória consolidada usadas pelos outros módulos. A integração não está concluída.

## Alterações desta entrega

Leitura de todos os status e datas, com paginação. Cada conversa processa um lote cronológico por ciclo. O marcador composto de data e ID evita pular mensagens com a mesma data. Registros legados sem marcador versão 2 são reconstruídos desde a primeira mensagem externa retida. Essa reconstrução é progressiva, não instantânea.

A análise precisa conter JSON válido, resumo e listas de memória e não pode ter terminado por limite de saída. Falhas preservam o marcador. Leituras e gravações da memória verificam erros HTTP; uma falha na consolidação do cliente após salvar a conversa pode ser reparada no ciclo seguinte.

A projeção do cliente reúne as memórias de suas conversas na mesma organização. O resumo identifica a conversa e o responsável; listas são deduplicadas, sem resolver automaticamente contradições. A projeção continua derivada de resumos, não é um arquivo integral dos fatos.

Metadados em raw_analysis.coverage registram mensagens do lote, participantes e setor consultado no perfil durante a ingestão. Esse setor não prova a lotação histórica do funcionário. Há indicação de histórico parcial, mídia sem análise e contexto truncado. Recomendações operacionais ficam suspensas enquanto ainda há lotes históricos a processar.

Não há migração de esquema nesta entrega. Permanecem a revisão diária às 19h de Brasília, o limite mensal configurado e o teto de análises por ciclo. O custo real e a duração da reconstrução dependem do volume e do provedor; o limite usa a estimativa existente e não é um bloqueio financeiro transacional.

## Limitações e próximos trabalhos necessários

1. Notas internas não são ingeridas; definir separação de conteúdo interno e conteúdo utilizável em respostas externas antes de incluí-las.
2. Fotos, vídeos e documentos não são interpretados por este ciclo. Áudio depende de transcrição disponível. As lacunas são registradas; o reprocessamento de mídia pendente precisa de uma fila própria.
3. Resumos continuam sujeitos a omissões e limites de contexto. Criar registros de fatos com fonte, versão, validade e correção, mantendo o histórico bruto como evidência. Não interpretar coverage.complete como garantia de memória factual perfeita.
4. Mensagens editadas, excluídas ou importadas com data anterior ao marcador precisam de reconciliação por eventos. O marcador atual cobre novas mensagens em ordem de criação.
5. Implementar fila persistente com distribuição justa, bloqueio por conversa e transações; o ciclo atual pode favorecer conversas anteriores na ordenação e não impede trabalhadores simultâneos.
6. Auditar RLS e permissões de setor antes de disponibilizar memória consolidada a todos os agentes. Esta entrega não amplia permissões nem conecta a projeção a respostas automáticas.
7. Conectar o Bruno automático à recuperação autorizada de conhecimento, com fontes e regras de ação por SDR, vendas, pós-venda, assistência e financeiro. Conversas de um cliente não devem vazar para outro.
8. Criar acompanhamento de cobertura, atraso, falhas, custo e qualidade por setor/agente. Contagem de chamadas à IA não comprova gravação nem utilização da memória.

## Verificação e publicação

Verificação de sintaxe Python e whitespace do diff; nenhuma suíte de testes nem atendimento real foi executado, conforme solicitação do usuário. Integração com o banco e comportamento em produção ainda não verificados. Entrega em branch para revisão, sem alteração da main ou implantação.


## Continuação — 08/09: utilização da memória nas respostas

A revisão aprofundada confirmou que o Bruno já chama buscar_memoria_ia para recuperar análises de várias conversas do mesmo contato. Portanto, a integração de memória de conversas existe; a lacuna é sua confiabilidade e a ausência de consulta ao conhecimento aprovado, exemplos e projeção customer_ai_memory.

Correções adicionais:

- A consulta do histórico local selecionava as primeiras 40 mensagens em ordem crescente. Agora seleciona as últimas 40, com desempate por ID, e reordena cronologicamente para o modelo. A mensagem atual já é persistida antes dessa consulta.
- Contatos duplicados para o mesmo telefone deixam de ser escolhidos arbitrariamente: a memória adicional é omitida enquanto a identidade estiver ambígua. Isso não resolve duplicidade cadastral nem telefones compartilhados entre pessoas.
- A consulta de análises inclui organização e contato explicitamente e mantém a seleção de conversas desse contato.
- O contexto enviado ao Bruno preserva conversa, responsável, datas e cobertura de cada fonte. Recomendações antigas deixam de ser rotuladas como situação atual e listas inferidas deixam de ser apresentadas como fatos certificados.
- Recuperação limitada a 100 conversas e 100 análises, com sinalização de limite e ausência de memória. O contexto tem orçamento de 12 mil caracteres de registros; fontes que não couberem ficam fora, com indicação de parcialidade. Essa janela serve à resposta e não representa a capacidade total de armazenamento.
- Nenhuma nova permissão de leitura nem canal de envio foi habilitado. A recuperação entre setores já existente continua dependendo de revisão das regras de acesso; prompts não substituem autorização no banco.

Verificação: sintaxe dos três módulos alterados e diff; sem suíte de testes e sem mensagens reais. Confirmar leitura com o esquema implantado e qualidade em execução permanece pendente.
