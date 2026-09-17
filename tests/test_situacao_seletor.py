"""O SELETOR DE SITUAÇÃO DO LEAD (17/09/2026).

Pedido do dono: "no follow-up, dentro do card tem um botão abrir ficha e dentro
dele tem etapas do funil; veja como elas estão e vamos criar um seletor mais
fácil pro vendedor qualificar".

Olhando pra desenhar, apareceu um defeito maior que o desenho — e quem viu foi o
dono, conferindo a tela contra o mockup: "você diz ter 9, na Prime só aparecem 6,
cadê o resto?".

A MESMA janela, no MESMO lead, oferecia listas diferentes conforme a porta:

    quadro do funil → clicar no card ........ 6 situações
    Follow-up → Abrir ficha ................. 9
    ficha completa .......................... 9
    app → lead → Etapa no funil ............. 5 (+ ganho/perdido em botão)

A causa: `web/painel_prospeccao.py` filtrava a lista de etapas por
`sai_do_quadro` pra montar as COLUNAS do quadro (migração 238, pedido do dono, e
certo) — e o seletor era montado, mais abaixo, da mesma variável já filtrada.
"Não vira coluna" virou, sem ninguém querer, "não dá pra escolher".

O custo, na conta 34: `ganho` se chama "Evento Realizado" e está marcada assim.
Quem trabalha pelo quadro não tinha como marcar a venda. Em dois meses, num funil
de 398 leads, 7 entradas em ganho.

O QUE ESTE ARQUIVO FIXA, então, é a separação: uma lista pras COLUNAS, outra pras
SITUAÇÕES — e a segunda nunca é filtrada.

E entra pela porta da frente. Asserção sobre o texto do fonte passa verde com a
tela quebrada; foi assim que o #702 e depois o selo do follow-up passaram. Aqui o
quadro é renderizado de verdade.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool
from starlette.datastructures import QueryParams

from web import janela_lead as jl
from web import painel_prospeccao as pp

CONTA = 11


# ═══════════════════════════════════════════════ a lista, sem banco nenhum
def _et(chave, rotulo, *, sai=False):
    return {"chave": chave, "rotulo": rotulo, "sai_do_quadro": sai}


def test_a_lista_traz_a_etapa_que_sai_do_quadro_marcada():
    """O coração do conserto: sai do quadro é sobre COLUNA, não sobre escolha."""
    saida = jl.lista_de_status([_et("novo", "Novo"), _et("qualificado", "Agendado Visita", sai=True)])
    assert [x["c"] for x in saida] == ["novo", "qualificado"]
    assert saida[1]["sai"] is True and saida[0]["sai"] is False


def test_ganho_e_perdido_saem_marcados_como_desfecho_pela_CHAVE():
    """O rótulo é livre — uma conta chama o `ganho` de "Evento Realizado", outra de
    "Producao". Separar pelo nome escrito na tela erraria em toda conta que
    renomeou; a chave é o que o Raio-X conta como venda e perda."""
    saida = jl.lista_de_status([
        _et("contatado", "Contatado"), _et("ganho", "Evento Realizado"), _et("perdido", "Entregue")])
    por_chave = {x["c"]: x for x in saida}
    assert por_chave["ganho"]["fim"] == "ganho"
    assert por_chave["perdido"]["fim"] == "perdido"      # mesmo chamando-se "Entregue"
    assert por_chave["contatado"]["fim"] == ""


def test_a_ordem_do_funil_e_preservada():
    """É dela que sai o "passo seguinte" destacado na tela: se a ordem embaralhar,
    a seta aponta pra etapa errada."""
    et = [_et("novo", "Novo"), _et("contatado", "Contatado"), _et("proposta", "Negociação")]
    assert [x["c"] for x in jl.lista_de_status(et)] == ["novo", "contatado", "proposta"]


def test_lista_vazia_nao_explode():
    assert jl.lista_de_status([]) == []


# ═══════════════════════════════════════════════ "parado há N dias"
def _atras(dias):
    return datetime.now(timezone.utc) - timedelta(days=dias)


def test_parado_conta_os_dias():
    agora = datetime.now(timezone.utc)
    assert jl.parado_texto(agora - timedelta(days=20), agora) == "parado há 20 dias"
    assert jl.parado_texto(agora - timedelta(days=1), agora) == "parado há 1 dia"


def test_lead_de_hoje_nao_ganha_rotulo_de_parado():
    """"parado há 0 dias" num lead que chegou hoje é cobrança errada — e o vendedor
    que lê isso uma vez aprende a ignorar o rótulo inteiro, inclusive quando ele
    estiver certo."""
    agora = datetime.now(timezone.utc)
    assert jl.parado_texto(agora - timedelta(hours=5), agora) == ""
    assert jl.parado_texto(None, agora) == ""


# ═══════════════════════════════════════════════ o quadro, renderizado
from tests.test_funil_por_mes import _SQL as _SQL_QUADRO  # noqa: E402

_SQL = _SQL_QUADRO + """
create table if not exists funil_movimentos (id bigserial primary key, conta_id bigint,
    prospeccao_id bigint, de text, para text, motivo text, membro_id bigint,
    criado_em timestamptz default now());
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_situacao_seletor_test"
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


