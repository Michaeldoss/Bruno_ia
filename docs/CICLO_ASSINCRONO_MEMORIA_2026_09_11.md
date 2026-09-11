# Continuidade do ciclo assíncrono da memória

O worker diário chamava asyncio.run separadamente para cada organização e a cada noite. Essa função encerra o event loop ao terminar. O AsyncAnthropic de crm_memory_service é global e mantém seu cliente/conexões entre chamadas; reutilizá-lo em loops sucessivos cria risco de uso de recursos associados a um loop já encerrado.

Agora asyncio.run envolve a vida inteira do worker: esperas usam asyncio.sleep e cada revisão é aguardada no mesmo loop. Mantém execução sequencial por organização, 19h Brasília, limites existentes e tratamento de falhas por organização. Não dispara análise ao iniciar, não cria tentativas extras nem altera banco/canais. O worker legado de crm_memory_service não é iniciado pelo aplicativo atual.

Sintaxe e chamadores conferidos. Sem suíte, mensagens ou execução paga. Essa correção elimina o padrão de loops descartáveis, mas não comprova a causa histórica da ausência de análises; ainda são necessários os logs ou diagnóstico autenticado de produção. Reinícios antes da janela, configuração e erros do provedor continuam possíveis.

Referência: https://docs.python.org/3.11/library/asyncio-runner.html
