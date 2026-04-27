import os
import time
import glob
from pathlib import Path
from dotenv import set_key

# Modifica o sys.path para garantir que consiga importar do 'app'
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from openai import OpenAI
from app.config import get_settings
from app.core.prompt import SYSTEM_PROMPT

def main():
    settings = get_settings()
    
    if settings.OPENAI_API_KEY == "stub" or not settings.OPENAI_API_KEY:
        print("Erro: A chave OPENAI_API_KEY não foi encontrada no .env ou é inválida.")
        return

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    
    docs_dir = Path("docs")
    if not docs_dir.exists() or not docs_dir.is_dir():
        print("Erro: O diretório '{}' não existe.".format(docs_dir.absolute()))
        return
        
    # Pega todos os arquivos doc e pdf
    file_paths = []
    for ext in ("*.pdf", "*.docx", "*.csv"):
        file_paths.extend(docs_dir.glob(ext))
        
    if not file_paths:
        print("Nenhum documento encontrado na pasta 'docs/'.")
        return

    print("Iniciando a criação do Vector Store e upload da Base de Conhecimento...")
    print(f"Arquivos a enviar: {[f.name for f in file_paths]}")

    # Cria o repositório Vetorial
    vector_store = client.beta.vector_stores.create(name="DOSS Knowledge Base")
    print(f"Vector Store criado com ID: {vector_store.id}")

    # Prepara as as streams dos arquivos
    file_streams = [open(path, "rb") for path in file_paths]
    
    # Upload e anexa ao vector store
    print("Fazendo o upload dos arquivos (ISSO PODE LEVAR ALGUNS MINUTOS dependendo do tamanho)...")
    file_batch = client.beta.vector_stores.file_batches.upload_and_poll(
        vector_store_id=vector_store.id, files=file_streams
    )
    
    for fs in file_streams:
        fs.close()
        
    print(f"Status do Lote de Arquivos: {file_batch.status}")
    print(f"Contagem de arquivos: {file_batch.file_counts}")

    print("Criando o Assistente Bruno G2...")
    assistant = client.beta.assistants.create(
        name="Bruno SDR 2.0 (DOSS)",
        instructions=SYSTEM_PROMPT,
        model="gpt-4o",
        tools=[{"type": "file_search"}],
        tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}},
        temperature=0.7 # Menos criativo, mais focado nos fatos e catálogos
    )
    
    print(f"Assistente Criado com Sucesso! ID: {assistant.id}")
    
    # Salvando no .env
    env_path = ".env"
    set_key(env_path, "OPENAI_ASSISTANT_ID", assistant.id)
    print(f"Salvo a OPENAI_ASSISTANT_ID ({assistant.id}) no arquivo .env!")
    print("Concluído.")

if __name__ == "__main__":
    main()
