import sys
import os
import time

import imageio_ffmpeg
import os
os.environ["PATH"] += os.pathsep + os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())

def transcrever_tudo(pasta_raiz, modelo="small"):
    if not os.path.exists(pasta_raiz):
        print("ERRO: Pasta nao encontrada: " + pasta_raiz)
        sys.exit(1)

    extensoes = (".opus", ".ogg", ".mp3", ".m4a", ".wav", ".mp4")

    # Descobre todas as subpastas com audios
    pastas_com_audio = []
    for item in sorted(os.listdir(pasta_raiz)):
        caminho_pasta = os.path.join(pasta_raiz, item)
        if os.path.isdir(caminho_pasta):
            audios = [f for f in os.listdir(caminho_pasta) if f.lower().endswith(extensoes)]
            if audios:
                pastas_com_audio.append((item, caminho_pasta, audios))

    if not pastas_com_audio:
        # Tenta a propria pasta raiz
        audios = [f for f in os.listdir(pasta_raiz) if f.lower().endswith(extensoes)]
        if audios:
            pastas_com_audio.append((os.path.basename(pasta_raiz), pasta_raiz, audios))

    if not pastas_com_audio:
        print("Nenhum audio encontrado em: " + pasta_raiz)
        sys.exit(0)

    total_pastas = len(pastas_com_audio)
    total_audios = sum(len(a) for _, _, a in pastas_com_audio)

    print("")
    print("=" * 60)
    print("  DOSS GROUP - Transcricao Completa")
    print("=" * 60)
    print("  Conversas encontradas: " + str(total_pastas))
    print("  Total de audios: " + str(total_audios))
    print("  Modelo Whisper: " + modelo)
    print("=" * 60)
    print("")
    print("Carregando modelo Whisper '" + modelo + "'...")
    print("(Na primeira vez baixa ~460MB — aguarde)")
    print("")

    import whisper
    model = whisper.load_model(modelo)
    print("Modelo carregado!")
    print("")

    # Arquivo de saida na pasta raiz
    saida = os.path.join(pasta_raiz, "transcricoes_completo.txt")
    total_transcritos = 0
    total_erros = 0
    inicio_geral = time.time()

    with open(saida, "w", encoding="utf-8") as f:
        f.write("TRANSCRICOES COMPLETAS - DOSS GROUP\n")
        f.write("Total de conversas: " + str(total_pastas) + "\n")
        f.write("Total de audios: " + str(total_audios) + "\n")
        f.write("=" * 60 + "\n\n")

        for idx_pasta, (nome_cliente, caminho_pasta, audios) in enumerate(pastas_com_audio, 1):
            print("")
            print(">>> CLIENTE " + str(idx_pasta) + "/" + str(total_pastas) + ": " + nome_cliente)
            print("    " + str(len(audios)) + " audios")
            print("")

            f.write("\n")
            f.write("*" * 60 + "\n")
            f.write("CLIENTE: " + nome_cliente + "\n")
            f.write("Audios: " + str(len(audios)) + "\n")
            f.write("*" * 60 + "\n\n")

            audios_ordenados = sorted(audios)

            for idx_audio, nome_audio in enumerate(audios_ordenados, 1):
                caminho_audio = os.path.join(caminho_pasta, nome_audio)
                print("  [" + str(idx_audio) + "/" + str(len(audios)) + "] " + nome_audio)

                inicio = time.time()
                try:
                    result = model.transcribe(caminho_audio, language="pt")
                    texto = result["text"].strip()
                    duracao = time.time() - inicio

                    f.write("--- " + nome_audio + " ---\n")
                    f.write(texto + "\n\n")
                    f.flush()

                    total_transcritos += 1
                    preview = texto[:70] + ("..." if len(texto) > 70 else "")
                    print("    OK (" + str(round(duracao, 1)) + "s): " + preview)

                except Exception as e:
                    total_erros += 1
                    print("    ERRO: " + str(e))
                    f.write("--- " + nome_audio + " ---\n")
                    f.write("[ERRO: " + str(e) + "]\n\n")

    duracao_total = time.time() - inicio_geral
    minutos = int(duracao_total // 60)
    segundos = int(duracao_total % 60)

    print("")
    print("=" * 60)
    print("  CONCLUIDO!")
    print("  Conversas: " + str(total_pastas))
    print("  Transcritos: " + str(total_transcritos) + "/" + str(total_audios))
    print("  Erros: " + str(total_erros))
    print("  Tempo total: " + str(minutos) + "m " + str(segundos) + "s")
    print("  Arquivo salvo: transcricoes_completo.txt")
    print("=" * 60)
    print("")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("")
        print("USO: python transcrever_tudo.py caminho_pasta_raiz")
        print("")
        print("Exemplo:")
        print('  python transcrever_tudo.py "C:/Users/DELL/Downloads/audio_doss_michael"')
        print("")
        sys.exit(1)

    pasta_raiz = sys.argv[1]
    modelo = sys.argv[2] if len(sys.argv) > 2 else "small"
    transcrever_tudo(pasta_raiz, modelo)
