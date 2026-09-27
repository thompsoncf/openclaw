"""Prontuário, fase 1: quem vê o quê (finance/clinica_acesso_clinico.py, migração 419).

O conteúdo clínico só abre pra profissional de saúde que o DONO liberou; toda leitura
fica no registro de acesso, que não se altera nem se apaga; o suporte do Zaq e o agente
do WhatsApp nunca leem.
"""
import re
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from finance import clinica_acesso_clinico as acc
from tests.test_clinica_agenda import CLINICA
from tests.test_clinica_ficha_link import _completar, _ligar, _marcar, banco  # noqa: F401
from tests.test_clinica_pacientes import _ficha_do_evento
from tests.test_clinica_pacientes import banco as _banco_pacientes  # noqa: F401
from tests.test_clinica_pacotes import _manoel, _paciente, pool, zap  # noqa: F401

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture()
def tela(banco, monkeypatch):  # noqa: F811
    from web import painel_clinica as pc
    from web import painel_clinica_agenda as pa
    from web import painel_clinica_pacientes as pw
    from web import painel_clinica_registro as pr
    for m in (pc, pa, pw, pr):
        monkeypatch.setattr(m, "get_pool", lambda: banco)
    for m in (pc, pa):
        monkeypatch.setattr(m, "conta_logada", lambda request: (CLINICA,))
        monkeypatch.setattr(m, "nicho_da_conta", lambda conta: "clinica")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    for m in (pc, pa, pw, pr):
        app.include_router(m.router)

    @app.get("/_login/{papel}/{membro}/{suporte}")
    def _login(request: Request, papel: str, membro: int, suporte: int):
        request.session.clear()
        request.session["papel"] = papel
        if membro:
            request.session["membro_id"] = membro
        if suporte:
            request.session["suporte_acesso_id"] = suporte
        return {}
    return TestClient(app, follow_redirects=False)


def _preparar(banco):
    """A Lúcia com a ficha completa (e a pré-consulta), o Dr. Manoel com login (membro 52)
    e conselho — ainda SEM o prontuário liberado."""
    with banco.connection() as c:
        _ligar(c)
        lead, _conv = _paciente(c)
        eid = _marcar(c, lead)
        kid = _ficha_do_evento(c, eid)
        c.execute("insert into membros (id, conta_id, nome, papel) values (52,39,'Dr. Manoel','gestor')")
        prof = _manoel(c)
        c.execute("update clinica_profissionais set membro_id=52, acesso='propria', conselho='CRM-MA 1234' "
                  "where id=%s", (prof,))
        c.commit()
    _completar(banco, kid)
    return kid, eid, prof


def test_so_le_quem_o_dono_liberou_e_tudo_fica_no_registro(tela, banco, zap):  # noqa: F811
    kid, eid, prof = _preparar(banco)
    tela.get("/_login/gestor/52/0")                       # o Dr. Manoel, gestor, com conselho
    assert "manchas no rosto" not in tela.get(f"/painel/clinica/pacientes/{kid}?aba=pre").text
    # o gestor não se libera
    r = tela.post(f"/painel/clinica/profissional/{prof}/prontuario", data={"acesso": "1"})
    assert r.status_code == 303
    with banco.connection() as c:
        assert acc.leitor(c, CLINICA, {"papel": "gestor", "membro_id": 52}) is None
    tela.get("/_login/dono/0/0")                          # o dono libera
    r = tela.post(f"/painel/clinica/profissional/{prof}/prontuario", data={"acesso": "1"})
    assert r.status_code == 303
    tela.get("/_login/gestor/52/0")
    assert "manchas no rosto" in tela.get(f"/painel/clinica/pacientes/{kid}?aba=pre").text
    assert "dipirona" in tela.get(f"/painel/clinica/agenda/evento/{eid}").text
    with banco.connection() as c:
        linhas = acc.registro(c, CLINICA, acc_agora())
        o_que = [x["o_que"] for x in linhas]
    assert "pré-consulta" in o_que and "pré-consulta (pelo agendamento)" in o_que
    assert any(x.startswith("liberou o prontuário de") for x in o_que)
    assert all("manchas" not in x and "dipirona" not in x for x in o_que)          # nunca o conteúdo


