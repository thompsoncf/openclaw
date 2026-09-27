"""O link "complete sua ficha" (finance/clinica_ficha_link.py, finance/clinica_preconsulta.py,
/ficha/{token}) — passo 0c do prontuário, seções 12 e 13 do mockup.

Reaproveita o banco dos pacientes (tests/test_clinica_pacientes.py) e põe a 411 por cima.
"""
import re
from datetime import date, time
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from finance import clinica_agenda as ca
from finance import clinica_ficha_link as cfl
from finance import clinica_pacientes as cpa
from tests.test_clinica_agenda import AGORA, BASE, CLINICA
from tests.test_clinica_pacientes import _ficha_do_evento
from tests.test_clinica_pacientes import banco as _banco_pacientes  # noqa: F401
from tests.test_clinica_pacotes import FONE, _manoel, _paciente, _sessao, _tipo, pool, zap  # noqa: F401

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture()
def banco(_banco_pacientes):  # noqa: F811
    with _banco_pacientes.connection() as c:
        c.execute((BASE / "411_clinica_ficha_link.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "417_clinica_termos_modelos.sql").read_text(encoding="utf-8"))
        c.commit()
    return _banco_pacientes


def _ligar(c):
    assert cfl.salvar_ligado(c, CLINICA, "ligado") is None
    c.commit()


def _marcar(c, lead, dia=date(2026, 9, 28), nome=None):
    eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, "Consulta")["id"],
                           inicio=ca.utc(dia, time(9)), lead_id=lead, agora=AGORA, paciente=nome or "")
    assert erro is None, erro
    c.commit()
    return eid


def _completar(banco, kid, nasc="1990-05-17", cpf="529.982.247-25"):
    with banco.connection() as c:
        f = cfl.por_token(c, cfl.token(c, CLINICA, kid))
        c.commit()
    assert cfl.salvar_cadastro(banco, f, {"nome": "Lúcia Ferreira", "nascimento": nasc, "cpf": cpf,
                                          "cidade": "Pedreiras"}, AGORA) == (None, None)
    from finance import clinica_preconsulta as cpc
    with banco.connection() as c:
        resp, erro = cpc.validar({"queixa": "manchas no rosto", "alergia": "sim", "alergia_qual": "dipirona",
                                  "remedios": "nao", "gravidez": "nao"}, curta=False)
        assert erro is None
        cpc.salvar(c, CLINICA, kid, resp, curta=False, evento_id=None)
        assert cfl.salvar_termos(c, f, {"lgpd": "1", "imagem": "clinico", "nome": "Lúcia Ferreira"},
                                 empresa="Espaço Pelle", ip="1.2.3.4", user_agent="teste", agora=AGORA) is None
        c.commit()


def test_desligado_a_confirmacao_fica_como_era(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c)
        ev = ca.evento(c, CLINICA, _marcar(c, lead))
        assert "/ficha/" not in ca.texto_marcado(c, CLINICA, ev)
        assert "/ficha/" not in ca.texto_vespera(c, CLINICA, ev, AGORA)


