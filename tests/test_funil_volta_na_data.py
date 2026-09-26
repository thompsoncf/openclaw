"""O lead perdido que volta sozinho na data que ele deu (finance/funil_perda.py,
`voltar_os_vencidos`; migração 373). Desenho: docs/mockups/nicho_construcao.html,
seção 08 — "restrição no CPF volta sozinho".

Os testes que mais importam:

`test_volta_uma_vez_so` — o ticker roda a cada 2 minutos em 2 workers; o lead
não pode voltar duas vezes nem ganhar dois follow-ups.

`test_o_historico_da_perda_fica` — o motivo e a data da perda são o que o dono
analisa depois; voltar não apaga.
"""
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import funil_perda as fp

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 21
HOJE = date(2026, 12, 1)

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text, contato text,
  status text default 'novo', estagio text default 'lead', vendedor_id bigint,
  perda_motivo text, temperatura text, proximo_contato_em timestamptz,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table funil_movimentos (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint, de text, para text, motivo text, membro_id bigint,
  criado_em timestamptz default now());
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table funil_etapas (id bigserial primary key, semeado_de text, conta_id bigint,
  chave text, rotulo text, ordem int default 0);
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text check (tipo in ('ligacao','whatsapp','email','reuniao','visita',
  'nota','bounce','engajamento')), resultado text check (resultado in ('sem_resposta',
  'retornar','interessado','sem_interesse','agendado','fechado')), descricao text,
  criado_em timestamptz default now());
create table follow_up_marcacoes (id bigserial primary key, conta_id bigint not null,
  prospeccao_id bigint not null, prazo_em timestamptz not null, acao text not null default '',
  membro_id bigint, automatico boolean not null default false, motivo text not null default '',
  criado_em timestamptz not null default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_volta_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        for m in ("235_motivos_de_perda_da_conta.sql", "236_reativar_o_lead_que_volta.sql",
                  "254_funil_semeado_de.sql", "373_perda_volta_em.sql"):
            c.execute((MIG / m).read_text(encoding="utf-8"))
        for ch, o in (("novo", 0), ("contatado", 10), ("follow_up", 20), ("perdido", 910)):
            c.execute("insert into funil_etapas (conta_id, chave, rotulo, ordem) values (%s,%s,%s,%s)",
                      (CONTA, ch, ch.capitalize(), o))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def limpo(pool):
    with pool.connection() as c:
        for t in ("prospeccao", "funil_movimentos", "prospeccao_atividades", "follow_up_marcacoes"):
            c.execute(f"delete from {t}")
        c.commit()


def _perdido(pool, volta_em, motivo="restricao_cpf", conta=CONTA):
    with pool.connection() as c:
        lid = c.execute("""insert into prospeccao (conta_id, contato, status, perda_motivo,
                                                   perda_em, perda_etapa)
                           values (%s,'João','perdido',%s,%s,'contatado') returning id""",
                        (conta, motivo, datetime(2026, 9, 26, tzinfo=timezone.utc))).fetchone()[0]
        fp.marcar_volta(c, conta, lid, volta_em)
        c.commit()
    return lid


def _lead(pool, lid):
    with pool.connection() as c:
        return dict(zip(("status", "perda_motivo", "perda_em", "perda_volta_em", "proximo"),
                        c.execute("""select status, perda_motivo, perda_em, perda_volta_em,
                                            proximo_contato_em from prospeccao where id=%s""",
                                  (lid,)).fetchone()))


def test_volta_no_dia_pro_follow_up(pool, limpo):
    lid = _perdido(pool, HOJE)
    assert fp.voltar_os_vencidos(pool, HOJE) == 1
    l = _lead(pool, lid)
    assert l["status"] == "follow_up" and l["perda_volta_em"] is None
    assert l["proximo"] == datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)   # 9h de Brasília
    with pool.connection() as c:
        mov = c.execute("select de, para, motivo from funil_movimentos where prospeccao_id=%s",
                        (lid,)).fetchall()
        nota = c.execute("select descricao from prospeccao_atividades where prospeccao_id=%s",
                         (lid,)).fetchone()[0]
        fu = c.execute("select acao, automatico from follow_up_marcacoes where prospeccao_id=%s",
                       (lid,)).fetchall()
    assert mov == [("perdido", "follow_up", "voltou_na_data")]
    assert nota.startswith("Voltou ao funil: chegou a data que ele deu")
    assert len(fu) == 1 and fu[0][1] is True


