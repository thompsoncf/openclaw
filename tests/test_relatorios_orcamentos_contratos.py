"""Relatórios > Orçamentos / Contratos: uma aba por tabela, tudo visível, e o
filtro (Status, Vendedor, busca por cliente) corta em cima disso.

Pedido do dono, 24/08/2026: ver a lista de todos os orçamentos gerados — os
concluídos/assinados/fechados e os que não foram — e a lista de contratos
fechados/assinados. Mockup aprovado trocou "3 abas fatiando os dados" por
"2 abas com tudo dentro, filtro faz o corte" (feedback: "SO 2 ABAS ... E
APARECENDO TODOS"), mais um botão de imprimir por linha reaproveitando as
páginas públicas que orçamento e contrato já têm.
"""
import os
from datetime import date, timedelta

import pytest
from psycopg_pool import ConnectionPool

import web.painel_relatorios as rel

_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table orcamentos (id bigserial primary key, conta_id bigint, numero int,
  cliente text, empresa text, token text, setup_centavos bigint default 0,
  mensal_centavos bigint default 0, primeiro_ano_centavos bigint,
  status text default 'rascunho', criado_por text, aprovada_em timestamptz,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table contratos (id bigserial primary key, conta_id bigint, numero int,
  orcamento_id bigint, status text default 'enviado', valor_centavos bigint,
  assinado_em timestamptz, substitui_id bigint, token text,
  criado_em timestamptz default now());
"""

HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_relatorios_orc_ct_test"
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
        c.execute("truncate contas, membros, orcamentos, contratos restart identity")
        conta = c.execute("insert into contas (nome) values ('Prime Eventos') returning id").fetchone()[0]
        jacqueline = c.execute("insert into membros (conta_id, nome) values (%s,'Jacqueline') "
                               "returning id", (conta,)).fetchone()[0]
        pedro = c.execute("insert into membros (conta_id, nome) values (%s,'Pedro') "
                          "returning id", (conta,)).fetchone()[0]
        c.commit()
    return {"conta": conta, "jacqueline": jacqueline, "pedro": pedro}


def _orc(pool, conta, *, cliente="Cliente X", status="rascunho", criado_por=None,
        valor=100000, token="tok-orc", empresa=None, aprovada_em=None):
    with pool.connection() as c:
        numero = c.execute("select coalesce(max(numero),0)+1 from orcamentos where conta_id=%s",
                           (conta,)).fetchone()[0]
        oid = c.execute(
            """insert into orcamentos (conta_id, numero, cliente, empresa, status, criado_por,
                 primeiro_ano_centavos, token, aprovada_em)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta, numero, cliente, empresa, status, str(criado_por) if criado_por else None,
             valor, token, aprovada_em),
        ).fetchone()[0]
        c.commit()
    return oid


