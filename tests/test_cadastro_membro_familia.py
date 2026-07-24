"""Membro de família pode virar conta própria ao se cadastrar no zaq.

Contexto: no plano Família o membro é adicionado em `membros` (com WhatsApp) e
recebe um código de convite pro Telegram — ele NÃO tem login web próprio. Antes,
se esse mesmo número tentasse "Criar sua conta", o cadastro travava porque o
WhatsApp já existia em `membros` (constraint unique).

Regra nova (_triagem_whatsapp_cadastro):
  • número que já é DONO de conta, ou que tem login web próprio (equipe PJ) →
    bloqueia (deve entrar, não criar outra);
  • número que é só membro de família (convite por código, sem login) →
    desvincula da família (libera o WhatsApp) e o cadastro segue → conta própria.

O teste de banco prova que, depois de desvincular (whatsapp_id=null, ativo=false),
o mesmo número entra numa conta NOVA como 'dono' sem violar o unique.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from web.portal import _triagem_whatsapp_cadastro

# 053 = módulo PJ; 072 = login web do membro (email/senha + relaxa o papel check).
_MIGRACOES = ("053_modulo_pj.sql", "072_membro_login_web.sql")


# ── parte pura: a triagem do WhatsApp ──────────────────────────────────────
def test_triagem_familia_desvincula():
    # só membro de família (papel != dono, sem email) → não bloqueia, desvincula
    rows = [(10, "membro", None)]
    bloqueia, desvincular = _triagem_whatsapp_cadastro(rows)
    assert bloqueia is False
    assert desvincular == [10]


def test_triagem_dono_bloqueia():
    # já é dono de uma conta → bloqueia, não desvincula ninguém
    rows = [(5, "dono", None)]
    bloqueia, desvincular = _triagem_whatsapp_cadastro(rows)
    assert bloqueia is True
    assert desvincular == []


def test_triagem_membro_com_login_web_bloqueia():
    # equipe PJ: papel não-dono, MAS tem login web próprio (email) → bloqueia
    rows = [(7, "vendedor", "vend@x.com")]
    bloqueia, desvincular = _triagem_whatsapp_cadastro(rows)
    assert bloqueia is True
    assert desvincular == []


def test_triagem_sem_membros_libera():
    # número novo, sem membership nenhuma → não bloqueia, nada a desvincular
    bloqueia, desvincular = _triagem_whatsapp_cadastro([])
    assert bloqueia is False
    assert desvincular == []


def test_triagem_restrito_desvincula():
    # papel 'restrito' (só lista) também é família sem login → desvincula
    bloqueia, desvincular = _triagem_whatsapp_cadastro([(9, "restrito", None)])
    assert bloqueia is False
    assert desvincular == [9]


# ── parte com banco: desvincular libera o WhatsApp (unique) ────────────────
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


def _limpar_zap(pool, zap):
    """Remove rastros de execuções anteriores (o banco de teste persiste entre
    rodadas locais; no CI é sempre limpo). Deleta membros e as contas órfãs."""
    with pool.connection() as c:
        contas = c.execute(
            "select distinct conta_id from membros where whatsapp_id=%s", (zap,)
        ).fetchall()
        c.execute("delete from membros where whatsapp_id=%s", (zap,))
        for (cid,) in contas:
            c.execute("delete from membros where conta_id=%s", (cid,))
            c.execute("delete from contas where id=%s", (cid,))
        c.commit()


def test_membro_familia_libera_whatsapp_pra_conta_propria(pool):
    zap = "5586990001122"
    _limpar_zap(pool, zap)
    with pool.connection() as c:
        # família: conta do dono + membro com o WhatsApp
        fam = c.execute(
            "insert into contas (tipo, nome, plano) values ('pf','Família Silva','zaq_familia') returning id"
        ).fetchone()[0]
        membro_id = c.execute(
            "insert into membros (conta_id, nome, papel, whatsapp_id) "
            "values (%s,'João','membro',%s) returning id", (fam, zap)
        ).fetchone()[0]
        c.commit()

        # triagem vê só o membro de família → desvincula
        rows = c.execute(
            "select id, papel, email from membros where whatsapp_id=%s", (zap,)
        ).fetchall()
    bloqueia, desvincular = _triagem_whatsapp_cadastro(rows)
    assert bloqueia is False
    assert desvincular == [membro_id]

    with pool.connection() as c:
        # o cadastro: desvincula o membro e cria a conta própria com o mesmo zap
        for mid in desvincular:
            c.execute("update membros set whatsapp_id=null, ativo=false where id=%s", (mid,))
        nova = c.execute(
            "insert into contas (tipo, nome, plano) values ('pf','João','zaq_pessoal') returning id"
        ).fetchone()[0]
        # NÃO estoura o unique de whatsapp_id — o número foi liberado
        c.execute(
            "insert into membros (conta_id, nome, papel, whatsapp_id) "
            "values (%s,'João','dono',%s)", (nova, zap))
        c.commit()

        # João agora é dono da conta nova; a antiga participação ficou inativa
        dono = c.execute(
            "select conta_id, papel from membros where whatsapp_id=%s", (zap,)
        ).fetchone()
        assert dono == (nova, "dono")
        antigo = c.execute(
            "select ativo, whatsapp_id from membros where id=%s", (membro_id,)
        ).fetchone()
        assert antigo == (False, None)   # desvinculado, histórico preservado


def test_dono_de_conta_continua_bloqueado(pool):
    zap = "5586990003344"
    _limpar_zap(pool, zap)
    with pool.connection() as c:
        conta = c.execute(
            "insert into contas (tipo, nome) values ('pf','Maria') returning id"
        ).fetchone()[0]
        c.execute(
            "insert into membros (conta_id, nome, papel, whatsapp_id) "
            "values (%s,'Maria','dono',%s)", (conta, zap))
        c.commit()
        rows = c.execute(
            "select id, papel, email from membros where whatsapp_id=%s", (zap,)
        ).fetchall()
    bloqueia, desvincular = _triagem_whatsapp_cadastro(rows)
    assert bloqueia is True
    assert desvincular == []
