# Bruno — situação da entrega em 18/11/2026

Registro para continuidade da operação. Distingue serviço publicado, prévia do CRM e protótipo em desenvolvimento. Não contém preços, estoque, credenciais ou dados de clientes.

## O que está corrigido e publicado

| Componente | Situação confirmada | Evidência |
| --- | --- | --- |
| Identidade da execução SQL | Correção aplicada em produção | Versão `20261118123029` |
| Serviço Bruno, primeira entrega | PR 51 mesclada e publicada; incorporada à versão seguinte | Merge `87e23d6a6c7f025237f6365016f48e1853e59143` |
| Serviço Bruno, versão atual | PR 52 mesclada; produção confirmada pela leitura do serviço | Release `772c1636f0204754f34d92cb4270daac3b887f6a` |
| Consulta ao catálogo ERP | Consulta de produtos concluída com sucesso | Job `c7e010d5-caef-490d-b228-d0f7b82aaf09` |

Serviço publicado: [Bruno no Render](https://bruno-ia.onrender.com/).
Alterações: [PR 51](https://github.com/Michaeldoss/Bruno_ia/pull/51) e [PR 52](https://github.com/Michaeldoss/Bruno_ia/pull/52).

A consulta ERP de 18/11/2026, às 09h47min03s de Brasília, confirmou estas identidades:

- `221935`: `TINTA DTF PRIME WHITE - 1LT`.
- `222353`: `TINTA DTF PRIME YELLOW- 1LT`.

A planilha ainda repete o código do amarelo na linha do branco. Não usar essa divergência para substituir o resultado do ERP. Preço e disponibilidade exigem consulta atual; não são conhecimento durável.

## O que está em prévia e ainda não está no CRM de produção

- [PR 439 do CRM](https://github.com/Michaeldoss/doss-crm/pull/439) permanece aberta.
- Head conferido: `f088a2f5a21b9dd97847a8d6532dafd0915f1ed8`.
- Validação local informada nesta entrega: **62 testes aprovados e lint concluído**.
- [Prévia Vercel](https://doss-92seasx0k-michaelsin-giehl-de-freitas-projects.vercel.app): estado `READY`.
- A anotação da [execução 44304843209](https://github.com/Michaeldoss/doss-crm/actions/runs/44304843209) informou falha de pagamento ou limite de gastos. A [execução do código final, 44306506593](https://github.com/Michaeldoss/doss-crm/actions/runs/44306506593), voltou a falhar antes de executar etapas.
- Prévia pronta e validação local não equivalem a CI aprovado nem a implantação no endereço de produção do CRM.

## O que continua como protótipo

- [PR 50 do Bruno](https://github.com/Michaeldoss/Bruno_ia/pull/50) permanece em rascunho.
- Revisão de código validada antes deste registro documental: `032e993d12d37820721a5a26acd974e36b4b7391`.
- As duas verificações estão verdes: [run 44307190711](https://github.com/Michaeldoss/Bruno_ia/actions/runs/44307190711) e [run 44307190708](https://github.com/Michaeldoss/Bruno_ia/actions/runs/44307190708).
- Essa PR reúne a evolução do motor SDR. Não atribuir seus testes isolados ao comportamento do atendimento real sem validar o caminho integrado.

## Conhecimento técnico e estado da automação

Há **cinco fichas no CRM pendentes de revisão técnica**: DGtex, DTF Prime, UV Flex/DX, DGeco e avaliação Epson. A evidência ERP de identidade está registrada; ela não aprova automaticamente as fichas.

Conteúdo e fontes: [revisão técnica de tintas e Epson](bruno_ink_knowledge_review_20261118.md).

Na conferência desta entrega, a automação permaneceu assim:

- Atendimento global da caixa de entrada: `false`.
- Atendimento automático de novas entradas: `false`.
- Sessões habilitadas: **0**.

Publicação do serviço não significa ativação do atendimento automático.

## Bloqueios concretos restantes

1. **Crédito da API OpenAI:** a verificação do modelo retorna `model_429_credit_balance_exhausted`. O serviço de verificação responde HTTP 503 por esse bloqueio interno do provedor; publicar código não repõe o crédito.
2. **Faturamento do GitHub Actions:** impede a execução do CI da PR 439. Os resultados locais devem ser preservados, e o CI precisa ser executado após a liberação.

Não confundir falta de crédito com falha de publicação: a release atual do Bruno foi confirmada em produção, mas a geração pelo modelo continua indisponível.

## Próximas ações, em ordem

1. Regularizar o crédito da API OpenAI e repetir a verificação do modelo.
2. Liberar o faturamento do GitHub Actions e reexecutar o CI da PR 439.
3. Com CI aprovado e revisão concluída, mesclar a PR 439 e confirmar a versão no CRM de produção.
4. Revisar tecnicamente as cinco fichas pelo fluxo existente de aprovação, preservando fontes e limites.
5. Fazer um teste controlado: entrada do lead, identificação do produto, consulta ERP, resposta útil e continuidade registrada no CRM.
6. Conferir ausência de mensagem duplicada, preço sem fonte, promessa de retorno sem ação e silêncio por falha. Só então habilitar a automação no escopo do piloto.

## Exemplo de conversa: entrada “procuro DTF”

Roteiro ilustrativo de seis mensagens. Não é uma conversa enviada nem uma execução comprovada do modelo nesta entrega.

1. **Cliente:** Procuro DTF.
2. **Bruno:** Oi! Sou o Bruno, da Doss. Você procura uma máquina ou tinta para uma DTF que já está usando?
3. **Cliente:** Máquina. Hoje terceirizo umas 300 estampas por mês.
4. **Bruno:** Esse volume já ajuda a comparar a terceirização com a produção própria, incluindo insumos e rotina de manutenção. Como posso te chamar?
5. **Cliente:** João, sou de Joinville. Quanto custa uma máquina?
6. **Bruno:** Prazer, João. Ainda não tenho um valor confirmado para te apresentar. Para definir a configuração, qual é o tamanho mais comum das suas estampas?

Quando houver uma oferta atual e identificada, Bruno deve apresentá-la no mesmo atendimento, sem obrigar o cliente a repetir a pergunta de preço. Se a consulta falhar, deve explicar a limitação brevemente e continuar com uma pergunta útil, sem anunciar um retorno que não foi registrado.
