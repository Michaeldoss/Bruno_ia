# Credenciais do n8n

O workflow exportado não deve conter chaves reais dentro de nodes `Code`.

## Mover para Credentials/variáveis

- `SUPABASE_SERVICE_ROLE_KEY`
- `EVOLUTION_API_KEY`
- `UNIPLUS_CLIENT_ID`
- `UNIPLUS_CLIENT_SECRET`
- URLs privadas quando aplicável

## Regra

O node de código deve ler os valores do ambiente/credencial configurada no n8n. Nunca publicar ou exportar o workflow com segredos embutidos.

## Operação

1. Rotacionar os valores que já foram expostos em exportações ou capturas.
2. Criar as credenciais no n8n.
3. Referenciar os valores nos nodes HTTP Request ou por variáveis permitidas no ambiente.
4. Testar no workflow de cópia antes de publicar.
5. Manter o workflow atual ativo até a cópia revisada ser validada.
