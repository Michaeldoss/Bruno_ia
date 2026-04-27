import os
import asyncio
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()

async def test_openai():
    print("\n--- Testando OpenAI ---")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Erro: OPENAI_API_KEY nao encontrada no .env")
        return
    
    try:
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "olá"}],
            max_tokens=5
        )
        # Setei gpt-4o-mini como um teste bsico de conectividade
        print("Sucesso: Resposta recebida da OpenAI")
    except Exception as e:
        print(f"Erro na OpenAI: {e}")

async def test_anthropic():
    print("\n--- Testando Anthropic ---")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("Erro: ANTHROPIC_API_KEY nao encontrada no .env")
        return
    
    try:
        client = AsyncAnthropic(api_key=api_key)
        print(f"Usando chave Anthropic terminada em ...{api_key[-4:]}")
        response = await client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=5,
            messages=[{"role": "user", "content": "olá"}]
        )
        print("Sucesso: Resposta recebida da Anthropic")
    except Exception as e:
        print(f"Erro na Anthropic: {e}")

async def main():
    await test_openai()
    await test_anthropic()

if __name__ == "__main__":
    asyncio.run(main())
