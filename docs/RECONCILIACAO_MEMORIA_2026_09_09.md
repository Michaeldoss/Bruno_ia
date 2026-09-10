# Mensagens recuperadas antes do checkpoint

Problema: a revisão incremental consultava apenas mensagens posteriores ao último par data/ID processado. Uma mensagem importada com data antiga podia permanecer no CRM sem entrar na memória.

Correção: cada lote validado salva uma assinatura encadeada dos IDs e datas das mensagens externas retidas até o checkpoint. Antes de continuar uma conversa já processada, o ciclo relê somente IDs e datas desse trecho e compara a assinatura. Inclusões, remoções e mudanças de data nesse trecho provocam reconstrução cronológica, usando os lotes e controles financeiros existentes. Memórias anteriores sem assinatura também precisam de uma reconstrução progressiva inicial; isso consome análises dentro dos limites existentes.

Ao detectar mudança, limpa as afirmações derivadas da memória da conversa, marca cobertura incompleta e atualiza a projeção do cliente antes de chamar a IA. Não altera nem remove mensagens originais. Falha de leitura não é interpretada como histórico vazio. Falha de geração não avança o checkpoint. Se a conversa ficar sem mensagens externas, a memória continua sem afirmações antigas e com cobertura incompleta.

Não há migração de banco. A conferência ocorre no ciclo existente, não no atendimento e não em tempo real. Sua leitura não chama modelos, mas aumenta tráfego e consultas ao banco. Usa páginas de até 500, valida avanço e limita a conferência a 20 mil mensagens por conversa. Acima disso, interrompe essa tentativa com erro: será necessária agregação no servidor. Não significa memória completa nem processamento garantido naquela noite.

Limites: não detecta edição de conteúdo que preserve ID/data, nem alteração do arquivo na mesma URL ou transcrição posterior. Notas internas continuam excluídas. A leitura paginada não é uma transação: alterações concorrentes podem ser percebidas apenas no próximo ciclo. A invalidação da conversa e a consolidação do cliente são duas gravações; se a segunda falhar, a projeção pode ficar desatualizada até nova execução. Não há bloqueio entre trabalhadores. O atendimento pode consultar memória antiga até o ciclo chegar à conversa. Fotos, vídeos e documentos continuam dependendo de processamento próprio.

Verificação: compilação de sintaxe Python, revisão do diff e leitura da estrutura da tabela existente no Supabase. Os campos limpos na invalidação aceitam nulo. Nenhum dado de produção foi alterado nesta verificação. Sem suíte de testes, execução em produção, chamadas pagas ou envio de mensagens. A consulta REST e a reconstrução precisam de validação em execução antes de considerar a lacuna resolvida em produção.

Publicação somente na branch de revisão. Reversão: reverter o commit de código; memórias já reconstruídas não precisam ser removidas. Uma memória invalidada exige concluir sua reconstrução.
