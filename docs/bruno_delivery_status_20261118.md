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
- Head conferido: `021a7daccb36a6475edbff318f4b4990b26253bd`.
- Validação local dos fluxos afetados: **97 testes aprovados**, sem falhas ou testes ignorados, em seis arquivos de testes. ESLint aprovado nos arquivos de runtime e interface envolvidos. Isso não representa execução de toda a suíte do CRM.
- [Prévia Vercel](https://doss-7lparwnir-michaelsin-giehl-de-freitas-projects.vercel.app): estado `READY`, no mesmo head.
- O bloqueio de pagamento ou limite de gastos foi confirmado diretamente na anotação da [execução 44337601515](https://github.com/Michaeldoss/doss-crm/actions/runs/44337601515). A [execução do código atual, 44342807773](https://github.com/Michaeldoss/doss-crm/actions/runs/44342807773), também terminou com falha: `workflow-syntax` não executou nenhuma etapa e `tests-and-build` foi ignorado. Sua anotação detalhada não foi inspecionada novamente. O gate permanece obrigatório.
- A revisão incorpora a main `bbe0adc66eade4c7e9f1aca14f66e8bb2c474d40`, preservando as correções recentes de cartões de contato e aviso no perfil.
- Prévia pronta e validação local não equivalem a CI aprovado nem a implantação no endereço de produção do CRM.

Na continuação, foram corrigidos dois casos de silêncio após falha do modelo: pergunta pelo código de um produto já confirmado e pergunta curta como “Quanto fica?” sobre o produto anterior. O fluxo simulado mantém a preparação persistida e faz um único envio sintético. O filtro comercial também passou a reconhecer BRL, preços sem símbolo monetário e milhares; um preço unitário não autoriza oferecer parcelamento. Medidas, datas, embalagens e valores atribuídos ao cliente continuam preservados nos testes.

A revisão anterior acrescentou oito testes e duas correções de continuidade:

- **Mudança de interesse:** se o cliente passa de tinta para máquina, Bruno retoma a qualificação do equipamento. O assunto antigo não faz uma pergunta de volume mensal virar uma pergunta de cor ou quantidade de tinta. Perguntas curtas de preço continuam vinculadas ao assunto atual; nome e cidade já informados permanecem disponíveis.
- **Recusa explícita de compra:** frases como “tenho tinta suficiente, não preciso comprar agora” seguem o contrato de espera, sem consultar catálogo/modelo ou enviar uma nova oferta. Isso não encerra a conversa nem registra opt-out. Uma pendência como “meu pedido está atrasado” continua elegível para atendimento, mesmo na mesma mensagem da recusa.

Os testes também cobrem “quero uma máquina, mas não quero comprar tinta”: a recusa do insumo não deve substituir o pedido positivo de equipamento. A detecção de recusa é deliberadamente conservadora; não é garantia de classificação determinística de toda frase possível sobre estoque. A validação do contrato de espera foi feita com dependências simuladas, sem alterar sessões reais.

A revisão adicional considerou **34 mensagens de texto em cinco conversas recentes**. As mensagens de saída usadas como exemplos positivos estavam associadas a usuários do CRM; essa associação não comprova autoria exclusivamente humana. Os casos de teste foram reescritos sem dados que identifiquem clientes. A amostra ajudou a verificar a preservação da intenção atual, dos itens de reposição informados e da informação de estoque suficiente. Testes adicionais cobriram a recusa explícita de compra. Trata-se de uma amostra limitada para orientar regras e testes; não houve treinamento do modelo nem validação do atendimento ao vivo nesta revisão.

### Preço confirmado deve ser apresentado

Foi reproduzido outro caso no caminho completo do worker com dependências simuladas: o cliente pedia o preço de uma tinta exata, o catálogo devolvia uma única oferta confirmada e a cidade já estava no cadastro; ainda assim, rascunho e revisão perguntavam novamente a cidade. A resposta passava pelos filtros porque não inventava valores, mas omitia a informação solicitada.

A correção apresenta o preço de tabela confirmado antes de preparar a mensagem. Ela atua em uma pergunta atual explícita de preço com oferta única compatível, mantém a decisão `reply`, as ações válidas de cadastro/card e a validação final anterior ao envio. Se não houver outra pergunta útil, não cria um handoff apenas para terminar a resposta. Uma resposta que já informa corretamente o preço permanece intacta; repetir o gasto do cliente não conta como cotação da Doss.

Ambiguidade, SKU diferente, pedido de máquina, preço histórico e condições de frete, desconto, parcelamento ou entrega não são tratados como uma simples cotação unitária. Um handoff técnico legítimo mantém seu motivo. Os testes usam valores sintéticos; não constituem oferta comercial.

### Disponibilidade visível no CRM

O diagnóstico do Bruno já informava `model_429_credit_balance_exhausted`, mas o CRM descartava esse motivo e a conversa podia exibir “IA ativa” apenas porque a sessão estava habilitada. A nova versão separa a habilitação da sessão da disponibilidade informada pelo serviço.

| Situação observada | Apresentação na nova versão |
| --- | --- |
| Saldo esgotado explicitamente confirmado | Atendimento indisponível por créditos da API esgotados |
| `insufficient_quota`, sem confirmação específica de saldo | Limite de uso ou créditos da API |
| Timeout, erro HTTP, JSON inválido ou diagnóstico ausente | Disponibilidade não verificada; não afirma uma queda confirmada |
| Sessão pausada ou desabilitada | Mantém o motivo da pausa, mesmo com serviço disponível |
| Observação vencida ou consulta posterior com falha | Retira a indicação verde anterior |

Pausar o Inbox já habilitado continua possível durante indisponibilidade. Iniciar a automação continua exigindo disponibilidade. A ação, as permissões e o estado habilitado dos controles foram preservados; a aparência deixa de sugerir serviço saudável quando a disponibilidade não está confirmada.

A consulta rápida de sessão continua a cada cinco segundos sem esperar pelo Render. O diagnóstico é consultado separadamente a cada 30 segundos, com cache curto e compartilhamento de requisição em andamento. A tela geral reaproveita sua atualização existente de 15 segundos. A data da observação não é renovada por um acerto do cache; após 75 segundos, uma observação antiga deixa de indicar disponibilidade.

O novo modo de diagnóstico passa pela autenticação e pelo mesmo RPC de status que valida administrador configurado e organização. Usa a origem fixa do serviço, sem enviar credenciais do CRM, e retorna somente códigos e mensagens permitidos. Não chama o modelo, não assume trabalhos e não altera sessões. O resultado descreve a saúde reportada pelo worker; não comprova que uma mensagem foi gerada ou entregue.

Além dos testes do parser, cache e autorização, os componentes reais foram montados em ambiente de teste com dados sintéticos. Foram verificados pausa acessível, isolamento do cache por conta/organização, polling separado, expiração e remoção do verde após erro. Não houve interação com clientes para essa validação.

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

Essa ficha foi compactada de **7.560 para 2.864 caracteres**, preservando as 20 configurações, os valores técnicos registrados, as 60 referências de células e as cinco notas de variantes. A comparação confirmou que a fonte e o estado de aprovação foram preservados. A alteração evita o corte do conteúdo por tamanho no caminho de leitura do CRM e permite que a ficha caiba isoladamente no orçamento de contexto do leitor legado do Bruno. **A ficha continua pendente e não foi ativada como conhecimento aprovado**; a compactação não comprova que todos os leitores incluirão todas as fichas simultaneamente.

Conteúdo e fontes: [revisão técnica de tintas e Epson](bruno_ink_knowledge_review_20261118.md).

Na conferência inicial registrada nesta entrega, a automação estava assim:

- Atendimento global da caixa de entrada: `false`.
- Atendimento automático de novas entradas: `false`.
- Sessões habilitadas: **0**.

Publicação do serviço não significa ativação do atendimento automático. Esta continuação não alterou esses controles nem habilitou sessões.

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
