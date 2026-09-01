"""Contas pagas / Contas recebidas passaram a ler o CAIXA, não o título baixado.

O incidente que originou a mudança, conferido em produção em 01/09/2026:
**Contas pagas estava vazia em todas as contas do sistema, desde sempre.** Na
Prime (conta 34) saíram R$ 34.232,86 do caixa em agosto — 53 pagamentos — e a
tela mostrava R$ 0,00.

Não era bug de consulta, era desenho. As duas abas liam `titulos` com
status='pago', e título só vira 'pago' quando alguém clica no botão. O
levantamento mostrou que ninguém nunca clicou: das 40 linhas de `titulos` da
produção inteira, 2 estavam pagas — e as duas foram baixadas pelo próprio
sistema (o fluxo do sinal). Enquanto isso o dinheiro real entra sozinho pelos
dois caminhos que nascem direto em `lancamentos`: a importação do extrato (OFX)
e a foto do comprovante mandada no WhatsApp.

O que este teste protege:

  * **a aba enche mesmo sem título nenhum.** É o caso exato do incidente, e o
    teste que teria pego: cenário com lançamentos e ZERO títulos tem que mostrar
    linha. Se alguém "voltar a ler titulos", isto quebra primeiro;
  * **o que aparecia antes continua aparecendo.** Título baixado gera lançamento
    com `origem='titulo'`, então a troca não perde as linhas que a aba já tinha —
    a regra 0 do CLAUDE.md vale pra tela também;
  * pagar lê despesa e receber lê receita, sem cruzar;
  * só `natureza='empresa'` entra na conta — despesa pessoal do dono não é conta
    paga da empresa;
  * **o que está "a definir" não entra na soma mas também não fica calado.** Em
    produção isso é dinheiro de verdade (a conta 3 tem R$ 20.901,18 de despesa
    sem natureza definida). Somar seria mentir; sumir sem avisar seria repetir o
    erro que a aba acabou de deixar de cometer;
  * a origem vira rótulo legível com cor, e origem desconhecida não derruba a
    tela;
  * as abas de COMPROMISSO (Contas a pagar / a receber) continuam lendo
    `titulos` — lá elas estão certas, e trocar a fonte delas seria o erro
    oposto.
"""
import os
import re
from datetime import date, timedelta

import pytest
from psycopg_pool import ConnectionPool

import web.painel_relatorios as rel

_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table clientes (id bigserial primary key, dono_id bigint, nome text);
create table lancamentos (
  id bigserial primary key, conta_id bigint not null, cliente_id bigint,
  tipo text not null, valor_centavos bigint not null, categoria text not null,
  descricao text not null default '', data date not null,
  origem text not null default 'manual', natureza text);
"""

HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_relatorios_caixa_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


@pytest.fixture
def conta(pool):
    with pool.connection() as c:
        c.execute("truncate contas, clientes, lancamentos restart identity")
        cid = c.execute("insert into contas (nome) values ('Prime Eventos') "
                        "returning id").fetchone()[0]
        c.commit()
    return cid


def _lanc(pool, conta_id, *, tipo="despesa", valor=10000, categoria="Serviços",
          descricao="Pagamento", data=None, origem="foto", natureza="empresa",
          cliente_id=None):
    with pool.connection() as c:
        lid = c.execute(
            """insert into lancamentos (conta_id, cliente_id, tipo, valor_centavos,
                 categoria, descricao, data, origem, natureza)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, cliente_id, tipo, valor, categoria, descricao,
             data or HOJE, origem, natureza)).fetchone()[0]
        c.commit()
    return lid


# ── o caso do incidente ──────────────────────────────────────────────────────
def test_contas_pagas_enche_sem_titulo_nenhum(pool, conta):
    """O caso EXATO de 01/09/2026: a Prime tinha 53 pagamentos e zero títulos
    baixados, e a tela mostrava R$ 0,00. Não existe tabela `titulos` neste banco
    de teste de propósito — se a aba voltar a depender dela, quebra aqui."""
    _lanc(pool, conta, valor=36000, descricao="2ª quinzena — Iasmin Flor")
    _lanc(pool, conta, valor=25823, descricao="Mensalidade Security", origem="extrato")
    d = rel._dados_caixa(pool, conta, "pagar", "todos")
    assert len(d["linhas"]) == 2, "a aba tem que mostrar o que saiu do caixa"
    assert d["total_centavos"] == 61823
    assert d["label"] == "Contas pagas"


def test_o_titulo_baixado_continua_aparecendo(pool, conta):
    """A troca de fonte não pode perder o que a aba já mostrava. `dar_baixa_titulo`
    grava o lançamento com origem='titulo', então ele entra pela porta nova."""
    _lanc(pool, conta, tipo="receita", valor=200000, origem="titulo",
          descricao="Evento — Beatriz do Carmo Brito · Sinal")
    d = rel._dados_caixa(pool, conta, "receber", "todos")
    assert [r["descricao"] for r in d["linhas"]] == \
        ["Evento — Beatriz do Carmo Brito · Sinal"]
    assert d["linhas"][0]["origem"] == "Título"


