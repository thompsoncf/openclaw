"""Agenda compartilhada: dono, gestor e vendedor veem o mesmo calendário.

O dado sempre foi da conta (finance/agenda.listar_eventos não filtra por membro);
o que separava era permissão de rota e uma consulta. Três buracos, três travas:

1. o gate barrava /painel/agenda pra todo papel que não fosse o dono;
2. o app mostrava só as visitas do próprio vendedor, e a data SEGURADA
   (pré-reserva esperando sinal) não aparecia pra ninguém — a informação que
   evita prometer a mesma data duas vezes;
3. dono e gestor não tinham aba de agenda no app.

A parte de banco reusa o padrão do test_cockpit (schema mínimo descartável).
"""
import inspect
import os
import re
from datetime import datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

from contas.equipe import rotas_do_papel
from finance import cockpit as ck

BRT = timezone(timedelta(hours=-3))

_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, whatsapp text, telefone text, status text default 'novo',
  estagio text default 'lead');
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  lembrete_min int, tipo text default 'pessoal', link_online text, desfecho text,
  status text default 'ativo', criado_em timestamptz default now(), prospeccao_id bigint,
  ics_token text, pre_reserva_ate timestamptz, sinal_centavos int);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    nome = "zaq_agenda_comp_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {nome}")
        c.execute(f"create database {nome}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + nome
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _cenario(pool):
    """Uma conta com dois vendedores e quatro eventos, um de cada natureza."""
    amanha = datetime.now(BRT).replace(hour=10, minute=0, second=0, microsecond=0) \
        + timedelta(days=1)
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome) values ('Buffet') returning id").fetchone()[0]
        rafa = c.execute("insert into membros (conta_id,nome,email) values (%s,'Rafael','r@x.com') returning id",
                         (conta,)).fetchone()[0]
        bia = c.execute("insert into membros (conta_id,nome,email) values (%s,'Bia','b@x.com') returning id",
                        (conta,)).fetchone()[0]
        lead = c.execute("insert into prospeccao (conta_id,vendedor_id,empresa,whatsapp) "
                         "values (%s,%s,'Ana e Pedro','+5586999990001') returning id",
                         (conta, rafa)).fetchone()[0]
        def ev(titulo, membro, **kw):
            cols = {"conta_id": conta, "membro_id": membro, "titulo": titulo,
                    "inicio": amanha, "status": "ativo"} | kw
            ks = ",".join(cols); ps = ",".join(["%s"] * len(cols))
            return c.execute(f"insert into eventos_agenda ({ks}) values ({ps}) returning id",
                             tuple(cols.values())).fetchone()[0]
        visita = ev("Visita — Ana e Pedro", rafa, prospeccao_id=lead)
        festa = ev("Casamento — Júlia", bia, inicio=amanha + timedelta(days=2))
        segurada = ev("Pré — Marina", bia, status="pre_reservado",
                      inicio=amanha + timedelta(days=3),
                      pre_reserva_ate=datetime.now(BRT) + timedelta(days=2))
        do_dono = ev("Reunião interna", None, inicio=amanha + timedelta(hours=2))
        c.commit()
    return {"conta": conta, "rafa": rafa, "bia": bia, "lead": lead,
            "visita": visita, "festa": festa, "segurada": segurada, "do_dono": do_dono}


# ── 1 · o gate ─────────────────────────────────────────────────────────────
def test_o_gate_libera_a_agenda_pra_quem_trabalha():
    """Era o buraco nº 1: gestor e vendedor levavam redirect. 'restrito' segue fora."""
    for papel in ("vendedor", "gestor", "financeiro"):
        assert "/painel/agenda" in rotas_do_papel(papel), papel
    assert "/painel/agenda" not in rotas_do_papel("restrito")


# ── 2 · a consulta do app ──────────────────────────────────────────────────
def test_todos_veem_tudo_inclusive_a_data_segurada(pool):
    cx = _cenario(pool)
    evs = ck.agenda_da_conta(pool, cx["conta"], cx["rafa"], so_meus=False)
    ids = {e["id"] for e in evs}
    assert ids == {cx["visita"], cx["festa"], cx["segurada"], cx["do_dono"]}, \
        "a agenda do app tem que trazer visita, festa, SEGURADA e o evento do dono"
    por_id = {e["id"]: e for e in evs}
    assert por_id[cx["visita"]]["tipo_ev"] == "visita"
    assert por_id[cx["festa"]]["tipo_ev"] == "compromisso"
    assert por_id[cx["segurada"]]["tipo_ev"] == "segurada"
    assert por_id[cx["segurada"]]["prazo"].endswith("d"), "o prazo do sinal vai no card"


