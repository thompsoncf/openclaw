"""O cron diário (scripts/cron_diario.py), 27/09/2026.

O cron de Virginia morreu todo dia de 21/09 a 27/09: o comando antigo importava
`web.app` só pra pegar o banco, e o painel se recusa a subir no Render sem
`PORTAL_SECRET` (#740) — variável que o cron não tem, e não deve ter.

O que este teste protege:
  * **o cron não carrega o painel** — nem importando o script no ambiente exato
    do incidente (RENDER setado, sem PORTAL_SECRET);
  * **um passo quebrado não impede o outro**, e quebra vira saída 1 (é o que faz o
    Render mandar o e-mail de falha);
  * **resumo não enviado não é quebra** — só aviso no log, como era antes.
"""
import os
import subprocess
import sys
from pathlib import Path

from scripts import cron_diario

RAIZ = Path(__file__).resolve().parents[1]


def test_o_cron_roda_sem_o_segredo_do_painel_e_sem_carregar_o_painel():
    env = {k: v for k, v in os.environ.items() if k != "PORTAL_SECRET"}
    env["RENDER"] = "true"
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys, scripts.cron_diario; "
         "print('web.app' in sys.modules)"],
        cwd=RAIZ, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "False", "o cron voltou a depender do painel"


def _fingir(monkeypatch, *, alerta, faxina):
    import finance.notificar as notificar
    import finance.observabilidade as obs
    chamadas = []

    def _alerta(pool, sempre=False):
        chamadas.append(("alerta", pool, sempre))
        return alerta()

    def _faxina(pool, dias=30):
        chamadas.append(("faxina", pool, dias))
        return faxina()

    monkeypatch.setattr(notificar, "alerta_fase_b", _alerta)
    monkeypatch.setattr(obs, "expurgar_antigos", _faxina)
    return chamadas


def test_roda_os_dois_passos_com_o_mesmo_banco(monkeypatch):
    chamadas = _fingir(monkeypatch, alerta=lambda: True, faxina=lambda: 3)
    assert cron_diario.rodar(pool="POOL") is True
    assert chamadas == [("alerta", "POOL", True), ("faxina", "POOL", 30)]


def test_o_resumo_quebrado_nao_impede_a_faxina_e_vira_falha(monkeypatch):
    def _quebra():
        raise RuntimeError("banco fora")
    chamadas = _fingir(monkeypatch, alerta=_quebra, faxina=lambda: 0)
    assert cron_diario.rodar(pool="POOL") is False
    assert [c[0] for c in chamadas] == ["alerta", "faxina"]


def test_a_faxina_quebrada_vira_falha(monkeypatch):
    def _quebra():
        raise RuntimeError("banco fora")
    _fingir(monkeypatch, alerta=lambda: True, faxina=_quebra)
    assert cron_diario.rodar(pool="POOL") is False


def test_resumo_nao_enviado_nao_e_quebra(monkeypatch, caplog):
    _fingir(monkeypatch, alerta=lambda: False, faxina=lambda: 0)
    with caplog.at_level("WARNING", logger="openclaw.cron_diario"):
        assert cron_diario.rodar(pool="POOL") is True
    assert "NÃO enviado" in caplog.text