def acc_agora():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def test_trocar_o_login_ou_o_conselho_desliga(tela, banco, zap):  # noqa: F811
    kid, _eid, prof = _preparar(banco)
    with banco.connection() as c:
        assert acc.liberar(c, CLINICA, prof, acesso=True, e_dono=False, por="Dono") is None
        c.commit()
        assert acc.leitor(c, CLINICA, {"papel": "gestor", "membro_id": 52})
        from finance import clinica_config as cc
        p = next(x for x in cc.listar_profissionais(c, CLINICA) if x["id"] == prof)
        assert cc.salvar_profissional(c, CLINICA, id=prof, nome=p["nome"], funcao=p["funcao"], conselho="CRM-MA 9999",
                                      acesso="propria", membro_id=52, tipos=p["tipos"]) is None
        c.commit()
        assert acc.leitor(c, CLINICA, {"papel": "gestor", "membro_id": 52}) is None


def test_o_medico_que_e_o_dono_le_com_o_login_do_dono_e_o_suporte_nunca(tela, banco, zap):  # noqa: F811
    kid, _eid, prof = _preparar(banco)
    with banco.connection() as c:
        assert acc.leitor(c, CLINICA, {"papel": "dono"}) is None
        assert acc.liberar(c, CLINICA, prof, acesso=True, e_dono=True, por="Dono") is None
        c.commit()
        assert acc.leitor(c, CLINICA, {"papel": "dono"})["profissional_id"] == prof
        assert acc.leitor(c, CLINICA, {"papel": "dono", "suporte_acesso_id": 7}) is None
    tela.get("/_login/dono/0/7")                          # o suporte "entrando como" dono
    assert "manchas no rosto" not in tela.get(f"/painel/clinica/pacientes/{kid}?aba=pre").text
    r = tela.post(f"/painel/clinica/profissional/{prof}/prontuario", data={"acesso": ""})
    with banco.connection() as c:
        assert acc.leitor(c, CLINICA, {"papel": "dono"})             # o suporte não mexeu
    tela.get("/_login/dono/0/0")
    assert "manchas no rosto" in tela.get(f"/painel/clinica/pacientes/{kid}?aba=pre").text
    assert r.status_code == 303


def test_sem_conselho_ou_sem_login_nao_libera(banco, zap):  # noqa: F811
    with banco.connection() as c:
        prof = _manoel(c)
        c.execute("update clinica_profissionais set conselho=null where id=%s", (prof,))
        assert "conselho" in acc.liberar(c, CLINICA, prof, acesso=True, e_dono=False, por="Dono")
        c.execute("update clinica_profissionais set conselho='CRM 1', membro_id=null, acesso='sem_login' "
                  "where id=%s", (prof,))
        assert "login" in acc.liberar(c, CLINICA, prof, acesso=True, e_dono=False, por="Dono")
        assert acc.liberar(c, CLINICA, prof, acesso=True, e_dono=True, por="Dono") is None   # o dono entra como dono


def test_o_registro_nao_se_altera_nem_se_apaga(banco, zap):  # noqa: F811
    import psycopg
    with banco.connection() as c:
        acc.registrar(c, CLINICA, {"nome": "Dr. Manoel", "profissional_id": 1}, None, "pré-consulta")
        c.commit()
        for sql in ("update clinica_acessos set quem='outro'", "delete from clinica_acessos"):
            with pytest.raises(psycopg.errors.RaiseException):
                c.execute(sql)
            c.rollback()
        assert c.execute("select count(*) from clinica_acessos").fetchone()[0] == 1


def test_quem_ve_o_registro(tela, banco, zap):  # noqa: F811
    kid, _eid, prof = _preparar(banco)
    with banco.connection() as c:
        acc.registrar(c, CLINICA, {"nome": "Dr. Manoel", "profissional_id": prof}, kid, "pré-consulta")
        c.commit()
    tela.get("/_login/vendedor/51/0")                     # a recepção: não
    assert tela.get("/painel/clinica/registro").status_code == 303
    # a sessão diz "gestor", mas o membro 51 é da recepção no banco: vale o banco
    tela.get("/_login/gestor/51/0")
    assert tela.get("/painel/clinica/registro").status_code == 303
    tela.get("/_login/gestor/52/0")                       # o gestor de verdade: tudo, sem o conteúdo
    html = tela.get(f"/painel/clinica/registro?paciente={kid}").text
    assert "Dr. Manoel" in html and "pré-consulta" in html and "Lúcia Ferreira" in html
    assert "manchas" not in html


