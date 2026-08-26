"""Relatórios > Agenda: a Agenda (web/painel_agenda.py) só mostra o que vem —
mês corrente e os próximos compromissos. Esta aba fecha o período: quantos
eventos, quantos viraram presença, quantos não aconteceram e quantos foram
cancelados. Mesmo template genérico de Relatórios (colunas/linhas/métricas),
nenhuma coluna nova no banco — `status`, `desfecho`, `tipo`, `tipo_evento`,
`convidados` e `sinal_centavos` já existem desde as migrações 098-179.
"""
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

import web.painel_relatorios as rel

_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table orcamentos (id bigserial primary key, conta_id bigint,
  cliente text, empresa text, evento_agenda_id bigint);
create table eventos_agenda (id bigserial primary key, conta_id bigint,
  membro_id bigint, titulo text not null, inicio timestamptz not null,
  tipo text default 'pessoal', tipo_evento text, status text default 'ativo',
  desfecho text, convidados int, sinal_centavos int,
  criado_em timestamptz default now());
"""

HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_relatorios_agenda_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


@pytest.fixture
def cen(pool):
    with pool.connection() as c:
        c.execute("truncate contas, membros, orcamentos, eventos_agenda restart identity")
        conta = c.execute("insert into contas (nome) values ('Prime Eventos') returning id").fetchone()[0]
        jacqueline = c.execute("insert into membros (conta_id, nome) values (%s,'Jacqueline') "
                               "returning id", (conta,)).fetchone()[0]
        pedro = c.execute("insert into membros (conta_id, nome) values (%s,'Pedro') "
                          "returning id", (conta,)).fetchone()[0]
        c.commit()
    return {"conta": conta, "jacqueline": jacqueline, "pedro": pedro}


def _evento(pool, conta, *, titulo="Compromisso", inicio=None, tipo="pessoal",
           tipo_evento=None, status="ativo", desfecho=None, convidados=None,
           sinal=None, membro_id=None):
    inicio = inicio or datetime.now(timezone.utc)
    with pool.connection() as c:
        eid = c.execute(
            """insert into eventos_agenda (conta_id, membro_id, titulo, inicio, tipo,
                 tipo_evento, status, desfecho, convidados, sinal_centavos)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta, membro_id, titulo, inicio, tipo, tipo_evento, status, desfecho,
             convidados, sinal),
        ).fetchone()[0]
        c.commit()
    return eid


def _orc(pool, conta, evento_id, *, cliente=None, empresa=None):
    with pool.connection() as c:
        c.execute(
            "insert into orcamentos (conta_id, cliente, empresa, evento_agenda_id) "
            "values (%s,%s,%s,%s)", (conta, cliente, empresa, evento_id),
        )
        c.commit()


# --------------------------------------------------------------------- lista
def test_lista_tudo_por_padrao_sem_filtro_de_status(pool, cen):
    _evento(pool, cen["conta"], status="ativo")
    _evento(pool, cen["conta"], status="pre_reservado")
    _evento(pool, cen["conta"], status="cancelado")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert len(dados["linhas"]) == 3


def test_filtro_de_status_corta_a_tabela(pool, cen):
    _evento(pool, cen["conta"], status="ativo")
    _evento(pool, cen["conta"], status="cancelado")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "cancelado", "", "")
    assert len(dados["linhas"]) == 1
    assert dados["linhas"][0]["status"] == "Cancelado"


def test_metricas_de_topo_ignoram_o_filtro_de_status_de_proposito(pool, cen):
    """Mesma garantia de Orçamentos: o filtro corta a TABELA, as métricas
    continuam mostrando a distribuição inteira do período."""
    _evento(pool, cen["conta"], status="ativo", desfecho="realizado")
    _evento(pool, cen["conta"], status="cancelado")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "cancelado", "", "")
    assert len(dados["linhas"]) == 1                       # tabela: só o cancelado
    assert dados["metricas"][0] == ("Eventos no período", "2")  # métrica: os dois


# ------------------------------------------------------------------- métricas
def test_realizados_nao_realizados_e_cancelados_contam_certo(pool, cen):
    _evento(pool, cen["conta"], desfecho="realizado")
    _evento(pool, cen["conta"], desfecho="realizado")
    _evento(pool, cen["conta"], desfecho="nao_realizado")
    _evento(pool, cen["conta"], status="cancelado")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    metricas = dict(dados["metricas"])
    assert metricas["Eventos no período"] == "4"
    assert metricas["Realizados"] == "2 · 50%"
    assert metricas["Não realizados"] == "1 · 25%"
    assert metricas["Cancelados"] == "1 · 25%"


