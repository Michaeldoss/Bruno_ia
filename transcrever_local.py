import sys
import os
import time

def transcrever_pasta(pasta, modelo="small"):
    if not os.path.exists(pasta):
        print("ERRO: Pasta nao encontrada: " + pasta)
        sys.exit(1)

    extensoes = (".opus", ".ogg", ".mp3", ".m4a", ".wav", ".mp4")
    arquivos = sorted([
        f for f in os.listdir(pasta)
        if f.lower().endswith(extensoes)
    ])

    if not arquivos:
        print("Nenhum audio encontrado em: " + pasta)
        sys.exit(0)

    print("")
    print("=" * 60)
    print("  DOSS GROUP - Transcricao em Lote")
    print("=" * 60)
    print("  Arquivos encontrados: " + str(len(arquivos)))
    print("  Modelo Whisper: " + modelo)
    print("=" * 60)
    print("")
    print("Carregando modelo... (pode demorar na primeira vez)")

    import whisper
    model = whisper.load_model(modelo)
    print("Modelo carregado. Iniciando transcricoes...")
    print("")

    saida = os.path.join(pasta, "transcricoes.txt")
    erros = 0
    transcritos = 0
    total = len(arquivos)

    with open(saida, "w", encoding="utf-8") as f:
        f.write("TRANSCRICOES - DOSS GROUP\n")
        f.write("Total de arquivos: " + str(total) + "\n")
        f.write("=" * 60 + "\n\n")

        for i, nome_arquivo in enumerate(arquivos, 1):
            caminho = os.path.join(pasta, nome_arquivo)
            print("[" + str(i) + "/" + str(total) + "] " + nome_arquivo)

            inicio = time.time()
            try:
                result = model.transcribe(caminho, language="pt")
                texto = result["text"].strip()
                duracao = time.time() - inicio

                f.write("--- AUDIO: " + nome_arquivo + " ---\n")
                f.write(texto + "\n\n")
                f.flush()

                transcritos += 1
                preview = texto[:80] + ("..." if len(texto) > 80 else "")
                print("  OK (" + str(round(duracao, 1)) + "s): " + preview)
                print("")

            except Exception as e:
                erros += 1
                print("  ERRO: " + str(e))
                print("")
                f.write("--- AUDIO: " + nome_arquivo + " ---\n")
                f.write("[ERRO: " + str(e) + "]\n\n")

    print("=" * 60)
    print("  CONCLUIDO!")
    print("  Transcritos: " + str(transcritos) + "/" + str(total))
    print("  Erros: " + str(erros))
    print("  Arquivo salvo: transcricoes.txt na pasta dos audios")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("USO: python transcrever_local.py caminho_da_pasta")
        sys.exit(1)

    pasta = sys.argv[1]
    modelo = sys.argv[2] if len(sys.argv) > 2 else "small"
    transcrever_pasta(pasta, modelo)
