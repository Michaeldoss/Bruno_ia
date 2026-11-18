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

A fonte de 2025 foi corrigida: `SUPRIMENTOS!A31` passou de `222353` para `221935`, e A30 manteve `222353`. A releitura do CSV efetivamente usado pelo catálogo confirmou a alteração em 18/11/2026, às **11h58min55s de Brasília**. Essa validação identifica os produtos na fonte; preço e disponibilidade continuam exigindo consulta atual ao ERP e não são conhecimento durável.

## O que está em prévia e ainda não está no CRM de produção

- [PR 439 do CRM](https://github.com/Michaeldoss/doss-crm/pull/439) permanece aberta.
- Head conferido: `791173956bff24018e2f5b27a86ef751671842dd`.
- Validação local dos fluxos afetados: **69 testes aprovados**; ESLint de runtime aprovado nos três arquivos de aplicação alterados. Isso não representa execução de toda a suíte do CRM.
- [Prévia Vercel](https://doss-ae9q7spd8-michaelsin-giehl-de-freitas-projects.vercel.app): estado `READY`, no mesmo head.
- A anotação da [execução 44304843209](https://github.com/Michaeldoss/doss-crm/actions/runs/44304843209) informou falha de pagamento ou limite de gastos. A [execução do código final, 44314086951](https://github.com/Michaeldoss/doss-crm/actions/runs/44314086951), voltou a falhar antes de executar etapas.
- A revisão incorpora a main `bbe0adc66eade4c7e9f1aca14f66e8bb2c474d40`, preservando as correções recentes de cartões de contato e aviso no perfil.
- Prévia pronta e validação local não equivalem a CI aprovado nem a implantação no endereço de produção do CRM.

Na continuação, foram corrigidos dois casos de silêncio após falha do modelo: pergunta pelo código de um produto já confirmado e pergunta curta como “Quanto fica?” sobre o produto anterior. O fluxo simulado mantém a preparação persistida e faz um único envio sintético. O filtro comercial também passou a reconhecer BRL, preços sem símbolo monetário e milhares; um preço unitário não autoriza oferecer parcelamento. Medidas, datas, embalagens e valores atribuídos ao cliente continuam preservados nos testes.

## Fonte de produtos corrigida e releitura validada

A fonte do catálogo em produção foi rastreada até o [arquivo de 2025 no Drive](https://docs.google.com/spreadsheets/d/1WWLllb5P9WFc-LkMLcnA5HCSBfdhyvi1/edit?gid=9362899#gid=9362899), ID `1WWLllb5P9WFc-LkMLcnA5HCSBfdhyvi1`. Antes da edição, o XLSX era idêntico ao anexo recebido. SHA256 daquela versão original: `d658117b38437d66bb8763fc54e692e622754c15d3fbd3e5eb84b01b86a3c5d1`.

A cópia arquivada da versão corrigida se chama `Produtos_Doss_2025_Corrigido_Origem.xlsx`, tem **23.815 bytes** e SHA256 `83eeb3b7a211de7e997fbbc6b240f76431c1cfe114cadcea79f8bde9a33b4690`. Esse é o hash do XLSX após a edição, diferente do hash do anexo original. A comparação do arquivo bruto obtido do Drive confirmou a preservação de conteúdo e formatos, com A31 como única célula alterada; o Google reindexou os mesmos 57 estilos e registros de texto e atualizou metadados. A identidade do arquivo de origem no Drive permanece a mesma.

A tela `src/pages/ProductCatalog.jsx` do CRM contém dez famílias estáticas. Ela não consulta o índice de SKUs nem alimenta o resolvedor do Bruno; não é a fonte a corrigir.

O conflito entre duas linhas da aba `SUPRIMENTOS` foi corrigido na fonte:

| Célula | Identificação na fonte | Código anterior | Código confirmado após salvar |
| --- | --- | --- | --- |
| A30 | Tinta DTF Prime amarela | `222353` | `222353`, mantido |
| A31 | Tinta DTF Prime branca, grafada `WITHE` | `222353` | `221935`, corrigido |

A edição pontual de A31 foi **salva pela interface da planilha**, após a autenticação Google. A releitura do CSV real terminou às **11h58min55s de Brasília de 18/11/2026** (`14:58:55 UTC`), com HTTP 200, **59 linhas e 46 produtos**. A comparação entre o CSV anterior e o posterior encontrou **uma única diferença: A31, de `222353` para `221935`**. A30 permaneceu `222353`; a grafia `WITHE` também foi preservada.

A reprodução do resolvedor com esse CSV confirmou:

| Consulta | Resultado do resolvedor |
| --- | --- |
| Tinta DTF branca 1L | Candidato `221935`, elegível para validação no ERP |
| Tinta DTF amarela 1L | Candidato `222353`, elegível para validação no ERP |
| Tinta DTF preta 1L | Candidato `222356`, elegível para validação no ERP |
| Tinta UV Flex branca 1L | Candidato `222289`, elegível para validação no ERP |
| DTF | Consulta ambígua, sem seleção automática de um produto |

Essa verificação cobre a releitura da fonte e a resolução de candidatos. **Não é uma cotação ERP nem um teste de atendimento ou geração pelo modelo**, e não comprova disponibilidade ou condições comerciais. A evidência sem preços e sem caminhos temporários está em [bruno_catalog_fix_evidence_20261118.json](bruno_catalog_fix_evidence_20261118.json).

## O que continua como protótipo

- [PR 50 do Bruno](https://github.com/Michaeldoss/Bruno_ia/pull/50) permanece em rascunho.
- Revisão de código validada antes deste registro documental: `032e993d12d37820721a5a26acd974e36b4b7391`.
- As duas verificações estão verdes: [run 44307190711](https://github.com/Michaeldoss/Bruno_ia/actions/runs/44307190711) e [run 44307190708](https://github.com/Michaeldoss/Bruno_ia/actions/runs/44307190708).
- Essa PR reúne a evolução do motor SDR. Não atribuir seus testes isolados ao comportamento do atendimento real sem validar o caminho integrado.

## Conhecimento técnico e estado da automação

As **cinco fichas de tintas e Epson no CRM continuam pendentes de revisão técnica**: DGtex, DTF Prime, UV Flex/DX, DGeco e avaliação Epson. A evidência ERP de identidade está registrada; ela não aprova automaticamente as fichas.

Também foram registradas as **20 configurações de equipamentos** na ficha `6145a104-d436-4e84-8e81-73310ee24f7e`, em estado `pending`, **não aprovada**. Esse registro não contém preços atuais e não autoriza ofertas comerciais nem recomendações técnicas sem revisão.

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
4. Revisar tecnicamente as cinco fichas de tintas e Epson e a ficha de equipamentos pelo fluxo existente de aprovação, preservando fontes e limites.
5. Com o modelo disponível e a versão validada no CRM, fazer um teste controlado: entrada do lead, identificação do produto na fonte corrigida, consulta ERP atual, resposta útil e continuidade registrada no CRM.
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
