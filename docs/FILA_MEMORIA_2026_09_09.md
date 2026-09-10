# Distribuição da revisão noturna entre responsáveis

O ciclo percorria conversas por ID e parava no teto diário. Conversas com muitos lotes pendentes no começo da ordem podiam consumir repetidamente o orçamento antes dos demais responsáveis.

Agora a revisão carrega as conversas da organização e os metadados de análise com paginação por chave. Alterna uma conversa por responsável, incluindo uma fila para conversas sem responsável. Dentro de cada fila, prioriza ausência de análise e depois a análise mais antiga. Empates variam diariamente para reduzir a repetição de registros com falha na frente da fila. A classificação usa o responsável atual, não todos os participantes históricos.

Erros de leitura interrompem a montagem da fila; não são tratados como ausência de memória. Falhas de processamento continuam consumindo o teto de tentativas, pois uma chamada pode já ter sido cobrada. Os registros do ciclo distinguem uso de IA retornado, processamento sem IA, falhas e conversas adiadas pelo limite. Uso de IA não comprova persistência de uma memória válida.

O agendamento existente, os limites financeiros e os checkpoints de mensagens permanecem. Há leitura adicional dos metadados de memória e armazenamento temporário da fila em RAM. A montagem da fila não chama modelos; a distribuição pode alterar quantas análises cabem no orçamento existente.

Limitações: não há prazo máximo garantido com orçamento insuficiente, nem fila transacional ou bloqueio distribuído. Novas conversas criadas atrás do cursor da listagem podem entrar apenas no próximo ciclo. Mensagens importadas antes do checkpoint, edições, exclusões e reprocessamento de mídia continuam pendentes de reconciliação específica. Não há garantia de cobertura de todos os setores em uma única noite.

Verificação: compilação de sintaxe dos dois módulos e revisão do diff. Sem suíte de testes, chamadas pagas, envio de mensagens ou execução contra produção. Validação da execução noturna permanece pendente. Publicação prevista somente na branch de revisão; para desfazer, reverter este commit.