def test_ligado_o_link_vai_na_confirmacao_e_na_vespera_e_some_com_a_ficha_completa(banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        lead, _conv = _paciente(c)
        eid = _marcar(c, lead)
        ev = ca.evento(c, CLINICA, eid)
        marcado = ca.texto_marcado(c, CLINICA, ev)
        vespera = ca.texto_vespera(c, CLINICA, ev, AGORA)
        c.commit()
        kid = _ficha_do_evento(c, eid)
    assert "Complete sua ficha antes da consulta" in marcado and "pela metade" in vespera
    tok = re.search(r"/ficha/(\S+)", marcado).group(1)
    assert tok in vespera                                   # o mesmo link nas duas
    assert "Responda 1 para confirmar ou 2" in vespera      # a resposta 1/2 continua valendo
    _completar(banco, kid)
    with banco.connection() as c:
        s = cfl.situacao(c, CLINICA, kid, AGORA)
        assert s["completa"] and s["pct"] == 100 and s["alergia"]
        assert "/ficha/" not in ca.texto_vespera(c, CLINICA, ev, AGORA)


def test_o_filho_no_whatsapp_da_mae_e_a_ficha_de_pedro(banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        lead, _conv = _paciente(c)
        mae = _ficha_do_evento(c, _marcar(c, lead))
        eid = _marcar(c, lead, date(2026, 9, 29), nome="Pedro Ferreira")
        pedro = _ficha_do_evento(c, eid)
        c.execute("update clientes set responsavel_id=%s where id=%s", (mae, pedro))
        c.commit()
        txt = ca.texto_marcado(c, CLINICA, ca.evento(c, CLINICA, eid))
    assert "Complete a ficha de Pedro antes da consulta" in txt


def test_menor_o_responsavel_da_o_cpf_e_aceita_os_termos(banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        lead, _conv = _paciente(c)
        pedro = _ficha_do_evento(c, _marcar(c, lead, nome="Pedro Ferreira"))
        f = cfl.por_token(c, cfl.token(c, CLINICA, pedro))
        c.commit()
    base = {"nome": "Pedro Ferreira", "nascimento": "2017-03-10", "cidade": "Pedreiras"}
    assert cfl.salvar_cadastro(banco, f, base, AGORA)[0] == "Menor de idade: escreva o nome completo do responsável."
    assert cfl.salvar_cadastro(banco, f, {**base, "resp_nome": "Lúcia Ferreira"}, AGORA)[0] == \
        "Informe o CPF do responsável (vai na nota fiscal)."
    with banco.connection() as c:                       # o erro não deixou responsável pela metade
        assert c.execute("select responsavel_id from clientes where id=%s", (pedro,)).fetchone()[0] is None
    assert cfl.salvar_cadastro(banco, f, {**base, "resp_nome": "Lúcia Ferreira", "resp_cpf": "529.982.247-25"},
                               AGORA) == (None, None)
    with banco.connection() as c:
        p = cpa.ficha(c, CLINICA, pedro, AGORA)
        assert p["responsavel"]["nome"] == "Lúcia Ferreira" and p["idade"] == 9
        s = cfl.situacao(c, CLINICA, pedro, AGORA)
        assert s["menor"] and s["cpf_ok"] and s["falta"] == ["pré-consulta", "termos"]
        assert cfl.salvar_termos(c, f, {"lgpd": "1", "imagem": "nao", "nome": "Lúcia Ferreira"},
                                 empresa="Espaço Pelle", ip="", user_agent="", agora=AGORA) is None
        c.commit()
        papel, texto = c.execute("select papel, texto from clinica_termos_aceites where cliente_id=%s and termo='lgpd'",
                                 (pedro,)).fetchone()
    assert papel == "responsavel" and "de quem sou responsável legal" in texto and "Pedro Ferreira" in texto


def test_a_lista_filtra_ficha_incompleta_e_a_agenda_mostra_a_ficha(banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        lead, _conv = _paciente(c)
        eid = _marcar(c, lead)
        d = cpa.listar(c, CLINICA, AGORA, filtro="ficha_incompleta")
        assert d["total"] == 1 and d["pacientes"][0]["ficha_txt"].startswith("ficha ")
        dia = ca.dia(c, CLINICA, date(2026, 9, 28), AGORA)
        evs = [e for col in dia["colunas"] for cel in col["celulas"] if cel.get("tipo") == "ev" for e in cel["evs"]]
        assert evs and evs[0]["id"] == eid and evs[0]["ficha"]["pct"] < 100
        kid = _ficha_do_evento(c, eid)
    _completar(banco, kid)
    with banco.connection() as c:
        assert cpa.listar(c, CLINICA, AGORA, filtro="ficha_incompleta")["total"] == 0


# ------------------------------------------------------------------ as telas

@pytest.fixture()
def cli(banco, monkeypatch):  # noqa: F811
    from web import ficha_publica as fp
    from web import painel_clinica_agenda as pa
    from web import painel_clinica_pacientes as pw
    from web import painel_clinica_termos as pt
    for m in (fp, pa, pw, pt):
        monkeypatch.setattr(m, "get_pool", lambda: banco)
    monkeypatch.setattr(pa, "conta_logada", lambda request: (CLINICA,))
    monkeypatch.setattr(pa, "nicho_da_conta", lambda conta: "clinica")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    for m in (fp, pa, pw, pt):
        app.include_router(m.router)

    @app.get("/_papel/{papel}/{membro}")
    def _papel(request: Request, papel: str, membro: int):
        request.session["papel"] = papel
        request.session["membro_id"] = membro
        return {}
    return TestClient(app, follow_redirects=False)


def test_desligado_o_link_nao_abre(cli, banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c)
        tok = cfl.token(c, CLINICA, _ficha_do_evento(c, _marcar(c, lead)))
        c.commit()
    assert cli.get(f"/ficha/{tok}").status_code == 404


def test_o_link_abre_com_a_data_e_trava_depois_de_5_erros(cli, banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        lead, _conv = _paciente(c)
        kid = _ficha_do_evento(c, _marcar(c, lead))
        c.execute("update clientes set aniversario='1990-05-17' where id=%s", (kid,))
        tok = cfl.token(c, CLINICA, kid)
        c.commit()
    assert cli.get("/ficha/nao-existe").status_code == 404
    html = cli.get(f"/ficha/{tok}").text
    assert "confirme a data de nascimento" in html and "Ferreira" not in html   # nada do paciente antes da data
    for _ in range(4):
        r = cli.post(f"/ficha/{tok}/entrar", data={"nascimento": "1990-05-18"})
        assert r.headers["location"] == f"/ficha/{tok}"               # o erro vai na sessão, não na URL
        assert "não confere" in cli.get(f"/ficha/{tok}").text
    cli.post(f"/ficha/{tok}/entrar", data={"nascimento": "1990-05-18"})
    assert "Muitas tentativas" in cli.get(f"/ficha/{tok}").text
    cli.post(f"/ficha/{tok}/entrar", data={"nascimento": "1990-05-17"})
    assert "Muitas tentativas" in cli.get(f"/ficha/{tok}").text     # travada vale até pra data certa
    with banco.connection() as c:                                   # passou a trava: mais 5 erros travam por 1h
        c.execute("update clientes set ficha_travada_ate = now() - interval '1 minute' where id=%s", (kid,))
        c.commit()
    for _ in range(5):
        cli.post(f"/ficha/{tok}/entrar", data={"nascimento": "1990-05-18"})
    with banco.connection() as c:
        ate = c.execute("select ficha_travada_ate - now() from clientes where id=%s", (kid,)).fetchone()[0]
    assert ate.total_seconds() > 50 * 60


def test_o_paciente_preenche_os_tres_passos(cli, banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        lead, _conv = _paciente(c)
        kid = _ficha_do_evento(c, _marcar(c, lead))
        c.execute("update clientes set aniversario='1990-05-17' where id=%s", (kid,))
        tok = cfl.token(c, CLINICA, kid)
        c.commit()
    r = cli.post(f"/ficha/{tok}/entrar", data={"nascimento": "17/05/1990"})
    assert r.headers["location"] == f"/ficha/{tok}"
    html = cli.get(f"/ficha/{tok}").text
    assert "Passo 1 de 4" in html and "Lúcia Ferreira" in html
    r = cli.post(f"/ficha/{tok}/cadastro", data={"nome": "Lúcia Ferreira", "nascimento": "1990-05-17",
                                                 "cpf": "529.982.247-25", "cidade": "Pedreiras", "uf": "ma"})
    assert r.headers["location"].endswith("passo=2")
    r = cli.post(f"/ficha/{tok}/preconsulta", data={"queixa": "manchas", "alergia": "nao", "remedios": "nao",
                                                    "gravidez": "na"})
    assert r.headers["location"].endswith("passo=3")
    r = cli.post(f"/ficha/{tok}/termos", data={"lgpd": "1", "imagem": "divulgacao", "nome": "Lúcia Ferreira"},
                 headers={"user-agent": "celular"})
    assert r.headers["location"].endswith("passo=4")
    assert "Ficha pronta" in cli.get(f"/ficha/{tok}").text
    with banco.connection() as c:
        assert cfl.situacao(c, CLINICA, kid, AGORA)["completa"]
        ua, opcao = c.execute("select user_agent, opcao from clinica_termos_aceites where cliente_id=%s "
                              "and termo='imagem'", (kid,)).fetchone()
    assert ua == "celular" and opcao == "divulgacao"


def test_sem_a_data_o_link_nao_mostra_nem_troca_o_que_a_recepcao_guardou(cli, banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        lead, _conv = _paciente(c)
        kid = _ficha_do_evento(c, _marcar(c, lead))
        c.commit()
    from finance import clientes as cli_
    cli_.atualizar_cliente(banco, CLINICA, kid, endereco="Rua Secreta, 12", cidade="Bacabal", cpf="52998224725")
    with banco.connection() as c:
        tok = cfl.token(c, CLINICA, kid)
        c.commit()
    html = cli.get(f"/ficha/{tok}").text
    assert "Passo 1 de 4" in html and "Rua Secreta" not in html and "52998224725" not in html
    # pular pro passo 2 sem ter dado a data não adianta
    assert cli.post(f"/ficha/{tok}/preconsulta", data={"queixa": "x"}).headers["location"] == f"/ficha/{tok}"
    # quem tem o link dá uma data, outro nome, outro CPF, outra cidade: só o vazio é preenchido
    r = cli.post(f"/ficha/{tok}/cadastro", data={"nome": "Fulano de Tal", "nascimento": "1980-01-01",
                                                 "cpf": "111.444.777-35", "cidade": "Codó", "endereco": "Rua X",
                                                 "cep": "65700-000"})
    assert r.headers["location"].endswith("passo=2")
    with banco.connection() as c:
        p = cpa.ficha(c, CLINICA, kid, AGORA)
    assert p["nome"] == "Lúcia Ferreira" and p["cpf"] == "52998224725" and p["cidade"] == "Bacabal"
    assert p["endereco"] == "Rua Secreta, 12" and p["cep"] == "65700000" and p["nascimento"] == date(1980, 1, 1)
    html = cli.get(f"/ficha/{tok}?passo=1").text                    # e continua sem ver o guardado
    assert "Rua Secreta" not in html and "52998224725" not in html and "Bacabal" not in html


def test_so_o_profissional_le_a_pre_consulta(cli, banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        lead, _conv = _paciente(c)
        eid = _marcar(c, lead)
        kid = _ficha_do_evento(c, eid)
        c.execute("insert into membros (id, conta_id, nome, papel) values (52,39,'Dr. Manoel','gestor')")
        c.execute("update clinica_profissionais set membro_id=52, acesso='propria', conselho='CRM-MA 1234' "
                  "where id=%s", (_manoel(c),))
        c.commit()
    _completar(banco, kid)
    cli.get("/_papel/vendedor/51")                                  # a recepção
    html = cli.get(f"/painel/clinica/pacientes/{kid}?aba=pre").text
    assert "manchas no rosto" not in html and "dipirona" not in html and "informou alergia" in html
    assert "manchas no rosto" not in cli.get(f"/painel/clinica/agenda/evento/{eid}").text
    cli.get("/_papel/gestor/52")                                    # o Dr. Manoel
    html = cli.get(f"/painel/clinica/pacientes/{kid}?aba=pre").text
    assert "manchas no rosto" in html and "dipirona" in html
    assert "dipirona" in cli.get(f"/painel/clinica/agenda/evento/{eid}").text


def test_na_chegada_o_agendamento_avisa_que_falta_o_cpf(cli, banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c)
        eid = _marcar(c, lead)
        for s in ("confirmado", "presente"):
            assert ca.mudar_situacao(c, CLINICA, eid, s) is None
        c.commit()
    cli.get("/_papel/vendedor/51")
    assert "Falta o CPF (vai na nota fiscal)" in cli.get(f"/painel/clinica/agenda/evento/{eid}").text


def test_o_agente_nunca_le_a_pre_consulta():
    """Seção 01 do mockup: o agente do WhatsApp nunca lê conteúdo clínico. A tabela só é
    lida em finance/clinica_preconsulta.py, e as respostas só saem por `ultima`, chamada
    só onde `pode_ler` foi conferido."""
    for arq in ("finance/agente.py", "finance/clinica_agente.py"):
        assert "preconsulta" not in (RAIZ / arq).read_text(encoding="utf-8"), arq
    fora = [str(p.relative_to(RAIZ)) for pasta in ("finance", "web", "contas", "db")
            for p in (RAIZ / pasta).rglob("*.py")
            if "clinica_preconsultas" in p.read_text(encoding="utf-8") and p.name != "clinica_preconsulta.py"]
    assert fora == [], fora
    for p in (RAIZ / "web").rglob("*.py"):
        t = p.read_text(encoding="utf-8")
        if re.search(r"cpc\.ultima\(", t):
            assert "pode_ler(" in t, p.name


def test_cpf_de_outra_conta_nao_mostra_o_nome_de_ninguem(banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        c.execute("insert into pessoas (nome, cpf) values ('Cliente de Outra Loja', '52998224725')")
        lead, _conv = _paciente(c)
        kid = _ficha_do_evento(c, _marcar(c, lead))
        f = cfl.por_token(c, cfl.token(c, CLINICA, kid))
        c.commit()
        assert cpa.cpf_de_outra_ficha(c, CLINICA, None, "52998224725") == \
            "Este CPF já está cadastrado em outra ficha do Zaq."
    erro, aviso = cfl.salvar_cadastro(banco, f, {"nome": "Lúcia Ferreira", "nascimento": "1990-05-17",
                                                 "cpf": "529.982.247-25", "cidade": "Pedreiras"}, AGORA, False)
    assert erro is None and "Outra Loja" not in aviso and "clínica confere" in aviso   # não trava o paciente
    with banco.connection() as c:
        p = cpa.ficha(c, CLINICA, kid, AGORA)
    assert p["cidade"] == "Pedreiras" and p["cpf"] == ""


def test_o_irmao_de_nome_parecido_nao_vira_o_responsavel(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c)
        ana = cpa.achar_ou_criar(c, CLINICA, lead, "Ana", FONE)            # a irmã, com um nome só
        c.execute("update clientes set aniversario='2012-01-01' where id=%s", (ana,))
        pedro = _ficha_do_evento(c, _marcar(c, lead, nome="Pedro Ferreira"))
        f = cfl.por_token(c, cfl.token(c, CLINICA, pedro))
        c.commit()
    assert cfl.salvar_cadastro(banco, f, {"nome": "Pedro Ferreira", "nascimento": "2017-03-10", "cidade": "Pedreiras",
                                          "resp_nome": "Ana Maria Souza", "resp_cpf": "529.982.247-25"},
                               AGORA) == (None, None)
    with banco.connection() as c:
        p = cpa.ficha(c, CLINICA, pedro, AGORA)
        cpf_ana = c.execute("select p.cpf from clientes k join pessoas p on p.id = k.pessoa_id where k.id=%s",
                            (ana,)).fetchone()[0]
    assert p["responsavel"]["nome"] == "Ana Maria Souza" and p["responsavel"]["id"] != ana and cpf_ana is None


def test_no_retorno_a_alergia_e_perguntada_e_vira_aviso(banco, zap):  # noqa: F811
    from finance import clinica_preconsulta as cpc
    with banco.connection() as c:
        _ligar(c)
        lead, _conv = _paciente(c)
        eid = _marcar(c, lead)
        kid = _ficha_do_evento(c, eid)
        # veio antes, SEM pré-consulta nenhuma: o retorno ainda pede a completa
        for st in ("confirmado", "presente", "atendimento", "finalizado"):
            assert ca.mudar_situacao(c, CLINICA, eid, st, tratamento="nao") is None
        c.commit()
        assert not cfl.situacao(c, CLINICA, kid, AGORA)["retorno"]
        resp, _e = cpc.validar({"queixa": "manchas", "alergia": "nao", "remedios": "nao", "gravidez": "na"}, False)
        cpc.salvar(c, CLINICA, kid, resp, curta=False, evento_id=None)
        c.commit()
        assert cfl.situacao(c, CLINICA, kid, AGORA)["retorno"]              # agora a curta
        assert [k for k, _t, _x in cpc.perguntas(True)][0] == "alergia"
        resp, erro = cpc.validar({"alergia": "sim", "alergia_qual": "dipirona", "mudou": "nao"}, True)
        assert erro is None
        cpc.salvar(c, CLINICA, kid, resp, curta=True, evento_id=None)
        c.commit()
        assert cfl.situacao(c, CLINICA, kid, AGORA)["alergia"]


def test_antes_da_411_as_telas_nao_quebram(pool, zap, monkeypatch):  # noqa: F811
    """Sem a migração 411: nada de 500 na agenda, no agendamento nem na confirmação."""
    from finance import clinica_preconsulta as cpc
    with pool.connection() as c:
        assert cfl.ligado(c, CLINICA) is False
        assert cfl.salvar_ligado(c, CLINICA, "off") is None
        assert cpc.ultima(c, CLINICA, 1) is None and cpc.resumo(c, CLINICA, [1]) == {}
        assert cfl.linha_da_mensagem(c, CLINICA, {"id": 1}, "marcado") == ""