#: O funil da Prime, reduzido ao que importa aqui: DUAS das etapas saem do quadro,
#: e uma delas é o `ganho`. É a forma exata do defeito.
_ETAPAS_PRIME = [
    ("novo", "Novo", 0, True, False),
    ("contatado", "Contatado", 10, False, False),
    ("qualificado", "Agendado Visita", 30, False, True),
    ("proposta", "Negociação", 50, False, False),
    ("ganho", "Evento Realizado", 900, True, True),
    ("perdido", "Perdido", 910, True, False),
]


def _monta(monkeypatch, pool):
    import finance.vendas as v
    monkeypatch.setattr(v, "vende_data", lambda pool, conta_id: True)
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_tem_follow_up", lambda conta: False)
    ctx = {"conta_id": CONTA, "membro_id": 1, "gerencia": True, "pode_atribuir": True,
           "conta": (CONTA, "Prime de teste")}
    monkeypatch.setattr(pp, "_acesso", lambda req: (ctx, None))
    with pool.connection() as c:
        c.execute("delete from funil_etapas where conta_id=%s", (CONTA,))
        for chave, rot, ordem, fixa, sai in _ETAPAS_PRIME:
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa, sai_do_quadro)
                         values (%s,%s,%s,%s,%s,%s)""", (CONTA, chave, rot, ordem, fixa, sai))
        c.execute("insert into prospeccao (conta_id, empresa, status, estagio) "
                  "values (%s,'Josiany Martins','contatado','lead')", (CONTA,))
        c.commit()
    return ctx


def _quadro(pool) -> str:
    req = SimpleNamespace(session={}, query_params=QueryParams(""))
    r = pp.prospeccao_kanban(req, entrou="tudo")
    assert r.status_code == 200, "o quadro tem que abrir"
    return bytes(r.body).decode("utf-8")


def _kb_status(html):
    m = re.search(r"var _KB_STATUS=(\[.*?\]);", html)
    assert m, "_KB_STATUS não foi embutido na página do quadro"
    return json.loads(m.group(1))


def test_O_QUADRO_OFERECE_A_ETAPA_DE_GANHO_MESMO_ELA_SAINDO_DO_QUADRO(monkeypatch, pool):
    """O teste que representa o defeito inteiro.

    Antes de 17/09/2026 este assert falhava: "Evento Realizado" está marcada
    sai_do_quadro, e por isso não chegava no seletor. O vendedor via o lead e não
    tinha como dizer que ele foi ganho.
    """
    _monta(monkeypatch, pool)
    lista = _kb_status(_quadro(pool))
    chaves = [x["c"] for x in lista]
    assert chaves == ["novo", "contatado", "qualificado", "proposta", "ganho", "perdido"], (
        "o seletor do quadro não recebeu as etapas todas — o filtro das colunas "
        "voltou a decidir o que o vendedor pode escolher")
    por_chave = {x["c"]: x for x in lista}
    assert por_chave["ganho"]["sai"] is True and por_chave["ganho"]["fim"] == "ganho"
    assert por_chave["qualificado"]["sai"] is True, (
        "sem a marca, a janela não avisa que o card vai sumir do quadro")


def test_o_quadro_continua_SEM_desenhar_coluna_pra_etapa_escondida(monkeypatch, pool):
    """A outra metade, e ela importa igual: o conserto não pode desfazer a 238.
    O dono pediu o quadro limpo — o que mudou é só o seletor."""
    _monta(monkeypatch, pool)
    html = _quadro(pool)
    abas = re.findall(r'data-tab="([^"]+)"', html)
    assert "contatado" in abas and "proposta" in abas
    assert "qualificado" not in abas and "ganho" not in abas, (
        "a etapa marcada pra sair do quadro virou coluna — a migração 238 foi desfeita")


def test_o_quadro_abre_e_mostra_o_lead(monkeypatch, pool):
    """A porta da frente: se a página não renderiza, os asserts acima não valem
    nada. Foi exatamente isto que faltou no #702."""
    _monta(monkeypatch, pool)
    assert "Josiany Martins" in _quadro(pool)