def test_a_metrica_diz_quantos_fecharam_o_ciclo(pool, conta):
    """É o número que mostra o buraco: quanto do caixa passou por um compromisso
    em vez de só cair na conta. Na Prime real era 2 de 72."""
    _lanc(pool, conta, tipo="receita", origem="titulo")
    _lanc(pool, conta, tipo="receita", origem="extrato")
    _lanc(pool, conta, tipo="receita", origem="foto")
    d = rel._dados_caixa(pool, conta, "receber", "todos")
    assert ("Quitaram um título", "1") in d["metricas"]
    assert ("Nº de entradas", "3") in d["metricas"]


# ── pagar x receber ──────────────────────────────────────────────────────────
def test_pagar_le_despesa_e_receber_le_receita(pool, conta):
    _lanc(pool, conta, tipo="despesa", valor=1000, descricao="Saiu")
    _lanc(pool, conta, tipo="receita", valor=2000, descricao="Entrou")
    pagas = rel._dados_caixa(pool, conta, "pagar", "todos")
    receb = rel._dados_caixa(pool, conta, "receber", "todos")
    assert [r["descricao"] for r in pagas["linhas"]] == ["Saiu"]
    assert [r["descricao"] for r in receb["linhas"]] == ["Entrou"]
    assert receb["label"] == "Contas recebidas"


def test_o_rotulo_da_coluna_e_do_valor_seguem_o_lado(pool, conta):
    pagas = rel._dados_caixa(pool, conta, "pagar", "todos")
    receb = rel._dados_caixa(pool, conta, "receber", "todos")
    assert pagas["colunas"][0]["rotulo"] == "Pagamento"
    assert receb["colunas"][0]["rotulo"] == "Recebimento"
    assert pagas["colunas"][1]["rotulo"] == "Fornecedor / descrição"
    assert receb["colunas"][1]["rotulo"] == "Cliente / descrição"
    assert pagas["colunas"][-1]["rotulo"] == "Valor pago"
    assert receb["colunas"][-1]["rotulo"] == "Valor recebido"


# ── natureza ─────────────────────────────────────────────────────────────────
def test_pessoal_nao_e_conta_da_empresa(pool, conta):
    _lanc(pool, conta, valor=5000, natureza="empresa", descricao="Da empresa")
    _lanc(pool, conta, valor=9900, natureza="pessoal", descricao="Do dono")
    d = rel._dados_caixa(pool, conta, "pagar", "todos")
    assert [r["descricao"] for r in d["linhas"]] == ["Da empresa"]
    assert d["total_centavos"] == 5000


def test_a_definir_fica_fora_da_conta_mas_vira_aviso(pool, conta):
    """Somar seria mentir (pode ser despesa pessoal). Sumir calado seria repetir o
    erro que esta aba acabou de deixar de cometer — é dinheiro de verdade."""
    _lanc(pool, conta, valor=5000, natureza="empresa")
    _lanc(pool, conta, valor=2090118, natureza=None, descricao="Sem definir")
    d = rel._dados_caixa(pool, conta, "pagar", "todos")
    assert d["total_centavos"] == 5000, "o indefinido não pode entrar na soma"
    assert len(d["linhas"]) == 1
    assert "aviso_config" in d, "o indefinido não pode sumir calado"
    assert "20.901,18" in d["aviso_config"]
    assert "a definir" in d["aviso_config"]


def test_sem_indefinido_nao_tem_aviso(pool, conta):
    _lanc(pool, conta, natureza="empresa")
    assert "aviso_config" not in rel._dados_caixa(pool, conta, "pagar", "todos")


def test_o_aviso_conta_so_o_indefinido_do_mesmo_lado(pool, conta):
    """Receita a definir não é aviso da aba de pagamento — senão a tela cobra do
    dono uma pendência que não é dali."""
    _lanc(pool, conta, tipo="receita", valor=7700, natureza=None)
    assert "aviso_config" not in rel._dados_caixa(pool, conta, "pagar", "todos")
    assert "aviso_config" in rel._dados_caixa(pool, conta, "receber", "todos")


# ── período ──────────────────────────────────────────────────────────────────
def test_o_periodo_recorta(pool, conta):
    _lanc(pool, conta, valor=1000, data=HOJE, descricao="De hoje")
    _lanc(pool, conta, valor=2000, data=HOJE - timedelta(days=400), descricao="Ano passado")
    todos = rel._dados_caixa(pool, conta, "pagar", "todos")
    ano = rel._dados_caixa(pool, conta, "pagar", "ano")
    assert len(todos["linhas"]) == 2
    assert [r["descricao"] for r in ano["linhas"]] == ["De hoje"]


def test_a_conta_do_vizinho_nao_aparece(pool, conta):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Outra') "
                          "returning id").fetchone()[0]
        c.commit()
    _lanc(pool, outra, valor=999999, descricao="Da outra empresa")
    _lanc(pool, conta, valor=1000, descricao="Minha")
    d = rel._dados_caixa(pool, conta, "pagar", "todos")
    assert [r["descricao"] for r in d["linhas"]] == ["Minha"]


