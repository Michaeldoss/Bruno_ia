import os
import csv
from openai import OpenAI
from dotenv import load_dotenv
import time

# Configurações de caminhos
ROOT_DIR = r"C:\Users\DELL\Downloads\audio_doss_michael"
OUTPUT_FILE = os.path.join(ROOT_DIR, "transcricoes_completas.txt")
LOG_FILE = os.path.join(ROOT_DIR, "processados_log.csv")

def load_processed_files():
    """Carrega a lista de arquivos já processados para não repetir trabalho."""
    if not os.path.exists(LOG_FILE):
        return set()
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        return set(line[0] for line in reader)

def log_processed_file(filepath):
    """Salva o caminho do arquivo processado no log."""
    with open(LOG_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([filepath])

def main():
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        print("Erro: OPENAI_API_KEY não encontrada no arquivo .env")
        return

    client = OpenAI(api_key=api_key)
    processed_files = load_processed_files()

    # Encontrar todos os arquivos .opus recursivamente
    all_audio_files = []
    print("Mapeando arquivos na pasta (isso pode levar um momento)...")
    for root, dirs, files in os.walk(ROOT_DIR):
        for file in files:
            if file.lower().endswith(".opus"):
                all_audio_files.append(os.path.join(root, file))

    total_files = len(all_audio_files)
    files_to_process = [f for f in all_audio_files if f not in processed_files]
    to_process_count = len(files_to_process)

    print(f"Total de áudios encontrados: {total_files}")
    print(f"Áudios já processados anteriormente: {len(processed_files)}")
    print(f"Áudios novos para processar: {to_process_count}\n")

    if to_process_count == 0:
        print("Todos os arquivos já foram processados!")
        return

    # Abre o arquivo de saída no modo append
    with open(OUTPUT_FILE, "a", encoding="utf-8") as out_f:
        for index, filepath in enumerate(files_to_process):
            filename = os.path.basename(filepath)
            relative_path = os.path.relpath(filepath, ROOT_DIR)
            remaining = to_process_count - (index + 1)
            
            print(f"[{index + 1}/{to_process_count}] Processando: {relative_path} (Faltam {remaining})...")
            
            try:
                # Transcrição via Whisper
                with open(filepath, "rb") as audio_file:
                    transcript = client.audio.transcriptions.create(
                        model="whisper-1", 
                        file=audio_file
                    )
                
                # Grava no arquivo final
                out_f.write(f"\n--- ARQUIVO: {relative_path} ---\n")
                out_f.write(f"{transcript.text}\n")
                out_f.write("-" * 50 + "\n")
                out_f.flush()

                # Marca como processado
                log_processed_file(filepath)
                
                # Pequena pausa para evitar stress na API se necessário (opcional)
                # time.sleep(0.1)
                
            except Exception as e:
                print(f"  --> Erro em {filename}: {e}")
                # Espera um pouco em caso de erro de conexão/rate limit antes de tentar o próximo
                if "rate_limit" in str(e).lower():
                    print("Aguardando 30 segundos devido a limite de taxa...")
                    time.sleep(30)
                continue

    print("-" * 30)
    print(f"Processo finalizado ou interrompido. Resultados em:\n{OUTPUT_FILE}")

if __name__ == "__main__":
    main()
