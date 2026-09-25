"""A tela Hoje (/painel/hoje) renderizada de verdade — quem entra, o que aparece.

O motor está em test_voltar_a_chamar.py. Aqui se prova o que a pessoa vê:
 1. só a clínica entra; festa, mensalidade e corretora voltam pro /painel;
 2. nenhuma palavra de festa vaza pra tela (CLAUDE.md §6);
 3. a recepção (vendedor) não vê o card do modo, e não consegue salvá-lo;
 4. o Mandar clicado duas vezes manda uma vez só;
 5. texto com palavra de saúde não é salvo.
"""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from finance import agente
from finance import voltar_a_chamar as vac
from tests.test_voltar_a_chamar import CLINICA, _br, _modo, _paciente_recebe_preco, pool  # noqa: F401
from web import painel_hoje as ph


@pytest.fixture()
def cli(pool, monkeypatch):  # noqa: F811
    estado = {"conta": CLINICA, "nicho": "clinica"}
    monkeypatch.setattr(ph, "get_pool", lambda: pool)
    monkeypatch.setattr(ph, "conta_logada", lambda request: (estado["conta"],))
    monkeypatch.setattr(ph, "nicho_da_conta", lambda conta: estado["nicho"])
    envios = []

    def _mandar(c, conta_id, canal, destino, texto, conversa_id=None):
        envios.append(texto)
        return {"ok": True, "sid": f"sid-{len(envios)}"}
    monkeypatch.setattr(agente, "_mandar", _mandar)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste-de-sessao")
    app.include_router(ph.router)

    @app.get("/_papel/{papel}")
    def _papel(request: Request, papel: str):
        request.session["papel"] = papel
        request.session["membro_id"] = 7
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.get("/_papel/dono")
    c.estado, c.envios, c.pool = estado, envios, pool
    return c


def _com_toque_vencido(pool):  # noqa: F811
    _modo(pool, CLINICA, "sugere")
    _paciente_recebe_preco(pool)                 # segunda 21/09, 10h
    vac.rodar(pool, agora=_br(21, 10, 30))
    # a tela lê o relógio de verdade: o toque 1 vence agora e os outros depois,
    # seja que dia for quando o teste rodar
    agora = datetime.now(timezone.utc)
    with pool.connection() as c:
        c.execute("""update voltar_a_chamar_toques
                        set devido_em = %s + (toque - 1) * interval '1 day'""", (agora,))
        tid = c.execute("select id from voltar_a_chamar_toques where toque=1").fetchone()[0]
        c.commit()
    return tid


@pytest.mark.parametrize("nicho", ["eventos", "consultoria", "seguros", "hortifruti", None])
def test_outros_nichos_voltam_pro_painel(cli, nicho):
    cli.estado["nicho"] = nicho
    r = cli.get("/painel/hoje")
    assert r.status_code == 303 and r.headers["location"] == "/painel"
    assert cli.post("/painel/hoje/config", data={"modo": "ligado"}).status_code == 303
    assert cli.post("/painel/hoje/toque/1/mandar").headers["location"] == "/painel"


def test_papel_sem_vendas_volta_pro_painel(cli):
    cli.get("/_papel/financeiro")
    assert cli.get("/painel/hoje").headers["location"] == "/painel"


def test_clinica_entra_e_nao_ve_festa(cli, pool):  # noqa: F811
    tid = _com_toque_vencido(pool)
    r = cli.get("/painel/hoje")
    assert r.status_code == 200
    html = r.text
    assert "Voltar a chamar hoje" in html and "Maria" in html
    assert f"/painel/hoje/toque/{tid}/mandar" in html
    # só o miolo da tela: o `base` tem comentário de JavaScript que fala de festa
    baixo = html.split('class="hj-pag"', 1)[1].split("<script>", 1)[0].lower()
    for palavra in ("festa", "visita", "convidado", "orçamento", "orcamento"):
        assert palavra not in baixo, palavra


def test_desligado_explica_e_nao_mostra_toques(cli):
    r = cli.get("/painel/hoje")
    assert r.status_code == 200
    assert "Voltar a chamar está desligado" in r.text
    assert "Voltar a chamar hoje" not in r.text


def test_vendedor_nao_ve_nem_salva_o_modo(cli):
    cli.get("/_papel/vendedor")
    r = cli.get("/painel/hoje")
    assert r.status_code == 200 and 'action="/painel/hoje/config"' not in r.text
    r = cli.post("/painel/hoje/config", data={"modo": "ligado"})
    assert "erro=" in r.headers["location"]
    with cli.pool.connection() as c:
        assert c.execute("select count(*) from voltar_a_chamar_config").fetchone()[0] == 0


def test_dono_liga_e_grava_quando_ligou(cli):
    r = cli.post("/painel/hoje/config", data={"modo": "sugere", "teto_dia": "8"})
    assert r.headers["location"] == "/painel/hoje?aviso=salvo"
    with cli.pool.connection() as c:
        modo, teto, ligado = c.execute(
            "select modo, teto_dia, ligado_em from voltar_a_chamar_config").fetchone()
    assert (modo, teto) == ("sugere", 8) and ligado is not None
    # desligar e ligar de novo não muda a data da 1ª vez (é a linha da repescagem)
    cli.post("/painel/hoje/config", data={"modo": "off"})
    cli.post("/painel/hoje/config", data={"modo": "ligado"})
    with cli.pool.connection() as c:
        assert c.execute("select ligado_em from voltar_a_chamar_config").fetchone()[0] == ligado


def test_texto_com_saude_nao_salva(cli):
    r = cli.post("/painel/hoje/config", data={"modo": "sugere",
                                               "t1": "Oi, {nome}! Ainda quer tratar a acne?"})
    assert "erro=" in r.headers["location"]
    with cli.pool.connection() as c:
        assert c.execute("select count(*) from voltar_a_chamar_config").fetchone()[0] == 0


def test_texto_trocado_vale_no_toque(cli, pool):  # noqa: F811
    tid = _com_toque_vencido(pool)
    novo = "Oi, {nome}! Posso ver um horário para você esta semana?"
    cli.post("/painel/hoje/config", data={"modo": "sugere", "t1": novo})
    assert "Oi, Maria! Posso ver um horário" in cli.get("/painel/hoje").text
    cli.post(f"/painel/hoje/toque/{tid}/mandar")
    assert cli.envios == ["Oi, Maria! Posso ver um horário para você esta semana?"]


def test_mandar_duas_vezes_manda_uma(cli, pool):  # noqa: F811
    tid = _com_toque_vencido(pool)
    cli.get("/_papel/vendedor")
    r1 = cli.post(f"/painel/hoje/toque/{tid}/mandar")
    r2 = cli.post(f"/painel/hoje/toque/{tid}/mandar")
    assert r1.headers["location"] == "/painel/hoje?aviso=mandado"
    assert r2.headers["location"] == "/painel/hoje?aviso=ja_tratado"
    assert len(cli.envios) == 1
    with pool.connection() as c:
        assert c.execute("select membro_id, enviado_por from voltar_a_chamar_toques where id=%s",
                         (tid,)).fetchone() == (7, "recepcao")


def test_nao_e_paciente_some_da_tela(cli, pool):  # noqa: F811
    tid = _com_toque_vencido(pool)
    r = cli.post(f"/painel/hoje/toque/{tid}/nao-paciente")
    assert r.headers["location"] == "/painel/hoje?aviso=nao_paciente"
    html = cli.get("/painel/hoje?aviso=nao_paciente").text
    assert "esse número não recebe mais toque nenhum" in html
    assert f"/painel/hoje/toque/{tid}/mandar" not in html