# ── a coluna Entrou por ──────────────────────────────────────────────────────
@pytest.mark.parametrize("origem,rotulo,cor", [
    ("extrato", "Extrato", "info"),
    ("foto", "Comprovante", "neutro"),
    ("titulo", "Título", "ok"),
    ("balcao", "Balcão", "ok"),
    ("manual", "Manual", "neutro"),
])
def test_a_origem_vira_rotulo_legivel(pool, conta, origem, rotulo, cor):
    _lanc(pool, conta, origem=origem)
    linha = rel._dados_caixa(pool, conta, "pagar", "todos")["linhas"][0]
    assert linha["origem"] == rotulo
    assert linha["origem_cor"] == cor


def test_origem_desconhecida_nao_derruba_a_tela(pool, conta):
    """Origem nova (integração futura) não pode virar KeyError numa tela de
    dinheiro."""
    _lanc(pool, conta, origem="pix_automatico")
    linha = rel._dados_caixa(pool, conta, "pagar", "todos")["linhas"][0]
    assert linha["origem"] == "Pix_automatico"
    assert linha["origem_cor"] == "neutro"


def test_a_coluna_origem_e_tag(pool, conta):
    cols = {c["chave"]: c for c in rel._dados_caixa(pool, conta, "pagar", "todos")["colunas"]}
    assert cols["origem"]["tag"] is True, \
        "sem tag=True o template imprime o texto cru, sem a pílula colorida"


# ── o nome na linha ──────────────────────────────────────────────────────────
def test_a_descricao_vazia_cai_no_nome_do_cliente(pool, conta):
    """Venda de balcão tem cliente cadastrado e às vezes descrição vazia; sem esta
    queda a linha aparece como '—' e ninguém sabe de quem é."""
    with pool.connection() as c:
        cli = c.execute("insert into clientes (dono_id, nome) values (%s,'Bianca "
                        "Oliveira') returning id", (conta,)).fetchone()[0]
        c.commit()
    _lanc(pool, conta, tipo="receita", descricao="   ", cliente_id=cli)
    linha = rel._dados_caixa(pool, conta, "receber", "todos")["linhas"][0]
    assert linha["descricao"] == "Bianca Oliveira"


def test_sem_descricao_e_sem_cliente_nao_fica_vazio(pool, conta):
    _lanc(pool, conta, descricao="")
    assert rel._dados_caixa(pool, conta, "pagar", "todos")["linhas"][0]["descricao"] == "—"


def test_a_elastica_da_aba_e_a_descricao(pool, conta):
    flex = [c["chave"] for c in
            rel._dados_caixa(pool, conta, "pagar", "todos")["colunas"] if c["flex"]]
    assert flex == ["descricao"], \
        "uma elástica só, e é a de nome livre (ver test_relatorios_largura)"


def test_a_mais_recente_vem_primeiro(pool, conta):
    _lanc(pool, conta, data=HOJE - timedelta(days=5), descricao="Velha")
    _lanc(pool, conta, data=HOJE, descricao="Nova")
    d = rel._dados_caixa(pool, conta, "pagar", "todos")
    assert [r["descricao"] for r in d["linhas"]] == ["Nova", "Velha"]


def test_a_aba_vazia_nao_quebra(pool, conta):
    d = rel._dados_caixa(pool, conta, "pagar", "todos")
    assert d["linhas"] == []
    assert d["total_centavos"] == 0
    assert ("Maior valor", "R$ 0,00") in d["metricas"]


# ── o outro lado não pode mudar ──────────────────────────────────────────────
def test_as_abas_de_compromisso_continuam_lendo_titulos():
    """Contas a pagar / a receber respondem "o que eu ainda devo" — isso é
    título, não caixa. Trocar a fonte delas seria o erro simétrico ao que
    acabamos de corrigir."""
    fonte = open(rel.__file__, encoding="utf-8").read()
    corpo = fonte.split("def _dados_titulos_abertos")[1].split("\ndef ")[0]
    assert "emp.listar_titulos" in corpo, \
        "as abas de compromisso têm que continuar lendo titulos"
    assert 'status="aberto"' in corpo


def test_as_abas_de_caixa_nao_leem_mais_titulo():
    fonte = open(rel.__file__, encoding="utf-8").read()
    corpo = fonte.split("def _dados_caixa")[1].split("\ndef ")[0]
    assert "listar_titulos" not in corpo, \
        "Contas pagas/recebidas voltaram a depender do título baixado — é o bug"
    assert re.search(r"from lancamentos", corpo), "tem que ler o caixa"


def test_as_duas_abas_apontam_pro_dados_caixa():
    assert rel.TIPOS["pagas"]["label"] == "Contas pagas"
    assert rel.TIPOS["recebidas"]["label"] == "Contas recebidas"
    fonte = open(rel.__file__, encoding="utf-8").read()
    for chave in ("pagas", "recebidas"):
        linha = [l for l in fonte.splitlines() if f'"{chave}": {{' in l][0]
        assert "_dados_caixa" in linha, f"a aba {chave} não foi religada"
