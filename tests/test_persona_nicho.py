"""Sistema molda ao CNAE/nicho: contabilidade reconhecida + persona por nicho.

- CNAE de contabilidade -> ramo Contabilidade -> nicho 'contabilidade'.
- bloco_persona_pj injeta o molde do nicho (o "sentimento") e reenquadra a folha
  quando a empresa é um escritório contábil.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import cnpj_info, nichos
from finance.tools_pj import bloco_persona_pj

_MIGRACOES = ("053_modulo_pj.sql", "031_fornecedor_fase0.sql",
              "090_nicho_contabilidade_e_cnae.sql",
              "091_nichos_servico_profissional.sql")


# ── parte pura ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("desc", [
    "Atividades de contabilidade",
    "Atividades de contabilidade, exceto auditoria",
    "Serviços contábeis",
    "Escritório de contabilidade",
])
def test_cnae_contabilidade_vira_nicho(desc):
    assert cnpj_info.classificar_ramo(desc) == "Contabilidade"
    assert cnpj_info.nicho_do_cnae(desc) == "contabilidade"


def test_ti_vira_tecnologia_nao_consultoria_nem_contabilidade():
    # o "limpar": TI/software vai pra 'tecnologia', separado da consultoria de
    # gestão (e nunca contabilidade). Consultoria de gestão continua 'consultoria'.
    assert cnpj_info.nicho_do_cnae("Consultoria em tecnologia da informação") == "tecnologia"
    assert cnpj_info.nicho_do_cnae("Consultoria em gestão empresarial") == "consultoria"


def test_persona_do_nicho():
    assert nichos.tem_persona("contabilidade")
    assert not nichos.tem_persona("alimentacao")   # sem molde -> genérico
    assert "CONTADOR" in nichos.persona_do_nicho("contabilidade")


def test_rotulo_receber_por_nicho():
    assert nichos.rotulo_receber("contabilidade") == "Honorários"
    assert nichos.rotulo_receber("alimentacao") == "Fiado"        # varejo
    assert nichos.rotulo_receber("advocacia") == "Honorários"
    assert nichos.rotulo_receber("agencia") == "Mensalidades"
    assert nichos.rotulo_receber("servicos_gerais") == "A receber"  # serviço sem molde


@pytest.mark.parametrize("cnae,nicho", [
    ("Atividades jurídicas", "advocacia"),
    ("Serviços advocatícios", "advocacia"),
    ("Atividades de consultoria em gestão empresarial", "consultoria"),
    ("Serviços de arquitetura", "arquitetura"),
    ("Serviços de engenharia", "arquitetura"),
    ("Agências de publicidade", "agencia"),
    ("Gestão de mídias sociais", "agencia"),
    ("Desenvolvimento de programas de computador", "tecnologia"),
    ("Suporte técnico em informática", "tecnologia"),
])
def test_cnae_ramos_servico(cnae, nicho):
    assert cnpj_info.nicho_do_cnae(cnae) == nicho


def test_todos_os_ramos_servico_tem_molde():
    for s in ("advocacia", "consultoria", "arquitetura", "agencia", "tecnologia"):
        assert nichos.tem_persona(s), s


# ── parte com banco: bloco_persona_pj molda ao nicho ──────────────────────
@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


def _conta_com_nicho(pool, slug: str | None):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj','Teste') returning id"
        ).fetchone()[0]
        if slug:
            nid = c.execute("select id from nichos where slug=%s", (slug,)).fetchone()
            c.execute("update contas set nicho_id=%s where id=%s", (nid[0], cid))
        c.commit()
    return cid


def test_persona_molda_para_contabilidade(pool):
    cid = _conta_com_nicho(pool, "contabilidade")
    txt = bloco_persona_pj(pool, cid, "Contabilidade do Paulo")
    # o molde do nicho entrou
    assert "ESCRITÓRIO DE CONTABILIDADE" in txt
    assert "HONORÁRIOS" in txt
    # a linha genérica "folha oficial é do contador" NÃO aparece (ele é o contador)
    assert "a folha oficial" not in txt


def test_persona_molda_para_advocacia(pool):
    cid = _conta_com_nicho(pool, "advocacia")
    txt = bloco_persona_pj(pool, cid, "Advocacia Silva")
    assert "ESCRITÓRIO DE ADVOCACIA" in txt
    assert "CARTEIRA" in txt   # reaproveita o framework da carteira
    # advogado NÃO é contador: a linha genérica da folha (contador cuida) continua
    assert "eSocial" in txt


def test_persona_generica_sem_nicho(pool):
    cid = _conta_com_nicho(pool, None)
    txt = bloco_persona_pj(pool, cid, "Loja Genérica")
    # sem molde de nicho: não tem o bloco de contabilidade...
    assert "ESCRITÓRIO DE CONTABILIDADE" not in txt
    # ...e mantém a regra genérica da folha
    assert "eSocial" in txt
