"""Cockpit do Dono (finance/cockpit_dono): métricas + ações da equipe.

- visao: KPIs (novos/atendimento/ganhos/conversão), funil do time, precisa de atenção;
- placar: ranking por R$ fechado, com fila/ganhos/conversão/pausado;
- vendedor: drill com leads abertos;
- atividade: feed (ganho/visita/proposta);
- reatribuir: move o lead + a conversa; pausar: liga/desliga o rodízio.

Banco dedicado e descartável (schema do test_cockpit + orcamentos).
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import cockpit_dono as cd
from tests.test_cockpit import _BASE_SQL

_SQL = _BASE_SQL + """
create table orcamentos (id bigserial primary key, conta_id bigint, empresa text, criado_por text,
  canal text, status text, setup_centavos bigint default 0, mensal_centavos bigint default 0,
  -- o valor que vira TÍTULO (`coalesce(primeiro_ano, setup)`): é ele que o placar
  -- e o "fechado no período" somam desde 24/09/2026
  primeiro_ano_centavos bigint,
  criado_em timestamptz default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_cockpit_dono_test"
    with admin.connection() as c:
        c.autocommit = True
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


def _seed(c, nome="Prime"):
    conta = c.execute("insert into contas (nome, nome_fantasia) values ('C',%s) returning id", (nome,)).fetchone()[0]
    dono = c.execute("insert into membros (conta_id,nome,email,papel) values (%s,'Dono','d@x.com','dono') returning id",
                     (conta,)).fetchone()[0]
    v1 = c.execute("insert into membros (conta_id,nome,email,papel) values (%s,'Carlos','c@x.com','vendedor') returning id",
                   (conta,)).fetchone()[0]
    v2 = c.execute("insert into membros (conta_id,nome,email,papel,cockpit_pausado) values (%s,'Ana','a@x.com','vendedor',true) returning id",
                   (conta,)).fetchone()[0]
    # Carlos: 1 ganho (R$8k) + 1 aberto contatado ; Ana: 1 aberto novo quente
    g = c.execute("insert into prospeccao (conta_id,vendedor_id,empresa,status,estagio,temperatura,valor_estimado_centavos) "
                  "values (%s,%s,'Festa Corp','ganho','lead','quente',800000) returning id", (conta, v1)).fetchone()[0]
    ab = c.execute("insert into prospeccao (conta_id,vendedor_id,empresa,status,estagio,temperatura) "
                   "values (%s,%s,'Bodas','contatado','lead','morno') returning id", (conta, v1)).fetchone()[0]
    an = c.execute("insert into prospeccao (conta_id,vendedor_id,empresa,status,estagio,temperatura) "
                   "values (%s,%s,'Aniversário','novo','lead','quente') returning id", (conta, v2)).fetchone()[0]
    c.execute("insert into conversas (conta_id,prospeccao_id,agente_ativo,responsavel_membro_id) values (%s,%s,false,%s)",
              (conta, ab, v1))
    c.execute("insert into orcamentos (conta_id,empresa,criado_por,canal,status) values (%s,'Festa Corp',%s,'cockpit','enviado')",
              (conta, str(v1)))
    c.execute("insert into eventos_agenda (conta_id,membro_id,prospeccao_id,titulo,inicio,status) "
              "values (%s,%s,%s,'Visita — Bodas',now(),'ativo')", (conta, v1, ab))
    c.commit()
    return conta, dono, v1, v2, ab, an


def test_visao(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c)
    v = cd.visao(pool, conta, "mes")
    k = v["kpis"]
    assert k["ganhos"] == 1 and k["ganhos_rs"] == "R$ 8 mil" and k["conversao"] == 100
    assert k["com_vend"] == 1 and k["com_ia"] == 1          # Bodas c/ vendedor, Aniversário c/ IA
    fun = {f["rotulo"]: f["n"] for f in v["funil"]}
    assert fun["Novo"] == 1 and fun["Contatado"] == 1 and fun["Qualificado"] == 0   # ganho não entra no funil
    a = v["atencao"]
    assert a["propostas"] == 1 and a["visitas"] == 1 and a["quentes"] == 1


def test_placar_ordena_por_rs(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Placar")
    lista = cd.placar(pool, conta)
    nomes = [x["nome"] for x in lista]
    assert nomes[0] == "Carlos"                             # mais R$ fechado vem 1º
    carlos = next(x for x in lista if x["nome"] == "Carlos")
    ana = next(x for x in lista if x["nome"] == "Ana")
    assert carlos["ganhos"] == 1 and carlos["rs"] == "R$ 8 mil" and carlos["fila"] == 1
    assert carlos["conversao"] == "100%" and carlos["atendendo"] == 1
    assert ana["pausado"] is True and ana["ganhos"] == 0


def test_vendedor_drill(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Drill")
    d = cd.vendedor(pool, conta, v1)
    assert d and d["nome"] == "Carlos" and d["fila"] == 1
    assert [l["empresa"] for l in d["leads"]] == ["Bodas"]  # só o aberto (ganho não conta)
    assert cd.vendedor(pool, conta, 999999) is None


def test_atividade_feed(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Ativ")
    feed = cd.atividade(pool, conta)
    tipos = {e["tipo"] for e in feed}
    assert "ganho" in tipos and "visita" in tipos and "prop" in tipos
    assert any("Carlos ganhou — Festa Corp" in e["txt"] for e in feed)


def test_leads_todos_e_filtros(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Leads")
    todos = cd.leads(pool, conta)
    emps = sorted(l["empresa"] for l in todos)
    assert emps == ["Aniversário", "Bodas"]                  # abertos (ganho fora)
    # por vendedor
    assert [l["empresa"] for l in cd.leads(pool, conta, vend=v2)] == ["Aniversário"]
    # por etapa
    assert [l["empresa"] for l in cd.leads(pool, conta, etapa="novo")] == ["Aniversário"]
    # por temperatura
    assert [l["empresa"] for l in cd.leads(pool, conta, temp="morno")] == ["Bodas"]
    # opções de filtro
    f = cd.filtros_leads(pool, conta)
    assert {x["nome"] for x in f["vendedores"]} == {"Carlos", "Ana"}
    assert "novo" in f["etapas"] and "ganho" not in f["etapas"]
    # os RÓTULOS vêm junto — é deles que a tela escreve a etapa de cada lead
    assert f["rotulos"]["novo"] == "Novo"


def test_o_app_escreve_a_etapa_com_o_nome_que_a_CONTA_deu(pool):
    """CLAUDE.md §6, e um defeito de verdade até 17/09/2026: a tela do gestor no app
    montava os nomes de uma tabela fixa de quatro no código. Numa conta que renomeou
    o funil, o gestor lia no app um vocabulário e no painel outro — "Qualificado"
    onde o painel dizia "Agendado Visita", e "Evento_Realizado" com sublinhado."""
    from web import painel_cockpit as pc
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Vocabulario")
        # o funil da Prime, com os apelidos que o dono deu
        for chave, rot, ordem in [("novo", "Novo", 0), ("contatado", "Contatado", 10),
                                  ("qualificado", "Agendado Visita", 30),
                                  ("proposta", "Negociação", 50),
                                  ("ganho", "Evento Realizado", 900),
                                  ("perdido", "Perdido", 910),
                                  ("evento_realizado", "Evento A Realizar", 920)]:
            c.execute("insert into funil_etapas (conta_id, chave, rotulo, ordem) values (%s,%s,%s,%s)",
                      (conta, chave, rot, ordem))
        c.commit()
    rots = cd.filtros_leads(pool, conta)["rotulos"]
    assert rots.get("qualificado") == "Agendado Visita", "o apelido da conta não chegou na tela"
    assert pc._rot_etapa(rots, "qualificado") == "Agendado Visita"
    assert pc._rot_etapa(rots, "evento_realizado") == "Evento A Realizar"
    assert pc._rot_etapa(rots, "proposta") == "Negociação"
    # ganho e perdido entram nos rótulos mesmo ficando FORA do filtro: o filtro é
    # sobre lead aberto, a lista mostra a etapa de qualquer um
    assert rots.get("ganho") == "Evento Realizado"
    assert "ganho" not in cd.filtros_leads(pool, conta)["etapas"]
    # e o último recurso, pra etapa que sumiu do funil depois de o lead entrar nela:
    # nunca devolver a chave crua, com sublinhado, pra tela
    assert pc._rot_etapa({}, "evento_realizado") == "Evento Realizado"
    assert pc._rot_etapa({}, "") == ""


def test_reatribuir_e_pausar(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Acao")
    # reatribui o lead do Carlos (ab) pra Ana
    assert cd.reatribuir(pool, conta, ab, v2)["ok"] is True
    assert cd.reatribuir(pool, conta, ab, 999999)["ok"] is False   # destino inválido
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s", (ab,)).fetchone()[0] == v2
        assert c.execute("select responsavel_membro_id from conversas where prospeccao_id=%s", (ab,)).fetchone()[0] == v2
    # pausar / reativar
    cd.pausar(pool, conta, v1, True)
    with pool.connection() as c:
        assert c.execute("select cockpit_pausado from membros where id=%s", (v1,)).fetchone()[0] is True
    cd.pausar(pool, conta, v1, False)
    with pool.connection() as c:
        assert c.execute("select cockpit_pausado from membros where id=%s", (v1,)).fetchone()[0] is False


# ------------------------------------------------------- o dinheiro que o gestor lê
# Medido na Prime em 24/09/2026: o cockpit do gestor dizia "Fechado no período:
# R$ 0" com OITO contratos assinados na semana. Ele somava
# `prospeccao.valor_estimado_centavos` — o palpite que o vendedor digita na ficha,
# zerado na base inteira (a mesma razão pela qual o relatório de leads e o portal
# não mostram total). Os oito tinham orçamento fechado e títulos gerados, somando
# R$ 55.490 pela fórmula que gera os títulos.

def _ganho_com_orcamento(c, conta, vend, *, primeiro_ano=750000, setup=1205000, estimado=0):
    o = c.execute("insert into orcamentos (conta_id,empresa,status,setup_centavos,primeiro_ano_centavos) "
                  "values (%s,'Casamento','fechado',%s,%s) returning id",
                  (conta, setup, primeiro_ano)).fetchone()[0]
    lid = c.execute("insert into prospeccao (conta_id,vendedor_id,empresa,status,estagio,"
                    "valor_estimado_centavos,orcamento_id) "
                    "values (%s,%s,'Casamento','ganho','lead',%s,%s) returning id",
                    (conta, vend, estimado, o)).fetchone()[0]
    c.commit()
    return lid, o


def test_o_fechado_vem_do_ORCAMENTO_e_nao_do_palpite(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Dinheiro")
        # zera o palpite do seed pra sobrar só o contrato de verdade
        c.execute("update prospeccao set valor_estimado_centavos=0 where conta_id=%s", (conta,))
        _ganho_com_orcamento(c, conta, v1)
    v = cd.visao(pool, conta, "mes")
    assert v["kpis"]["ganhos"] == 2
    carlos = next(x for x in cd.placar(pool, conta) if x["nome"] == "Carlos")
    # 7.500 do primeiro ano (o que vira título), e não os 12.050 do setup nem R$ 0
    assert carlos["rs_centavos"] == 750000, carlos
    assert v["kpis"]["ganhos_rs"] != "R$ 0"


def test_sem_orcamento_o_palpite_ainda_vale(pool):
    """Conta que trabalha sem orçamento não pode perder o número que tinha."""
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Palpite")
    carlos = next(x for x in cd.placar(pool, conta) if x["nome"] == "Carlos")
    assert carlos["rs_centavos"] == 800000      # o valor_estimado do seed


def test_o_orcamento_sem_primeiro_ano_cai_no_setup(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Setup")
        c.execute("update prospeccao set valor_estimado_centavos=0 where conta_id=%s", (conta,))
        _ganho_com_orcamento(c, conta, v1, primeiro_ano=None, setup=1205000)
    carlos = next(x for x in cd.placar(pool, conta) if x["nome"] == "Carlos")
    assert carlos["rs_centavos"] == 1205000


# ------------------------------------------------------- o funil com o nome da conta

def _funil_da_prime(c, conta):
    for chave, rot, ordem in [("novo", "Novo", 0), ("contatado", "Contatado", 10),
                              ("qualificado", "Agendado Visita", 30),
                              ("proposta", "Negociação", 50),
                              ("evento_realizado", "ORCAMENTO ASSINADO", 70),
                              ("ganho", "CONTRATO ASSINADO", 900),
                              ("perdido", "Perdido", 910)]:
        c.execute("insert into funil_etapas (conta_id, chave, rotulo, ordem, fase) values (%s,%s,%s,%s,%s)",
                  (conta, chave, rot, ordem, "fechamento" if chave in ("ganho", "perdido") else "venda"))
    c.commit()


def test_o_funil_do_time_usa_as_etapas_DA_CONTA(pool):
    """O mesmo defeito que a aba Leads corrigiu em 17/09/2026 continuava aqui, na
    tela de cima: quatro chaves fixas com os rótulos de fábrica."""
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Funil")
        _funil_da_prime(c, conta)
    rot = [f["rotulo"] for f in cd.visao(pool, conta, "mes")["funil"]]
    assert rot == ["Novo", "Contatado", "Agendado Visita", "Negociação", "ORCAMENTO ASSINADO"], rot
    # ganho e perdido não são etapa de funil aberto
    assert "CONTRATO ASSINADO" not in rot and "Perdido" not in rot


def test_conta_sem_funil_cadastrado_nao_fica_sem_tela(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "SemFunil")
    rot = [f["rotulo"] for f in cd.visao(pool, conta, "mes")["funil"]]
    assert rot == ["Novo", "Contatado", "Qualificado", "Proposta"]


# ------------------------------------------------------- parados: o que é "contato"

def test_parados_olha_a_CONVERSA_e_nao_so_o_campo(pool):
    """`ultimo_contato_em` só é escrito por quem move o lead na mão no painel — na
    Prime, 34 dos 423 leads abertos. "Parados há +3 dias" virava "cadastrados há
    mais de 3 dias": 363 leads, 118 deles com mensagem nossa nos últimos 3 dias."""
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Parados")
        c.execute("update prospeccao set criado_em=now() - interval '10 days', ultimo_contato_em=null "
                  "where conta_id=%s", (conta,))
        c.commit()
        assert cd.visao(pool, conta, "mes")["atencao"]["parados"] == 2, "os dois abertos estão parados"
        # falamos com um deles ontem, pelo WhatsApp da empresa
        cv = c.execute("select id from conversas where prospeccao_id=%s", (ab,)).fetchone()[0]
        c.execute("insert into mensagens (conversa_id, direcao, criado_em) values (%s,'out',now() - interval '1 day')",
                  (cv,))
        c.commit()
    assert cd.visao(pool, conta, "mes")["atencao"]["parados"] == 1


def test_quente_que_recebeu_mensagem_hoje_sai_do_alerta(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Quentes")
        c.execute("update prospeccao set criado_em=now() - interval '10 days', ultimo_contato_em=null "
                  "where conta_id=%s", (conta,))
        cv = c.execute("insert into conversas (conta_id,prospeccao_id) values (%s,%s) returning id",
                       (conta, an)).fetchone()[0]
        c.commit()
        assert cd.visao(pool, conta, "mes")["atencao"]["quentes"] == 1
        c.execute("insert into mensagens (conversa_id, direcao) values (%s,'out')", (cv,))
        c.commit()
    assert cd.visao(pool, conta, "mes")["atencao"]["quentes"] == 0


def test_a_mensagem_que_ENTROU_nao_apaga_o_parado(pool):
    """Cliente que escreve e não é respondido continua parado — senão o alerta some
    justamente quando alguém está esperando."""
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "SoEntrada")
        c.execute("update prospeccao set criado_em=now() - interval '10 days', ultimo_contato_em=null "
                  "where conta_id=%s", (conta,))
        cv = c.execute("select id from conversas where prospeccao_id=%s", (ab,)).fetchone()[0]
        c.execute("insert into mensagens (conversa_id, direcao) values (%s,'in')", (cv,))
        c.commit()
    assert cd.visao(pool, conta, "mes")["atencao"]["parados"] == 2
