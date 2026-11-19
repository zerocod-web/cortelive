"""Une as faixas de um replay DASH sem manifesto legível pelo FFmpeg."""
import http.server
import shutil
import subprocess
import sys
import tempfile
import threading


FORMATOS = {"/video": "bv[height<=1080]/bv", "/audio": "ba"}


def transmitir(yt_dlp, url, pasta):
    falhou = threading.Event()

    class Faixa(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            formato = FORMATOS.get(self.path)
            if formato is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            comando = [
                yt_dlp, "--no-warnings", "--no-progress", "--no-playlist",
                "-f", formato, "-o", "-", "--", url,
            ]
            # O yt-dlp nativo usa nomes de fragmento iguais ao escrever no stdout.
            # Cada faixa precisa de sua própria pasta para não ler fragmentos da outra.
            with tempfile.TemporaryDirectory(dir=pasta) as temporario:
                with subprocess.Popen(comando, cwd=temporario, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=sys.stderr) as fonte:
                    try:
                        shutil.copyfileobj(fonte.stdout, self.wfile, length=64 * 1024)
                    except OSError:
                        fonte.kill()
                        return
                    if fonte.wait() != 0:
                        falhou.set()

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Faixa) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            ffmpeg = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                 "-i", base + "/video", "-i", base + "/audio",
                 "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-f", "matroska", "pipe:1"],
                stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr,
            )
            return ffmpeg.returncode or int(falhou.is_set())
        finally:
            server.shutdown()


if __name__ == "__main__":
    sys.exit(transmitir(*sys.argv[1:]))