def test_sem_evento_nenhum_percentual_nao_quebra_com_divisao_por_zero(pool, cen):
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    metricas = dict(dados["metricas"])
    assert metricas["Eventos no período"] == "0"
    assert metricas["Realizados"] == "0 · 0%"


def test_sinal_soma_no_total_e_na_metrica(pool, cen):
    _evento(pool, cen["conta"], sinal=50000)
    _evento(pool, cen["conta"], sinal=30000)
    _evento(pool, cen["conta"], sinal=None)  # sem sinal: não quebra a soma
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["total_centavos"] == 80000
    assert dict(dados["metricas"])["Sinal no período"] == "R$ 800,00"


# ------------------------------------------------------------------- colunas
def test_evento_usa_tipo_evento_quando_tem_senao_cai_pro_titulo(pool, cen):
    _evento(pool, cen["conta"], titulo="Compromisso qualquer", tipo_evento="Casamento")
    _evento(pool, cen["conta"], titulo="Visita", tipo_evento=None)
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    eventos = {l["evento"] for l in dados["linhas"]}
    assert eventos == {"Casamento", "Visita"}


def test_convidados_nulo_mostra_travessao(pool, cen):
    _evento(pool, cen["conta"], convidados=None)
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["convidados"] == "—"


def test_cliente_vem_do_orcamento_vinculado(pool, cen):
    eid = _evento(pool, cen["conta"])
    _orc(pool, cen["conta"], eid, empresa="Isabela Silva Mendes")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["cliente"] == "Isabela Silva Mendes"


def test_sem_orcamento_vinculado_cliente_e_travessao(pool, cen):
    """Visita e compromisso pessoal não nascem de orçamento — não pode dar
    erro, tem que mostrar "—" como todo campo sem dado neste relatório."""
    _evento(pool, cen["conta"], tipo="pessoal")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["cliente"] == "—"


def test_cliente_do_orcamento_prioriza_empresa_sobre_cliente(pool, cen):
    """Mesma regra `empresa or cliente` de _dados_orcamentos/_dados_contratos."""
    eid = _evento(pool, cen["conta"])
    _orc(pool, cen["conta"], eid, cliente="86994160050", empresa="Rafael Mendes")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["cliente"] == "Rafael Mendes"


# -------------------------------------------------------------------- filtros
def test_filtro_por_vendedor_via_membro_id(pool, cen):
    _evento(pool, cen["conta"], membro_id=cen["jacqueline"])
    _evento(pool, cen["conta"], membro_id=cen["pedro"])
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", str(cen["jacqueline"]), "")
    assert len(dados["linhas"]) == 1


def test_vendedor_invalido_nao_quebra_a_consulta(pool, cen):
    """Um valor não numérico no filtro (query param adulterado) não pode
    derrubar o relatório — só é tratado como "sem filtro"."""
    _evento(pool, cen["conta"])
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "abc", "")
    assert len(dados["linhas"]) == 1


def test_busca_por_cliente_do_orcamento_vinculado(pool, cen):
    e1 = _evento(pool, cen["conta"])
    e2 = _evento(pool, cen["conta"])
    _orc(pool, cen["conta"], e1, empresa="Talila Arrais")
    _orc(pool, cen["conta"], e2, empresa="Rafael Mendes")
    dados = rel._dados_agenda(pool, cen["conta"], "todos", "", "", "talila")
    assert len(dados["linhas"]) == 1 and dados["linhas"][0]["cliente"] == "Talila Arrais"


def test_periodo_filtra_por_data_de_inicio_do_evento(pool, cen):
    dentro = datetime.now(timezone.utc)
    fora = dentro - timedelta(days=200)
    _evento(pool, cen["conta"], inicio=dentro)
    _evento(pool, cen["conta"], inicio=fora)
    dados = rel._dados_agenda(pool, cen["conta"], "mes", "", "", "")
    assert len(dados["linhas"]) == 1


def test_outra_conta_nao_vaza_no_relatorio(pool, cen):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Rival') returning id").fetchone()[0]
        c.commit()
    _evento(pool, cen["conta"])
    assert rel._dados_agenda(pool, outra, "todos", "", "", "")["linhas"] == []


# ---------------------------------------------------------- aba e formulário
def test_aba_agenda_esta_registrada_em_tipos():
    assert "agenda" in rel.TIPOS
    assert rel.TIPOS["agenda"]["label"] == "Agenda"
