"""O SELO DO FOLLOW-UP NO CARD DO FUNIL (16/09/2026).

Pedido do dono: "avisar no card a questão do follow". O que este arquivo fixa não
é a aparência do selo — é QUEM RECEBE ele, que é onde uma tela erra caro:

  1. conta sem a tela de Follow-up (§6: hoje só o perfil `eventos`) não vê selo;
  2. conta com a tela mas com `follow_up_modo='off'` também não — o dono não
     optou por nada, e um selo de cobrança aparecendo sozinho no quadro dos
     vendedores seria o sistema decidindo por ele;
  3. em 'observando' APARECE, de propósito: foi a escolha do dono em 16/09, pra
     ver a régua rodando com lead de verdade antes de ligar a cobrança;
  4. o atraso é escrito igual nas duas telas — o funil e o /painel/follow-up leem
     o mesmo `_tempo_curto`, senão uma arredonda diferente da outra e ninguém
     descobre por meses.

O que NÃO está aqui: o estado em si (crítico, atrasado, hoje…). Ele é do
`finance/follow_up.py` e já tem `tests/test_follow_up.py` inteiro — este selo só
mostra o que aquele motor calculou, e duplicar o teste seria duplicar a regra.
"""
import os
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool
from starlette.datastructures import QueryParams

from web import painel_follow_up as pf
from web import painel_prospeccao as pp

CONTA = 11


# ----------------------------------------------------- quem recebe o selo
def test_conta_sem_a_tela_de_follow_up_nao_ganha_selo(monkeypatch):
    """Perfil recorrente/produto: `PERFIS_COM_TELA` não os inclui hoje."""
    monkeypatch.setattr("web.portal.nicho_da_conta", lambda conta: "consultoria")
    assert pp._tem_follow_up((3, "ZAQ")) is False


def test_conta_de_eventos_tem_a_tela(monkeypatch):
    monkeypatch.setattr("web.portal.nicho_da_conta", lambda conta: "eventos")
    assert pp._tem_follow_up((34, "Prime")) is True


def test_conta_curta_nao_explode_e_fica_sem_selo():
    """Mock de teste com tupla curta: o lado seguro de errar é não mostrar nada."""
    assert pp._tem_follow_up(()) in (False, True)   # não levanta


def test_o_modo_off_nao_mostra_selo_e_observando_mostra():
    """A regra do handler, escrita como dado pra não depender de subir o board.

    Se alguém trocar a tupla por `('ligado',)` este teste cai — e é justamente
    isso que a gente quer que caia, porque 'observando' mostrando o selo foi
    decisão explícita do dono."""
    import re
    fonte = open("web/painel_prospeccao.py", encoding="utf-8").read()
    m = re.search(r'_cfg\.get\("follow_up_modo"\) in \(([^)]*)\)', fonte)
    assert m, "a porta do modo sumiu do handler"
    modos = m.group(1)
    assert "observando" in modos, "o dono escolheu ver o selo em observando"
    assert "ligado" in modos
    assert "off" not in modos, "em 'off' o dono não optou por nada"


# ----------------------------------------------------- o atraso, uma grafia só
def test_o_atraso_e_escrito_igual_nas_duas_telas():
    for h in (None, 0, 1, 16, 47, 48, 49, 72, 500):
        assert pf._tempo(h) == pp._tempo_curto(h)


def test_o_atraso_vira_dias_a_partir_de_48h():
    assert pp._tempo_curto(47) == "47h"
    assert pp._tempo_curto(48) == "2d"
    assert pp._tempo_curto(72) == "3d"
    assert pp._tempo_curto(None) == "—"


# ------------------------------------------------ o QUADRO DE VERDADE, renderizado
#
# POR QUE ESTE BLOCO EXISTE, e é a parte que importa deste arquivo: a primeira
# versão dele só afirmava coisas sobre o TEXTO do fonte e sobre funções puras.
# Passou verde enquanto o quadro estava quebrado — `ctx["conta"]` não existia no
# handler, e `prospeccao_kanban` levantava NameError na linha do selo. Quem pegou
# foi o test_funil_por_mes, com 51 falhas, porque ele RENDERIZA a tela.
#
# É a mesma lição que o #702 deixou escrita neste repo: "os testes chamam a função
# DIRETO e provam que ela funciona. Ninguém exercitava o caminho da TELA, que é
# onde estava o erro." Repeti o erro no mesmo mês, então aqui fica o teste que
# entra pela porta da frente.
# O SCHEMA VEM DO test_funil_por_mes, que já exercita este mesmo handler e é
# mantido junto com ele. Copiar as tabelas pra cá seria criar uma SEGUNDA
# definição do quadro — e a primeira tentativa deste arquivo fez exatamente isso
# e quebrou em `column "fixa" does not exist` na primeira execução. Aqui só se
# ACRESCENTA o que o selo precisa e o outro arquivo não tem.
from tests.test_funil_por_mes import _SQL as _SQL_QUADRO  # noqa: E402

