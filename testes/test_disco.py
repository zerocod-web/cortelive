"""Aviso de espaço em disco e limpeza da gravação bruta (cortelive/disco.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cortelive import disco  # noqa: E402

CFG = {"disco_aviso_gb": 10, "disco_critico_gb": 3}


def _livre(monkeypatch, gb, total=10000):
    monkeypatch.setattr(disco, "livre_gb", lambda pasta: gb)
    monkeypatch.setattr(disco, "total_gb", lambda pasta: total)


def test_padrao_v117_30gb_ou_10_por_cento(monkeypatch, tmp_path):
    """Print de 19/11/2026: 'Pouco espaço' com 376,9 GB livres (limiar forçado no teste). No padrão, não avisa."""
    _livre(monkeypatch, 376.9, 931)
    assert disco.avaliar(tmp_path, {})[0] == "ok"
    _livre(monkeypatch, 29, 4000)
    assert disco.avaliar(tmp_path, {})[0] == "aviso"          # menos de 30 GB
    _livre(monkeypatch, 80, 1000)
    assert disco.avaliar(tmp_path, {})[0] == "aviso"          # 8% do disco
    _livre(monkeypatch, 150, 2000)
    assert disco.avaliar(tmp_path, {})[0] == "ok"             # 7,5%, mas 150 GB não é pouco (teto 100)
    _livre(monkeypatch, 2, 931)
    assert disco.avaliar(tmp_path, {})[0] == "critico"


def test_niveis(monkeypatch, tmp_path):
    _livre(monkeypatch, 50, 200)          # 25% do disco livre
    assert disco.avaliar(tmp_path, CFG)[0] == "ok"
    _livre(monkeypatch, 8.4)
    nv, livre, frase = disco.avaliar(tmp_path, CFG)
    assert nv == "aviso" and frase == "Pouco espaço no disco: 8,4 GB livres."
    _livre(monkeypatch, 2.0)
    nv, _, frase = disco.avaliar(tmp_path, CFG)
    assert nv == "critico" and "Parei a gravação" in frase and "cortes prontos estão salvos" in frase


def test_limites_configuraveis_e_desligavel(monkeypatch, tmp_path):
    _livre(monkeypatch, 4)
    assert disco.avaliar(tmp_path, {"disco_aviso_gb": 5, "disco_critico_gb": 1})[0] == "aviso"
    assert disco.avaliar(tmp_path, {"disco_aviso_gb": 0})[0] == "ok"


def test_livre_gb_real(tmp_path):
    assert disco.livre_gb(tmp_path / "nao" / "existe") > 0


def _sessao(tmp_path):
    pasta = tmp_path / "cortes" / "2026-11-18 01h00 - canal"
    dint = pasta / "_interno"
    (dint / ".buffer").mkdir(parents=True)
    (dint / ".buffer" / "seg_000.ts").write_bytes(b"x" * 1000)
    (pasta / "sem legenda").mkdir()
    (pasta / "2026-11-18 01h05 - canal - Titulo.mp4").write_bytes(b"m" * 500)
    (pasta / "sem legenda" / "2026-11-18 01h05 - canal - Titulo.mp4").write_bytes(b"s" * 400)
    (dint / "2026-11-18 01h05 - canal - Titulo.json").write_text("{}")
    (dint / "2026-11-18 01h05 - canal - Titulo.ass").write_text("a")
    (dint / "log.txt").write_text("log")
    (dint / "sessao.json").write_text("{}")
    # temporários
    (dint / "corte_002_000200_trecho.mkv").write_bytes(b"t" * 300)
    (dint / "corte_002_000200.json").write_text("{}")
    (dint / "corte_002_000200.res").write_text("{}")
    # lixo de fora da live: nunca pode sumir
    fora = tmp_path / "cortes" / "outra live"
    fora.mkdir()
    (fora / "corte_001_000010_trecho.mkv").write_bytes(b"o")
    return pasta, dint, fora


def test_limpar_bruto_so_apaga_temporarios_da_live(tmp_path):
    pasta, dint, fora = _sessao(tmp_path)
    liberados = disco.limpar_bruto(pasta, dint)
    assert liberados >= 1000 + 300
    assert not (dint / ".buffer").exists()
    assert not list(dint.glob("corte_*"))
    for f in ["2026-11-18 01h05 - canal - Titulo.mp4", "sem legenda/2026-11-18 01h05 - canal - Titulo.mp4",
              "_interno/2026-11-18 01h05 - canal - Titulo.json", "_interno/2026-11-18 01h05 - canal - Titulo.ass",
              "_interno/log.txt", "_interno/sessao.json"]:
        assert (pasta / f).exists(), f
    assert (fora / "corte_001_000010_trecho.mkv").exists()


def test_limpar_bruto_nao_apaga_json_de_corte_pronto_com_nome_interno(tmp_path):
    pasta, dint, _ = _sessao(tmp_path)
    (pasta / "corte_003_000300.mp4").write_bytes(b"m")          # corte final que ficou com o nome interno
    (dint / "corte_003_000300.json").write_text("{}")
    disco.limpar_bruto(pasta, dint)
    assert (dint / "corte_003_000300.json").exists()


def test_limpar_bruto_recusa_interno_fora_da_pasta(tmp_path):
    pasta, dint, fora = _sessao(tmp_path)
    assert disco.limpar_bruto(pasta, fora) == 0
    assert (fora / "corte_001_000010_trecho.mkv").exists()