def test_antes_da_419_ninguem_le(pool, zap):  # noqa: F811
    with pool.connection() as c:
        assert acc.leitor(c, CLINICA, {"papel": "dono"}) is None
        assert acc.estado(c, CLINICA) == {} and acc.registro(c, CLINICA, acc_agora()) == []


def test_a_liberacao_e_do_login_e_do_membro_ativo(tela, banco, zap):  # noqa: F811
    """O gestor que liga o login DELE ao cadastro do médico não herda a liberação; o
    médico desligado da equipe para de ler na hora."""
    kid, _eid, prof = _preparar(banco)
    with banco.connection() as c:
        c.execute("insert into membros (id, conta_id, nome, papel) values (53,39,'Gestor','gestor')")
        assert acc.liberar(c, CLINICA, prof, acesso=True, e_dono=False, por="Dono") is None
        c.commit()
        assert acc.leitor(c, CLINICA, {"papel": "gestor", "membro_id": 52})
        # troca direto no banco (sem passar pelo reset da tela): nem assim o 53 lê
        c.execute("update clinica_profissionais set membro_id=53 where id=%s", (prof,))
        c.commit()
        assert acc.leitor(c, CLINICA, {"papel": "gestor", "membro_id": 53}) is None
        c.execute("update clinica_profissionais set membro_id=52 where id=%s", (prof,))
        c.execute("update membros set ativo=false where id=52")
        c.commit()
        assert acc.leitor(c, CLINICA, {"papel": "gestor", "membro_id": 52}) is None       # desligado
        assert acc.pode_ver_registro(c, CLINICA, {"papel": "gestor", "membro_id": 52}) == (False, None)


def test_o_cadastro_do_dono_nao_abre_pelo_login_de_membro(banco, zap):  # noqa: F811
    _kid, _eid, prof = _preparar(banco)
    with banco.connection() as c:
        assert acc.liberar(c, CLINICA, prof, acesso=True, e_dono=True, por="Dono") is None
        c.commit()
        assert acc.leitor(c, CLINICA, {"papel": "dono"})
        assert acc.leitor(c, CLINICA, {"papel": "gestor", "membro_id": 52}) is None    # o 52 ainda ligado


def test_so_registra_quando_ha_o_que_ler(tela, banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        lead, _conv = _paciente(c)
        eid = _marcar(c, lead)                     # sem pré-consulta respondida
        c.execute("insert into membros (id, conta_id, nome, papel) values (52,39,'Dr. Manoel','gestor')")
        prof = _manoel(c)
        c.execute("update clinica_profissionais set membro_id=52, acesso='propria', conselho='CRM 1' where id=%s",
                  (prof,))
        assert acc.liberar(c, CLINICA, prof, acesso=True, e_dono=False, por="Dono") is None
        c.commit()
    tela.get("/_login/gestor/52/0")
    for _ in range(3):
        tela.get(f"/painel/clinica/agenda/evento/{eid}")
    with banco.connection() as c:
        o_que = [x["o_que"] for x in acc.registro(c, CLINICA, acc_agora())]
    assert not [x for x in o_que if x.startswith("pré-consulta")]
    assert any("login: Dr. Manoel" in x for x in o_que)          # quem foi liberado, pelo login


def test_o_agente_nunca_chega_no_conteudo_clinico():
    """Seção 01: o módulo do agente não tem acesso às tabelas clínicas."""
    for arq in ("finance/agente.py", "finance/clinica_agente.py"):
        t = (RAIZ / arq).read_text(encoding="utf-8")
        for proibido in ("clinica_acesso_clinico", "clinica_acessos", "preconsulta", "clinica_preconsultas"):
            assert proibido not in t, (arq, proibido)
    # toda leitura do conteúdo clínico passa pelo portão, que registra — FUNÇÃO por função,
    # e ninguém importa a leitura direto (from ... import ultima)
    import ast
    for pasta in ("web", "finance", "contas"):
        for p in (RAIZ / pasta).rglob("*.py"):
            if p.name == "clinica_preconsulta.py":
                continue
            t = p.read_text(encoding="utf-8")
            assert not re.search(r"from\s+finance\.clinica_preconsulta\s+import", t), p.name
            if "ultima(" not in t:
                continue
            for no in ast.walk(ast.parse(t)):
                if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    corpo = ast.get_source_segment(t, no) or ""
                    if re.search(r"cpc\.ultima\(", corpo):
                        assert ".ler(c" in corpo, (p.name, no.name)