def test_meus_filtra_pro_proprio_e_todos_nao(pool):
    cx = _cenario(pool)
    meus = ck.agenda_da_conta(pool, cx["conta"], cx["rafa"], so_meus=True)
    assert {e["id"] for e in meus} == {cx["visita"]}, "Meus = só o que ELE marcou"
    todos = ck.agenda_da_conta(pool, cx["conta"], cx["rafa"], so_meus=False)
    assert len(todos) == 4


def test_cada_evento_diz_quem_marcou(pool):
    cx = _cenario(pool)
    por_id = {e["id"]: e for e in ck.agenda_da_conta(pool, cx["conta"], cx["bia"])}
    assert por_id[cx["visita"]]["autor"] == "Rafael"
    assert por_id[cx["visita"]]["minha"] is False
    assert por_id[cx["festa"]]["minha"] is True, "o evento da própria Bia é 'minha'"
    assert por_id[cx["do_dono"]]["autor"] == "", \
        "evento do dono titular (sem membro) fica sem autor — a tela omite a linha"


def test_dono_sem_membro_id_ve_a_conta_inteira(pool):
    """O dono titular entra sem membro_id; so_meus não pode explodir nem filtrar."""
    cx = _cenario(pool)
    evs = ck.agenda_da_conta(pool, cx["conta"], None, so_meus=True)
    assert len(evs) == 4, "sem membro_id, so_meus é ignorado (não há 'meus')"


def test_conta_vizinha_nao_ve_nada(pool):
    cx = _cenario(pool)
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Vizinha') returning id").fetchone()[0]
        c.commit()
    assert ck.agenda_da_conta(pool, outra, None) == []


def test_evento_cancelado_fica_fora(pool):
    cx = _cenario(pool)
    with pool.connection() as c:
        c.execute("update eventos_agenda set status='cancelado' where id=%s", (cx["festa"],))
        c.commit()
    ids = {e["id"] for e in ck.agenda_da_conta(pool, cx["conta"], None)}
    assert cx["festa"] not in ids


# ── 3 · as telas ───────────────────────────────────────────────────────────
def test_o_app_tem_aba_de_agenda_pro_dono_e_gestor():
    from web import painel_cockpit as pc
    fonte = inspect.getsource(pc._abas_dono)
    assert '"agenda"' in fonte and "/agenda" in fonte, \
        "dono e gestor não tinham aba de agenda nenhuma no app"


def test_a_tela_do_app_atende_os_dois_guards_e_tem_o_seletor():
    from web import painel_cockpit as pc
    fonte = inspect.getsource(pc.cockpit_agenda)
    assert "_gerencia(request)" in fonte, "o dono titular (sem membro_id) precisa entrar"
    assert "agenda_da_conta" in fonte, "a consulta nova é a fonte da tela"
    assert "t=meus" in fonte and "t=todos" in fonte, "o seletor Meus × Todos"
    assert "not gestao" in fonte, \
        "o PADRÃO diz quem é: vendedor abre em Meus, dono/gestor em Todos"


def test_a_rota_de_novo_compromisso_existe_e_vem_antes_do_id():
    from web import painel_cockpit as pc
    fonte = inspect.getsource(pc)
    assert '"/cockpit/agenda/novo"' in fonte
    p = inspect.signature(pc.cockpit_agenda_novo).parameters
    assert {"titulo", "data", "hora", "local"} <= set(p)


def test_o_desktop_mostra_autor_e_filtro_por_pessoa():
    import web.painel_agenda as pa
    assert "ag-pessoas" in pa._AGENDA_TPL, "os chips do filtro por pessoa"
    assert "e.autor" in pa._AGENDA_TPL, "o autor na linha dos próximos e na caixa do dia"
    fonte = inspect.getsource(pa.agenda_home)
    assert 'p: str = ""' in fonte and "p_id" in fonte