def _contrato(pool, conta, orcamento_id, *, status="enviado", valor=100000,
             substitui_id=None, token="tok-ct", assinado_em=None):
    with pool.connection() as c:
        numero = c.execute("select coalesce(max(numero),0)+1 from contratos where conta_id=%s",
                           (conta,)).fetchone()[0]
        cid = c.execute(
            """insert into contratos (conta_id, numero, orcamento_id, status, valor_centavos,
                 substitui_id, token, assinado_em) values (%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta, numero, orcamento_id, status, valor, substitui_id, token, assinado_em),
        ).fetchone()[0]
        c.commit()
    return cid


# ------------------------------------------------------------------ orçamentos
def test_lista_tudo_por_padrao_sem_filtro_de_status(pool, cen):
    _orc(pool, cen["conta"], status="rascunho")
    _orc(pool, cen["conta"], status="fechado")
    _orc(pool, cen["conta"], status="perdido")
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "", "", "")
    assert len(dados["linhas"]) == 3


def test_filtro_grupo_fechados_so_traz_status_fechado(pool, cen):
    _orc(pool, cen["conta"], status="rascunho")
    _orc(pool, cen["conta"], status="fechado", valor=50000)
    _orc(pool, cen["conta"], status="perdido")
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "fechados", "", "")
    assert len(dados["linhas"]) == 1
    assert dados["linhas"][0]["status"] == "Fechado"
    assert dados["total_centavos"] == 50000


def test_filtro_grupo_abertos_exclui_fechado_e_perdido(pool, cen):
    _orc(pool, cen["conta"], status="rascunho")
    _orc(pool, cen["conta"], status="negociando")
    _orc(pool, cen["conta"], status="fechado")
    _orc(pool, cen["conta"], status="perdido")
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "abertos", "", "")
    assert {l["status"] for l in dados["linhas"]} == {"Rascunho", "Negociando"}


def test_metricas_de_topo_ignoram_o_filtro_de_status_de_proposito(pool, cen):
    """O filtro corta a TABELA; as métricas continuam mostrando a distribuição
    inteira — é o que permite ver "9 em aberto" mesmo filtrando só Fechados."""
    _orc(pool, cen["conta"], status="fechado", valor=70000)
    _orc(pool, cen["conta"], status="rascunho", valor=30000)
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "fechados", "", "")
    metricas = dict(dados["metricas"])
    assert len(dados["linhas"]) == 1                 # tabela: só o fechado
    assert metricas["Fechados"].startswith("1 ")
    assert metricas["Em aberto"].startswith("1 ")     # a métrica não sumiu com o filtro


def test_filtro_por_vendedor(pool, cen):
    _orc(pool, cen["conta"], criado_por=cen["jacqueline"])
    _orc(pool, cen["conta"], criado_por=cen["pedro"])
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "", str(cen["jacqueline"]), "")
    assert len(dados["linhas"]) == 1 and dados["linhas"][0]["vendedor"] == "Jacqueline"


def test_busca_por_cliente(pool, cen):
    _orc(pool, cen["conta"], cliente="Talila Arrais")
    _orc(pool, cen["conta"], cliente="Rafael Mendes")
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "", "", "talila")
    assert len(dados["linhas"]) == 1 and dados["linhas"][0]["cliente"] == "Talila Arrais"


def test_coluna_cliente_prioriza_empresa_sobre_cliente(pool, cen):
    """Relato em produção (print real da Prime): "Cliente" aparecia vazio na
    maioria das linhas e o nome de verdade estava em "Empresa" — o formulário
    de criar orçamento troca o RÓTULO do campo `empresa` pra "Nome completo"
    quando o cliente é pessoa física, mas continua gravando na mesma coluna.
    Vira uma coluna só: `empresa or cliente`, mesma regra que
    `_espelhar_cliente` (web/painel_servicos.py) já usa."""
    _orc(pool, cen["conta"], cliente=None, empresa="Isabela Silva Mendes")
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["cliente"] == "Isabela Silva Mendes"
    assert "empresa" not in dados["linhas"][0]
    assert not any(c["chave"] == "empresa" for c in dados["colunas"])


def test_coluna_cliente_cai_pro_cliente_quando_empresa_vazia(pool, cen):
    _orc(pool, cen["conta"], cliente="86994160050", empresa=None)
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["cliente"] == "86994160050"


def test_busca_acha_nome_que_so_esta_em_empresa(pool, cen):
    """Antes a busca só olhava `cliente` — pra pessoa física (nome em
    `empresa`) buscar pelo próprio nome não achava nada."""
    _orc(pool, cen["conta"], cliente=None, empresa="Larissa Rakel Almeida Rodrigues")
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "", "", "larissa")
    assert len(dados["linhas"]) == 1


def test_aprovada_em_aparece_quando_o_cliente_assinou(pool, cen):
    """"Aprovada em" (`aprovada_em`) é o instante em que o CLIENTE assinou a
    proposta pública (web/proposta.py) — diferente de "Fechado em"
    (`atualizado_em` quando `status='fechado'`), que é quando o VENDEDOR fecha
    o negócio de fato. Um orçamento pode ficar dias em "aprovada" sem fechar."""
    assinado = HOJE - timedelta(days=1)
    _orc(pool, cen["conta"], status="aprovada", aprovada_em=assinado)
    _orc(pool, cen["conta"], status="rascunho")
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "", "", "")
    por_status = {l["status"]: l["aprovada_em"] for l in dados["linhas"]}
    assert por_status["Aprovada"] != "—"
    assert por_status["Rascunho"] == "—"


def test_vendedor_dono_mostra_o_nome_da_conta_nao_travessao(pool, cen):
    """Relato em produção: 2 orçamentos apareciam "sem vendedor" (—). Causa:
    `criado_por` guarda o id do membro OU a palavra 'dono' (quem abriu a conta,
    sem vendedor específico — mesma leitura de web/proposta.py) — e "dono" não
    bate com id de membro nenhum, então o join simplesmente não achava nada."""
    _orc(pool, cen["conta"], criado_por="dono")
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["vendedor"] == "Prime Eventos"


def test_nao_tem_coluna_fechado_em(pool, cen):
    """Relato do dono: a coluna "Fechado em" não fazia sentido — o status
    "Fechado" já aparece na etiqueta, e a data ficava vazia pra quase toda
    linha num relatório onde a maioria ainda está em aberto."""
    _orc(pool, cen["conta"], status="fechado")
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "", "", "")
    assert not any(c["chave"] == "fechado_em" for c in dados["colunas"])
    assert "fechado_em" not in dados["linhas"][0]


def test_acao_href_usa_o_token_do_orcamento(pool, cen):
    _orc(pool, cen["conta"], token="abc123")
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["acao_href"] == "/proposta/abc123"
    assert dados["acao"] is True and dados["acao_rotulo"]


def test_sem_token_nao_gera_link_quebrado(pool, cen):
    with pool.connection() as c:
        c.execute("insert into orcamentos (conta_id, cliente, token) values (%s,'Sem token',null)",
                  (cen["conta"],))
        c.commit()
    dados = rel._dados_orcamentos(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["acao_href"] is None


def test_outra_conta_nao_vaza_no_orcamentos(pool, cen):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Rival') returning id").fetchone()[0]
        c.commit()
    _orc(pool, cen["conta"])
    assert rel._dados_orcamentos(pool, outra, "todos", "", "", "")["linhas"] == []


# ------------------------------------------------------------------- contratos
def test_contratos_lista_tudo_por_padrao(pool, cen):
    o1 = _orc(pool, cen["conta"])
    o2 = _orc(pool, cen["conta"])
    _contrato(pool, cen["conta"], o1, status="assinado", token="t1")
    _contrato(pool, cen["conta"], o2, status="rescindido", token="t2")
    dados = rel._dados_contratos(pool, cen["conta"], "todos", "", "", "")
    assert len(dados["linhas"]) == 2


def test_contratos_aditivo_esconde_o_substituido(pool, cen):
    """O vivo é o que ninguém substituiu (substitui_id is null na tabela) — o
    índice parcial de finance/contrato.py (`por_orcamento`) só permite UM vivo
    por orçamento justamente porque o ANTIGO ganha o substitui_id apontando pro
    novo (liberando o lugar dele na constraint); o novo nasce com substitui_id
    nulo. Os dois não podem contar juntos na lista, senão o mesmo negócio
    aparece duplicado."""
    o1 = _orc(pool, cen["conta"])
    velho = _contrato(pool, cen["conta"], o1, status="assinado", token="v1")
    novo = _contrato(pool, cen["conta"], o1, status="assinado", token="v2")
    with pool.connection() as c:
        c.execute("update contratos set substitui_id=%s where id=%s", (novo, velho))
        c.commit()
    dados = rel._dados_contratos(pool, cen["conta"], "todos", "", "", "")
    assert len(dados["linhas"]) == 1
    assert dados["linhas"][0]["acao_href"] == "/contrato/v2"


def test_contratos_filtro_grupo_assinados_junta_assinado_e_cumprido(pool, cen):
    o1, o2, o3 = (_orc(pool, cen["conta"]) for _ in range(3))
    _contrato(pool, cen["conta"], o1, status="assinado")
    _contrato(pool, cen["conta"], o2, status="cumprido")
    _contrato(pool, cen["conta"], o3, status="enviado")
    dados = rel._dados_contratos(pool, cen["conta"], "todos", "assinados", "", "")
    assert {l["status"] for l in dados["linhas"]} == {"Assinado", "Cumprido"}


def test_contratos_vendedor_vem_do_orcamento_de_origem(pool, cen):
    o1 = _orc(pool, cen["conta"], criado_por=cen["pedro"])
    _contrato(pool, cen["conta"], o1, status="assinado")
    dados = rel._dados_contratos(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["vendedor"] == "Pedro"


def test_contratos_cliente_tambem_prioriza_empresa_do_orcamento_de_origem(pool, cen):
    o1 = _orc(pool, cen["conta"], cliente=None, empresa="Isabela Silva Mendes")
    _contrato(pool, cen["conta"], o1, status="assinado")
    dados = rel._dados_contratos(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["cliente"] == "Isabela Silva Mendes"


def test_contratos_filtro_por_vendedor_via_orcamento(pool, cen):
    o1 = _orc(pool, cen["conta"], criado_por=cen["pedro"])
    o2 = _orc(pool, cen["conta"], criado_por=cen["jacqueline"])
    _contrato(pool, cen["conta"], o1, status="assinado")
    _contrato(pool, cen["conta"], o2, status="assinado")
    dados = rel._dados_contratos(pool, cen["conta"], "todos", "", str(cen["pedro"]), "")
    assert len(dados["linhas"]) == 1 and dados["linhas"][0]["vendedor"] == "Pedro"


def test_contratos_vendedor_dono_mostra_o_nome_da_conta_nao_travessao(pool, cen):
    o1 = _orc(pool, cen["conta"], criado_por="dono")
    _contrato(pool, cen["conta"], o1, status="assinado")
    dados = rel._dados_contratos(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["vendedor"] == "Prime Eventos"


def test_contratos_acao_href_usa_o_token_do_contrato(pool, cen):
    o1 = _orc(pool, cen["conta"])
    _contrato(pool, cen["conta"], o1, status="assinado", token="xyz789")
    dados = rel._dados_contratos(pool, cen["conta"], "todos", "", "", "")
    assert dados["linhas"][0]["acao_href"] == "/contrato/xyz789"
