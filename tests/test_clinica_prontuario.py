"""Prontuário, fase 2: a ficha clínica e a evolução (finance/clinica_prontuario.py,
/painel/clinica/prontuario). Seções 02, 03 e 04 do desenho.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from finance import clinica_acesso_clinico as acc
from finance import clinica_agenda as ca
from finance import clinica_prontuario as prt
from tests.test_clinica_acesso_clinico import _preparar
from tests.test_clinica_agenda import CLINICA
from tests.test_clinica_ficha_link import _completar, _ligar, _marcar, banco  # noqa: F401
from tests.test_clinica_pacientes import banco as _banco_pacientes  # noqa: F401
from tests.test_clinica_pacotes import pool, zap  # noqa: F401

RAIZ = Path(__file__).resolve().parents[1]
AGORA = datetime.now(timezone.utc)
MANOEL = {"papel": "gestor", "membro_id": 52}


@pytest.fixture()
def tela(banco, monkeypatch):  # noqa: F811
    from web import painel_clinica_agenda as pa
    from web import painel_clinica_pacientes as pw
    from web import painel_clinica_prontuario as pp
    for m in (pa, pw, pp):
        monkeypatch.setattr(m, "get_pool", lambda: banco)
    monkeypatch.setattr(pa, "conta_logada", lambda request: (CLINICA,))
    monkeypatch.setattr(pa, "nicho_da_conta", lambda conta: "clinica")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    for m in (pa, pw, pp):
        app.include_router(m.router)

    @app.get("/_login/{papel}/{membro}")
    def _login(request: Request, papel: str, membro: int):
        request.session.clear()
        request.session["papel"] = papel
        if membro:
            request.session["membro_id"] = membro
        return {}
    return TestClient(app, follow_redirects=False)


def _liberado(banco):
    kid, eid, prof = _preparar(banco)
    with banco.connection() as c:
        assert acc.liberar(c, CLINICA, prof, acesso=True, e_dono=False, por="Dono") is None
        c.commit()
    return kid, eid, prof


def test_evolucao_rascunho_assina_e_nao_muda_mais(banco, zap):  # noqa: F811
    kid, eid, _prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        evo = prt.nova(c, CLINICA, kid, q, "dermatologia", eid)
        assert prt.nova(c, CLINICA, kid, q, "dermatologia", eid) == evo          # o mesmo rascunho do atendimento
        assert prt.salvar(c, CLINICA, kid, evo, q, {"queixa": "manchas", "exame": "melasma malar",
                                                    "conduta": "fotoproteção", "cid": "L81.1",
                                                    "retorno_dias": "30"}) is None
        assert prt.assinar(c, CLINICA, kid, evo, q, AGORA) is None
        c.commit()
        e = prt.evolucao(c, CLINICA, kid, evo)
        assert e["status"] == "assinado" and e["conselho"] == "CRM-MA 1234" and len(e["hash"]) == 64
        assert prt.integra(c, CLINICA, kid, evo)
        assert prt.salvar(c, CLINICA, kid, evo, q, {"queixa": "outra"}) == \
            "Evolução assinada não muda: escreva um adendo."
        # nem direto no banco
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("update clinica_evolucoes set campos='{}' where id=%s", (evo,))
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("delete from clinica_evolucoes where id=%s", (evo,))
        c.rollback()
        # o retorno da evolução vira o padrão da agenda
        assert prt.retorno_da_evolucao(c, CLINICA, eid) == 30


def test_o_adendo_corrige_e_a_adulteracao_aparece(banco, zap):  # noqa: F811
    kid, eid, _prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        evo = prt.nova(c, CLINICA, kid, q, "livre", eid)
        prt.salvar(c, CLINICA, kid, evo, q, {"texto": "Aplicado peeling."})
        prt.assinar(c, CLINICA, kid, evo, q, AGORA)
        assert prt.adendo(c, CLINICA, kid, evo, q, "Corrigindo: peeling de ácido glicólico 35%.", AGORA) is None
        c.commit()
        h = prt.historia(c, CLINICA, kid, q)
        assert len(h) == 1 and h[0]["adendos"][0]["campos"]["texto"].startswith("Corrigindo")
        # alguém mexe no banco por baixo (desligando a trava): o hash denuncia
        c.execute("alter table clinica_evolucoes disable trigger clinica_evolucoes_assinada")
        c.execute("""update clinica_evolucoes set campos='{"texto": "outra coisa"}' where id=%s""", (evo,))
        c.execute("alter table clinica_evolucoes enable trigger clinica_evolucoes_assinada")
        c.commit()
        assert not prt.integra(c, CLINICA, kid, evo)


def test_rascunho_so_o_autor_ve(banco, zap):  # noqa: F811
    kid, eid, _prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        prt.nova(c, CLINICA, kid, q, "livre", eid)
        c.commit()
        assert prt.historia(c, CLINICA, kid, q)[0]["meu_rascunho"]
        assert prt.historia(c, CLINICA, kid, {"profissional_id": q["profissional_id"] + 999}) == []


def test_ficha_clinica_versoes_e_o_aviso_de_alergia(banco, zap):  # noqa: F811
    kid, _eid, _prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        prt.salvar_ficha_clinica(c, CLINICA, kid, q, {"alergias": "", "medicamentos": "isotretinoína 20 mg"})
        prt.salvar_ficha_clinica(c, CLINICA, kid, q, {"alergias": "penicilina", "medicamentos": "isotretinoína 20 mg"})
        c.commit()
        assert prt.ficha_clinica(c, CLINICA, kid)["alergias"] == "penicilina"
        assert c.execute("select count(*) from clinica_ficha_clinica where cliente_id=%s", (kid,)).fetchone()[0] == 2
        assert prt.tem_alergia(c, CLINICA, [kid]) == {kid}
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("delete from clinica_ficha_clinica")
        c.rollback()


def test_as_telas_e_quem_abre(tela, banco, zap):  # noqa: F811
    kid, eid, _prof = _liberado(banco)
    with banco.connection() as c:
        for st in ("confirmado", "presente"):
            assert ca.mudar_situacao(c, CLINICA, eid, st) is None
        c.commit()
    tela.get("/_login/vendedor/51")                        # a recepção: volta pra ficha
    assert tela.get(f"/painel/clinica/prontuario/{kid}").headers["location"] == f"/painel/clinica/pacientes/{kid}"
    assert "Abrir prontuário" not in tela.get(f"/painel/clinica/agenda/evento/{eid}").text
    tela.get("/_login/gestor/52")                          # o Dr. Manoel, que atende o horário
    assert "Abrir prontuário" in tela.get(f"/painel/clinica/agenda/evento/{eid}").text
    html = tela.get(f"/painel/clinica/prontuario/{kid}?evento={eid}").text
    assert "Nova evolução" in html and "Pré-consulta" in html and "dipirona" in html
    r = tela.post(f"/painel/clinica/prontuario/{kid}/ficha", data={"levar_alergia": "1"})
    assert r.status_code == 303
    assert "ALERGIA: dipirona" in tela.get(f"/painel/clinica/prontuario/{kid}").text
    r = tela.post(f"/painel/clinica/prontuario/{kid}/evolucao/nova", data={"modelo": "dermatologia", "evento": eid})
    evo = int(r.headers["location"].rsplit("/", 1)[1])
    j = tela.post(f"/painel/clinica/prontuario/{kid}/evolucao/{evo}/salvar",
                  data={"auto": "1", "queixa": "manchas", "retorno_dias": "30"}).json()
    assert j["ok"]
    assert "está em rascunho" in tela.get(f"/painel/clinica/agenda/evento/{eid}").text
    tela.post(f"/painel/clinica/prontuario/{kid}/evolucao/{evo}/assinar",
              data={"modelo": "dermatologia", "queixa": "manchas", "conduta": "fotoproteção", "retorno_dias": "30"})
    html = tela.get(f"/painel/clinica/prontuario/{kid}").text
    assert "assinado · íntegro" in html and "fotoproteção" in html
    ev_html = tela.get(f"/painel/clinica/agenda/evento/{eid}").text
    assert "está em rascunho" not in ev_html and 'value="30"' in ev_html   # o retorno da evolução
    with banco.connection() as c:
        o_que = [x["o_que"] for x in acc.registro(c, CLINICA, datetime.now(timezone.utc))]
    assert "prontuário" in o_que and "assinou uma evolução" in o_que and "atualizou a ficha clínica" in o_que


def test_a_recepcao_ve_so_o_aviso_de_alergia(tela, banco, zap):  # noqa: F811
    kid, _eid, _prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        prt.salvar_ficha_clinica(c, CLINICA, kid, q, {"alergias": "látex"})
        c.commit()
    tela.get("/_login/vendedor/51")
    html = tela.get(f"/painel/clinica/pacientes/{kid}").text
    assert "⚠ alergia" in html and "látex" not in html


def test_o_agente_nunca_le_o_prontuario():
    """Nada no caminho do agente e do link público importa o prontuário (o aviso de
    alergia sai do banco como sim/não, sem o texto)."""
    for arq in ("finance/agente.py", "finance/clinica_agente.py", "finance/clinica_agenda.py",
                "finance/clinica_ficha_link.py", "finance/clinica_pacientes.py", "web/ficha_publica.py"):
        t = (RAIZ / arq).read_text(encoding="utf-8")
        assert not re.search(r"import\s+clinica_prontuario|clinica_prontuario\s+as|from\s+finance\.clinica_prontuario", t), arq
        assert "clinica_evolucoes" not in t, arq
    # toda TELA (GET) que mostra o prontuário passa pelo portão, que registra — rota por rota
    import ast
    for p in (RAIZ / "web").rglob("*.py"):
        t = p.read_text(encoding="utf-8")
        if "prt." not in t:
            continue
        for no in ast.walk(ast.parse(t)):
            if isinstance(no, ast.FunctionDef) and any(".get(" in (ast.get_source_segment(t, d) or "")
                                                       for d in no.decorator_list):
                corpo = ast.get_source_segment(t, no) or ""
                if re.search(r"prt\.(historia|evolucao|ficha_clinica)\(", corpo):
                    assert "acc.ler(" in corpo, (p.name, no.name)


def test_antes_da_423(pool, zap):  # noqa: F811
    with pool.connection() as c:
        assert prt.tem_alergia(c, CLINICA, [1]) == set()
        assert prt.rascunho_do_evento(c, CLINICA, 1) is None and prt.retorno_da_evolucao(c, CLINICA, 1) is None


def test_trocar_o_modelo_nao_perde_o_texto_e_o_rascunho_se_descarta(tela, banco, zap):  # noqa: F811
    kid, eid, _prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        evo = prt.nova(c, CLINICA, kid, q, "dermatologia", eid)
        prt.salvar(c, CLINICA, kid, evo, q, {"queixa": "manchas", "exame": "melasma"})
        assert prt.salvar(c, CLINICA, kid, evo, q, {"modelo": "livre", "queixa": "manchas", "exame": "melasma"}) is None
        e = prt.evolucao(c, CLINICA, kid, evo)
        assert e["modelo"] == "livre" and "Queixa e história: manchas" in e["campos"]["texto"]
        assert "Exame dermatológico: melasma" in e["campos"]["texto"]
        # sem atendimento, o "Começar" de novo volta o rascunho vazio, não cria outro
        vazio = prt.nova(c, CLINICA, kid, q, "livre")
        assert prt.nova(c, CLINICA, kid, q, "livre") == vazio
        assert prt.descartar(c, CLINICA, kid, vazio, q)
        prt.salvar(c, CLINICA, kid, evo, q, {"texto": "ok"})
        prt.assinar(c, CLINICA, kid, evo, q, AGORA)
        assert not prt.descartar(c, CLINICA, kid, evo, q)          # assinado não se descarta
        c.commit()


def test_abrir_o_rascunho_fica_no_registro_e_o_retorno_depois_de_finalizar(tela, banco, zap):  # noqa: F811
    kid, eid, _prof = _liberado(banco)
    with banco.connection() as c:
        for st in ("confirmado", "presente", "atendimento"):
            assert ca.mudar_situacao(c, CLINICA, eid, st) is None
        q = acc.leitor(c, CLINICA, MANOEL)
        evo = prt.nova(c, CLINICA, kid, q, "livre", eid)
        prt.salvar(c, CLINICA, kid, evo, q, {"texto": "Peeling.", "retorno_dias": "20"})
        # a recepção finaliza ANTES da assinatura, sem pedir retorno
        assert ca.mudar_situacao(c, CLINICA, eid, "finalizado", tratamento="nao") is None
        c.commit()
    tela.get("/_login/gestor/52")
    assert "Peeling." in tela.get(f"/painel/clinica/prontuario/{kid}/evolucao/{evo}").text
    tela.post(f"/painel/clinica/prontuario/{kid}/evolucao/{evo}/assinar", data={"texto": "Peeling.", "retorno_dias": "20"})
    with banco.connection() as c:
        o_que = [x["o_que"] for x in acc.registro(c, CLINICA, datetime.now(timezone.utc))]
        assert "evolução (rascunho)" in o_que
        r = c.execute("select vence_em from clinica_retornos where evento_id=%s", (eid,)).fetchone()
        card = c.execute("""select p.status from prospeccao p
                             join eventos_agenda e on e.prospeccao_id = p.id where e.id=%s""", (eid,)).fetchone()[0]
    assert r is not None                                           # o retorno da evolução foi pra agenda
    assert card == "retorno"                # e o card, que tinha ido pra Concluído, acompanha a fila


def test_a_evolucao_so_liga_ao_atendimento_do_proprio_profissional(banco, zap):  # noqa: F811
    kid, eid, prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        outro = dict(q, profissional_id=prof + 999, cadastros=[prof + 999])
        evo = prt.nova(c, CLINICA, kid, outro, "livre", eid)
        assert prt.evolucao(c, CLINICA, kid, evo)["evento_id"] is None
        c.rollback()
