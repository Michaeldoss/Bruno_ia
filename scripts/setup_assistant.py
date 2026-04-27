import os
import time
from dotenv import load_dotenv
from openai import OpenAI
import glob

# Carrega var ambiente manualmente para script independente
load_dotenv(".env")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# O Prompt do Bruno está em app.core.prompt
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.prompt import SYSTEM_PROMPT

def setup():
    print("Iniciando setup do Assistente Bruno via OpenAI Assistants API...")
    
    # 1. Cria Vector Store
    print("Criando Vector Store (Base de Conhecimento)...")
    vector_store = client.beta.vector_stores.create(name="DOSS Base de Conhecimento")
    
    # 2. Faz Upload dos arquivos da pasta docs/
    doc_paths = glob.glob(os.path.join("docs", "*.*"))
    if not doc_paths:
        print("Nenhum arquivo encontrado na pasta docs/. Coloque os PDFs e DOCXs lá.")
        return

    print(f"Encontrados {len(doc_paths)} documentos. Iniciando upload...")
    
    file_streams = [open(path, "rb") for path in doc_paths]
    
    # Faz o batch upload e aguarda o processamento
    file_batch = client.beta.vector_stores.file_batches.upload_and_poll(
        vector_store_id=vector_store.id, files=file_streams
    )
    
    # Fecha os arquivos físicos
    for f in file_streams:
        f.close()
        
    print(f"Status do Lote: {file_batch.status}")
    print(f"Arquivos processados com sucesso: {file_batch.file_counts.completed}")
    if file_batch.file_counts.failed > 0:
        print(f"Falha em {file_batch.file_counts.failed} arquivos.")

    # 3. Cria o Assistant e atrela o Vector Store
    print("Criando Agente Bruno na OpenAI...")
    assistant = client.beta.assistants.create(
        name="Bruno SDR - Doss Group",
        instructions=SYSTEM_PROMPT,
        model="gpt-4o",
        tools=[{"type": "file_search"}],
        tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}},
        temperature=0.3
    )
    
    print(f"Assistente criado com Sucesso! ID: {assistant.id}")
    
    # 4. Salva o ASSISTANT_ID no .env para o sistema usar depois
    with open(".env", "a", encoding="utf-8") as env_file:
        env_file.write(f"\nOPENAI_ASSISTANT_ID={assistant.id}\n")
        
    print("OPENAI_ASSISTANT_ID salvo no .env! Tudo pronto para rodar.")

if __name__ == "__main__":
    setup()