def test_a_janela_recebe_os_botoes_e_nao_o_select(monkeypatch, pool):
    """O desenho aprovado: fileira de botões. Se o <select> voltar, volta junto a
    roleta de três toques no celular."""
    _monta(monkeypatch, pool)
    html = _quadro(pool)
    assert "function kbLeadSitHtml" in html and "kbLeadIr(this," in html
    assert 'onchange="kbLeadStatus(' not in html, "o <select> de situação voltou"


# ═══════════════════════════════════════════════ o tempo parado, do banco
def test_parado_desde_le_a_ultima_entrada_na_etapa_atual(monkeypatch, pool):
    _monta(monkeypatch, pool)
    with pool.connection() as c:
        lid = c.execute("select id from prospeccao where conta_id=%s", (CONTA,)).fetchone()[0]
        c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, criado_em)
                     values (%s,%s,'novo','contatado',%s), (%s,%s,'contatado','proposta',%s)""",
                  (CONTA, lid, _atras(40), CONTA, lid, _atras(3)))
        c.commit()
    # o lead está em 'contatado': vale a entrada de 40 dias atrás, não a de 3
    desde = pp._parado_desde(pool, CONTA, lid)
    assert 39 <= (datetime.now(timezone.utc) - desde).days <= 41


def test_sem_movimento_nenhum_o_relogio_corre_desde_que_o_lead_nasceu(monkeypatch, pool):
    """Chutar "agora" esconderia justamente o lead esquecido — o que a tela existe
    pra mostrar."""
    _monta(monkeypatch, pool)
    with pool.connection() as c:
        lid = c.execute("insert into prospeccao (conta_id, empresa, status, estagio, criado_em) "
                        "values (%s,'Antigo','contatado','lead',%s) returning id",
                        (CONTA, _atras(60))).fetchone()[0]
        c.commit()
    desde = pp._parado_desde(pool, CONTA, lid)
    assert 59 <= (datetime.now(timezone.utc) - desde).days <= 61


def test_base_sem_a_tabela_de_movimentos_nao_derruba_a_janela(pool):
    """Tolerante de propósito: um lead sem o rótulo "parado há N dias" é um
    detalhe; uma janela que não abre é a tela inteira. Mesma lição da ponte do
    funil com a agenda, que quebrou por um `left join` numa tabela ausente."""
    with pool.connection() as c:
        c.execute("drop table if exists funil_movimentos")
        c.commit()
    assert pp._parado_desde(pool, CONTA, 999999) is None