def test_antes_do_dia_nao_volta(pool, limpo):
    lid = _perdido(pool, HOJE + timedelta(days=1))
    assert fp.voltar_os_vencidos(pool, HOJE) == 0
    assert _lead(pool, lid)["status"] == "perdido"


def test_sem_data_continua_perdido(pool, limpo):
    lid = _perdido(pool, None)
    assert fp.voltar_os_vencidos(pool, HOJE) == 0
    assert _lead(pool, lid)["status"] == "perdido"


def test_volta_uma_vez_so(pool, limpo):
    lid = _perdido(pool, HOJE - timedelta(days=3))
    assert fp.voltar_os_vencidos(pool, HOJE) == 1
    assert fp.voltar_os_vencidos(pool, HOJE) == 0
    with pool.connection() as c:
        assert c.execute("select count(*) from follow_up_marcacoes where prospeccao_id=%s",
                         (lid,)).fetchone()[0] == 1


def test_o_historico_da_perda_fica(pool, limpo):
    lid = _perdido(pool, HOJE)
    fp.voltar_os_vencidos(pool, HOJE)
    l = _lead(pool, lid)
    assert l["perda_motivo"] == "restricao_cpf" and l["perda_em"] is not None


def test_quem_ja_saiu_de_perdido_nao_volta(pool, limpo):
    """O vendedor reabriu à mão antes do dia: a data guardada não mexe mais nele."""
    lid = _perdido(pool, HOJE)
    with pool.connection() as c:
        c.execute("update prospeccao set status='contatado' where id=%s", (lid,))
        c.commit()
    assert fp.voltar_os_vencidos(pool, HOJE) == 0
    assert _lead(pool, lid)["status"] == "contatado"


def test_conta_sem_a_etapa_follow_up_fica_como_esta(pool, limpo):
    lid = _perdido(pool, HOJE, conta=99)
    assert fp.voltar_os_vencidos(pool, HOJE) == 0
    assert _lead(pool, lid)["status"] == "perdido"


def test_marcar_e_limpar_a_data(pool, limpo):
    lid = _perdido(pool, HOJE)
    with pool.connection() as c:
        assert fp.volta_em(c, CONTA, lid) == HOJE
        fp.marcar_volta(c, CONTA, lid, None)
        c.commit()
        assert fp.volta_em(c, CONTA, lid) is None


# ── a ficha do lead ───────────────────────────────────────────────────────
def test_o_campo_so_aparece_na_conta_de_obra(pool, limpo, monkeypatch):
    from web import painel_prospeccao as pp
    lid = _perdido(pool, HOJE)
    with pool.connection() as c:
        monkeypatch.setattr(pp._fr, "perfil_da_conta", lambda c, conta: "obras")
        v = pp._volta_da_ficha(c, CONTA, lid)
        assert v == {"liberada": True, "iso": "2026-12-01", "br": "01/12/2026"}
        monkeypatch.setattr(pp._fr, "perfil_da_conta", lambda c, conta: "eventos")
        assert pp._volta_da_ficha(c, CONTA, lid)["liberada"] is False


def test_a_data_da_tela(pool):
    from web import painel_prospeccao as pp
    assert pp._dia_ou_none("2026-12-01") == HOJE
    assert pp._dia_ou_none("") is None and pp._dia_ou_none("amanhã") is None


def test_a_ficha_compila_com_o_campo_condicionado():
    from web import painel_prospeccao as pp
    fonte = pp._FICHA_TPL
    i = fonte.find('name="perda_volta_em"')
    assert i > 0 and fonte.rfind("{% if volta and volta.liberada %}", 0, i) > fonte.rfind("{% endif %}", 0, i)
    pp._env.get_template("prospeccao_ficha")