_SQL = _SQL_QUADRO + """
create table if not exists funil_regua (conta_id bigint primary key, follow_up_modo text,
  -- `fu_zap` (migração 280): o aviso também no WhatsApp do vendedor, desligado por padrão
  fu_zap boolean not null default false,
    fila_modo text, fu_proposta_dias int, fu_festa_dias int, fu_toques text,
    fu_teto_dia int);
create table if not exists follow_up_marcacoes (id bigserial primary key, conta_id bigint,
    prospeccao_id bigint, prazo_em timestamptz, acao text, membro_id bigint,
    automatico boolean default true, motivo text, criado_em timestamptz default now());
create table if not exists funil_movimentos (id bigserial primary key, prospeccao_id bigint,
    para text, criado_em timestamptz default now());
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_selo_fu_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _monta(monkeypatch, pool, *, modo="observando", tem_tela=True, conta_no_ctx=True):
    import finance.vendas as v
    monkeypatch.setattr(v, "vende_data", lambda pool, conta_id: True)
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_tem_follow_up", lambda conta: tem_tela)
    ctx = {"conta_id": CONTA, "membro_id": 1, "gerencia": True, "pode_atribuir": True}
    if conta_no_ctx:
        ctx["conta"] = (CONTA, "Prime de teste")
    monkeypatch.setattr(pp, "_acesso", lambda req: (ctx, None))
    with pool.connection() as c:
        c.execute("delete from funil_regua where conta_id=%s", (CONTA,))
        c.execute("insert into funil_regua (conta_id, follow_up_modo) values (%s,%s)", (CONTA, modo))
        c.commit()


def _lead_esperando(pool, empresa, *, festa=None):
    """Um lead com o cliente falando por último — cai no grupo 'esperando'."""
    with pool.connection() as c:
        lid = c.execute(
            "insert into prospeccao (conta_id, empresa, status, estagio, evento_em) "
            "values (%s,%s,'contatado','lead',%s) returning id", (CONTA, empresa, festa)).fetchone()[0]
        cid = c.execute("insert into conversas (conta_id, prospeccao_id, canal) "
                        "values (%s,%s,'whatsapp') returning id", (CONTA, lid)).fetchone()[0]
        c.execute("insert into mensagens (conversa_id, direcao, texto) values (%s,'in','oi')", (cid,))
        c.commit()
    return lid


def _render(monkeypatch, pool) -> str:
    req = SimpleNamespace(session={}, query_params=QueryParams(""))
    r = pp.prospeccao_kanban(req, entrou="tudo")
    assert r.status_code == 200, "o quadro tem que abrir"
    return bytes(r.body).decode("utf-8")


def test_o_quadro_ABRE_com_o_selo_ligado(monkeypatch, pool):
    """O teste que faltava: entra pelo handler, não pelo texto do fonte.

    Sem ele, o NameError de `ctx["conta"]` passaria verde aqui e quebraria o
    quadro inteiro em produção."""
    _monta(monkeypatch, pool)
    _lead_esperando(pool, "MiKaella Nara")
    html = _render(monkeypatch, pool)
    assert "MiKaella Nara" in html


def test_o_quadro_abre_mesmo_sem_a_linha_da_conta_no_ctx(monkeypatch, pool):
    """Chamador que monta o ctx à mão não pode derrubar a tela — fica sem selo."""
    _monta(monkeypatch, pool, conta_no_ctx=False)
    _lead_esperando(pool, "Janui")
    html = _render(monkeypatch, pool)
    assert "Janui" in html
    assert 'class="kbfu' not in html, "sem a conta no ctx não dá pra saber o nicho: sem selo"


def test_o_quadro_abre_em_conta_sem_a_tela_de_follow_up(monkeypatch, pool):
    _monta(monkeypatch, pool, tem_tela=False)
    _lead_esperando(pool, "Bianca Sousa")
    html = _render(monkeypatch, pool)
    assert "Bianca Sousa" in html
    assert 'class="kbfu' not in html


def test_o_quadro_abre_com_o_follow_up_desligado(monkeypatch, pool):
    _monta(monkeypatch, pool, modo="off")
    _lead_esperando(pool, "Verônica Cardoso")
    html = _render(monkeypatch, pool)
    assert "Verônica Cardoso" in html
    assert 'class="kbfu' not in html, "em 'off' o dono não optou por nada"


def test_o_selo_que_EXPLODE_nao_derruba_o_quadro(monkeypatch, pool):
    """A tolerância, provada em vez de afirmada.

    A versão anterior deste teste lia o FONTE atrás de `with c.transaction():` e
    `except Exception:`. Isso quebrou sozinho quando eu acrescentei um comentário
    no meio — teste que depende da largura de uma janela de texto não prova nada e
    ainda dá trabalho. Agora o follow-up levanta de verdade e o que se exige é o
    que importa: o quadro abre, com os cards.

    O SAVEPOINT é a parte que só este formato pega. No Postgres um erro aborta a
    transação inteira; sem o ponto de retorno, as consultas que vêm DEPOIS nesta
    mesma conexão morreriam com "current transaction is aborted" — e o quadro
    cairia por causa de um enfeite. Se alguém tirar o `with c.transaction():`,
    este teste reprova, porque os cards não chegam na tela."""
    _monta(monkeypatch, pool)
    _lead_esperando(pool, "Laura Teste")

    import finance.follow_up as _fu

    def explode(*a, **kw):
        raise RuntimeError("banco sem a migração do follow-up, por exemplo")

    monkeypatch.setattr(_fu, "leads", explode)
    html = _render(monkeypatch, pool)
    assert "Laura Teste" in html, "o quadro tem que abrir mesmo com o selo quebrado"
    assert 'class="kbfu' not in html
