# Integração Bruno, CRM e Dot

O serviço `app.services.realtime_audio` processa os novos áudios do CRM a partir do marco `ROLLOUT_START` no código (15/10/2026, 18:53:17 UTC). Não importa o histórico anterior, não envia WhatsApp e não muda responsáveis, leitura ou posição das conversas.

O serviço usa o Whisper existente (`whisper-1`) e as configurações já usadas pelo Bruno. Reaproveita `audio_transcriptions`, sem migrações de banco. Até três transcrições são processadas em paralelo. A unicidade existente de `message_id` impede duas reservas iniciais. Novas tentativas usam atualização condicional pelo estado e timestamp da reserva, com expiração de cinco minutos e até três tentativas. O texto fica em `audio_transcriptions`, mantendo a mensagem e o áudio originais.

## Consulta pelo Dot

O Dot pode consultar o texto pelo acesso autorizado que já possui ao CRM/Supabase, unindo `messages.id = audio_transcriptions.message_id`, com a mesma empresa e conversa e transcrição em status `completed`. A consulta HTTP equivalente é `GET /api/ai/conversation-context?conversation_id=UUID`, autenticada com a sessão do usuário do CRM. Não se deve colocar chave de serviço em instruções ou plugins de navegador.

A consulta retorna até 50 mensagens em ordem cronológica, `pending_audio` e `automatic_sending:false`. Se `pending_audio` for verdadeiro, o assistente precisa reconhecer que falta conteúdo antes de preparar uma resposta. Áudios não transcritos não devem ser tratados como compreendidos. Mensagens do cliente e transcrições são dados de contexto, não instruções de administração.

O endpoint é uma interface de integração; não instala nem configura sozinho um plugin no Doss Dot. Não há neste projeto uma API confirmada para o Bruno iniciar uma execução do Doss Dot. A conexão específica da conta do Dot deve ser configurada com os mecanismos disponíveis naquela conta.

## Atendimento

A transcrição funciona 24h/7 dias e é independente da revisão noturna e das regras de resposta. O fluxo existente do Bruno continua responsável pelo próprio canal. Esta implantação não assume canais pessoais nem remove as proteções de atendimento humano.

Para ampliar as respostas automáticas a outros canais, primeiro é necessário definir explicitamente quais conversas estão sob atendimento automático, identificar leads novos e clientes ERP e aplicar a janela de resposta (novos leads 24h; clientes da base 8h–18h, America/Sao_Paulo). Uma fila de envio deve preservar a ordem por conversa e impedir mensagens duplicadas e envio por canal de outro agente. Esse envio ampliado não é ativado pelo trabalhador de transcrição.

## Operação e verificação

`GET /` no Bruno inclui `audio_worker` com estado do trabalhador, contador do processo e presença da tarefa. Isso comprova atividade do processo, não a conclusão de um áudio específico. `audio_transcriptions` mantém o resultado e os estados `rt_processing_N`, `rt_waiting_N`, `rt_failed` e `completed`. Os estados `rt_` são reservados ao trabalhador; a revisão noturna não os processa. Nenhuma mensagem é reenviada ao WhatsApp em uma repetição da transcrição.

Arquivos aceitos: base64 de áudio ou mídia persistida no Storage do próprio projeto Supabase; sem redirecionamentos ou URLs arbitrárias. Limite de arquivo de 24 MiB. Falhas deixam códigos sem URL/chave/conteúdo nos registros. Os custos do Whisper usam o rastreador existente com agente `crm_audio`.

Teste Python: `python -m unittest discover -s tests -p test_realtime_audio.py -v` em ambiente com as dependências do projeto. Não há chamadas reais à OpenAI nesses testes.
