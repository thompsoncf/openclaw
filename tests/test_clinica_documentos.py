"""Prontuário, fase 5: documentos (finance/clinica_documentos.py). Seção 06 do desenho."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from finance import clinica_acesso_clinico as acc
from finance import clinica_documentos as cdoc
from finance import clinica_ficha_link as cfl
from tests.test_clinica_agenda import CLINICA
from tests.test_clinica_ficha_link import banco, cli  # noqa: F401
from tests.test_clinica_pacientes import banco as _banco_pacientes  # noqa: F401
from tests.test_clinica_pacotes import pool, zap  # noqa: F401
from tests.test_clinica_prontuario import MANOEL, _liberado, tela  # noqa: F401

AGORA = datetime.now(timezone.utc)


def _emitido(c, kid, q, tipo="receita", corpo="Uso tópico:\n1. Hidratante labial, 3x/dia"):
    did = cdoc.novo(c, CLINICA, kid, q, tipo, "Lúcia Ferreira", AGORA)
    assert cdoc.salvar(c, CLINICA, kid, did, q, {"titulo": cdoc.TIPOS[tipo][0], "corpo": corpo}) is None
    assert cdoc.emitir(c, CLINICA, kid, did, q) is None
    return did


def test_emitir_nao_muda_mais_e_o_pdf_sai(banco, zap):  # noqa: F811
    kid, _eid, _prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        at = cdoc.novo(c, CLINICA, kid, q, "atestado", "Lúcia Ferreira", AGORA)
        assert "Lúcia Ferreira" in cdoc.documento(c, CLINICA, kid, at)["corpo"]
        assert "troque os ___" in cdoc.emitir(c, CLINICA, kid, at, q)       # o modelo tem lacunas
        did = _emitido(c, kid, q)
        c.commit()
        d = cdoc.documento(c, CLINICA, kid, did)
        assert d["status"] == "assinado" and cdoc.integro(d, kid) and d["conselho"] == "CRM-MA 1234"
        assert cdoc.salvar(c, CLINICA, kid, did, q, {"corpo": "outra"}) == "Documento emitido não muda: emita outro."
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("update clinica_documentos set corpo='x' where id=%s", (did,))
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("delete from clinica_documentos where id=%s", (did,))
        c.rollback()
        pdf = cdoc.pdf(c, CLINICA, kid, d)
        assert pdf[:4] == b"%PDF"
        cc_ = _emitido(c, kid, q, "receita_controle")
        import pymupdf
        assert pymupdf.open(stream=cdoc.pdf(c, CLINICA, kid, cdoc.documento(c, CLINICA, kid, cc_)),
                            filetype="pdf").page_count == 2          # as duas vias
        nt = cdoc.novo(c, CLINICA, kid, q, "notificacao", "Lúcia", AGORA)
        assert "número do talão" in cdoc.emitir(c, CLINICA, kid, nt, q)
        cdoc.salvar(c, CLINICA, kid, nt, q, {"corpo": "isotretinoína 20 mg", "numero_talao": "A123"})
        assert cdoc.emitir(c, CLINICA, kid, nt, q) is None
        assert cdoc.pdf(c, CLINICA, kid, cdoc.documento(c, CLINICA, kid, nt)) is None   # fica no talão


def test_a_recepcao_ve_o_tipo_e_manda_o_link_e_o_paciente_abre(cli, banco, zap):  # noqa: F811
    kid, _eid, _prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        did = _emitido(c, kid, q)
        c.execute("update clientes set aniversario='1990-05-17' where id=%s", (kid,))
        tok = cfl.token(c, CLINICA, kid)
        c.commit()
    cli.get("/_papel/vendedor/51")
    html = cli.get(f"/painel/clinica/pacientes/{kid}").text
    assert "Documentos emitidos" in html and "Receita" in html and "Hidratante" not in html
    r = cli.post(f"/painel/clinica/pacientes/{kid}/documentos/enviar", data={"doc": str(did)})
    assert r.status_code == 303
    assert zap.saiu and f"/ficha/{tok}" in zap.saiu[-1][1] and ".pdf" not in zap.saiu[-1][1]   # o link, nunca o arquivo
    celular = type(cli)(cli.app, follow_redirects=False)
    assert celular.get(f"/ficha/{tok}/documento/{did}.pdf").status_code == 303        # sem a data, não abre
    celular.post(f"/ficha/{tok}/entrar", data={"nascimento": "1990-05-17"})
    r = celular.get(f"/ficha/{tok}/documento/{did}.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF" and "no-store" in r.headers["cache-control"]
    assert "Documentos da consulta" in celular.get(f"/ficha/{tok}?passo=4").text
    with banco.connection() as c:                                    # depois de 30 dias, sai do link
        assert cdoc.no_link(c, CLINICA, kid, AGORA + timedelta(days=31)) == []


def test_so_o_profissional_abre_o_pdf_e_fica_no_registro(tela, banco, zap):  # noqa: F811
    kid, _eid, _prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        did = _emitido(c, kid, q)
        c.commit()
    tela.get("/_login/vendedor/51")
    assert tela.get(f"/painel/clinica/prontuario/{kid}/documento/{did}/pdf").status_code == 303
    tela.get("/_login/gestor/52")
    r = tela.get(f"/painel/clinica/prontuario/{kid}/documento/{did}/pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    with banco.connection() as c:
        o_que = [x["o_que"] for x in acc.registro(c, CLINICA, datetime.now(timezone.utc))]
    assert f"abriu o documento #{did}" in o_que


def test_o_agente_nunca_manda_documento():
    raiz = Path(__file__).resolve().parents[1]
    for arq in ("finance/agente.py", "finance/clinica_agente.py"):
        t = (raiz / arq).read_text(encoding="utf-8")
        assert "clinica_documentos" not in t, arq


def test_antes_da_432(pool, zap):  # noqa: F811
    with pool.connection() as c:
        assert cdoc.listar(c, CLINICA, 1) == [] and cdoc.emitidos_sem_conteudo(c, CLINICA, 1) == []
        assert cdoc.no_link(c, CLINICA, 1, AGORA) == []
