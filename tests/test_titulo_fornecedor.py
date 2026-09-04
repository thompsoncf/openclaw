"""O título A PAGAR passa a escolher FORNECEDOR de verdade — não mais "Cliente".

A REGRESSÃO QUE ISTO FECHA. No formulário de Títulos a pagar e receber o campo
de "quem" sempre se chamou "Cliente", mesmo com o Tipo em "A pagar". E não era
só rótulo errado: o backend (`web/portal.py:empresa_titulo_criar`) só tentava
ligar aquele nome a um cadastro quando o tipo era "receber" — então numa dívida
A PAGAR o nome digitado ali NEM CHEGAVA A SER SALVO. `contraparte` ficava vazia,
e a dívida virava "sem nome" no relatório do contador.

O conserto tem duas partes, e as duas estão testadas aqui:

  1. `contraparte` SEMPRE guarda o nome digitado — ligado a um cadastro ou não.
     Perder o "quem" de uma dívida é pior que não achar o cadastro.
  2. o campo agora liga no PAPEL certo: "a pagar" busca (e cria) em quem está
     marcado FORNECEDOR; "a receber" continua em quem está marcado CLIENTE.
     Os dois papéis vivem no MESMO cadastro (`finance.clientes`) — não há
     tabela de fornecedor separada, só uma coluna a mais.

A primeira metade inspeciona a FONTE da rota (o mesmo padrão que
tests/test_titulos_pagos_na_tela.py já usa pra este arquivo, porque
web/portal.py não tem harness de TestClient com sessão). A segunda metade
compõe as MESMAS funções que a rota chama, na mesma ordem, contra um banco de
verdade — provando que finance.clientes e finance.empresa encaixam certo pra
esta história.
"""
import inspect
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import clientes as cli
from finance import empresa as emp
from web import portal

_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "053_modulo_pj.sql",
              "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql",
              "197_titulo_acrescimo.sql",
              "057_natureza_lancamento.sql",
              "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql",
              "067_titulos_cliente.sql",
              "131_pessoa_cnpj.sql",
              "182_clientes_papel.sql")


@pytest.fixture(scope="module")
def pool():
    test_db_url = os.environ["TEST_DATABASE_URL"]
    p = ConnectionPool(test_db_url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Teste Titulo Fornecedor') "
            "returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def _como_a_rota_cria(pool, conta_id, *, tipo, nome_digitado, contraparte_livre=""):
    """A MESMA sequência de `empresa_titulo_criar`: acha pelo papel certo, cria
    se não achou (já com o papel certo), e contraparte sempre guarda o nome."""
    papel = "cliente" if tipo == "receber" else "fornecedor"
    cli_id = None
    nome_cli = nome_digitado.strip()
    if nome_cli:
        cli_id = cli.achar_cliente_por_nome(pool, conta_id, nome_cli, papel=papel)
        if cli_id is None:
            cli_id = cli.criar_cliente(pool, conta_id, nome_cli,
                                       eh_cliente=(papel == "cliente"),
                                       eh_fornecedor=(papel == "fornecedor"))
    novo = emp.criar_titulo(pool, conta_id, tipo, "Compra de material", 50000,
                            date(2026, 9, 10),
                            contraparte=contraparte_livre or nome_cli,
                            cliente_id=cli_id)
    # criar_titulo não devolve `contraparte` no dict — relê da lista, que é o
    # que a tela também usa pra desenhar a linha.
    achado = next(t for t in emp.listar_titulos(pool, conta_id, tipo=tipo, limite=200)
                  if t["id"] == novo["id"])
    return achado


# ---------------------------------------------------- a fonte da rota, travada

def test_a_rota_sempre_guarda_o_nome_em_contraparte():
    fonte = inspect.getsource(portal.empresa_titulo_criar)
    assert "contraparte=contraparte or nome_cli" in fonte, (
        "a regressão original: contraparte só era preenchida quando cli_id "
        "existia — voltou a perder o nome em título a pagar")
    # a versão velha que causava o bug: nome só entrava se já tivesse achado/ligado
    assert "nome_cli if cli_id else" not in fonte


def test_a_rota_deriva_o_papel_do_tipo_e_filtra_por_ele():
    fonte = inspect.getsource(portal.empresa_titulo_criar)
    assert 'papel = "cliente" if tipo_ok == "receber" else "fornecedor"' in fonte
    assert "achar_cliente_por_nome(pool, conta[0], nome_cli, papel=papel)" in fonte


def test_a_rota_cria_o_cadastro_novo_ja_com_o_papel_certo():
    fonte = inspect.getsource(portal.empresa_titulo_criar)
    assert "eh_cliente=(papel == \"cliente\")" in fonte
    assert "eh_fornecedor=(papel == \"fornecedor\")" in fonte


def test_o_campo_do_formulario_troca_de_rotulo_com_o_tipo():
    """JS: 'A pagar' pede Fornecedor, 'A receber' pede Cliente — sem isso o
    campo volta a dizer "Cliente" pra tudo, que foi o problema original."""
    assert "titTipoTroca" in portal._EMPRESA
    assert 'onchange="titTipoTroca(this)"' in portal._EMPRESA
    assert "tit-forn-dl" in portal._EMPRESA and "tit-cli-dl" in portal._EMPRESA


# ---------------------------------------------------- end-to-end, com banco de verdade

def test_titulo_a_pagar_liga_no_fornecedor_e_cria_com_o_papel_certo(pool, conta_id):
    t = _como_a_rota_cria(pool, conta_id, tipo="pagar",
                          nome_digitado="Distribuidora Rio Poti")
    assert t["contraparte"] == "Distribuidora Rio Poti"
    assert t["cliente_id"] is not None
    novo = cli.obter_cliente(pool, conta_id, t["cliente_id"])
    assert novo["eh_fornecedor"] is True
    assert novo["eh_cliente"] is False, "um fornecedor criado por aqui não devia nascer cliente"


def test_titulo_a_receber_continua_ligando_no_cliente(pool, conta_id):
    t = _como_a_rota_cria(pool, conta_id, tipo="receber", nome_digitado="Maria Helena")
    assert t["contraparte"] == "Maria Helena"
    novo = cli.obter_cliente(pool, conta_id, t["cliente_id"])
    assert novo["eh_cliente"] is True
    assert novo["eh_fornecedor"] is False


def test_titulo_a_pagar_nao_liga_em_quem_so_e_cliente_com_nome_igual(pool, conta_id):
    """O caso que motivou o filtro por papel: existe uma cliente chamada 'Ana
    Distribuidora', e uma dívida a pagar pra uma fornecedora de mesmo nome não
    pode acabar ligada na ficha da cliente."""
    cli.criar_cliente(pool, conta_id, "Ana Distribuidora")  # só cliente
    t = _como_a_rota_cria(pool, conta_id, tipo="pagar", nome_digitado="Ana Distribuidora")
    assert t["contraparte"] == "Ana Distribuidora", "o nome tem que ser salvo mesmo sem ligar"
    # um NOVO cadastro fornecedor foi criado — não teve como reusar o de cliente
    novo = cli.obter_cliente(pool, conta_id, t["cliente_id"])
    assert novo["eh_fornecedor"] is True


def test_titulo_a_pagar_reusa_fornecedor_ja_cadastrado_sem_duplicar(pool, conta_id):
    existente = cli.criar_cliente(pool, conta_id, "Papelaria Central",
                                  eh_cliente=False, eh_fornecedor=True)
    t = _como_a_rota_cria(pool, conta_id, tipo="pagar", nome_digitado="Papelaria Central")
    assert t["cliente_id"] == existente


def test_titulo_sem_nome_nao_cria_cadastro_nenhum(pool, conta_id):
    t = _como_a_rota_cria(pool, conta_id, tipo="pagar", nome_digitado="")
    assert t["cliente_id"] is None
    assert t["contraparte"] == ""


def test_contraparte_livre_ganha_do_nome_do_campo_cliente(pool, conta_id):
    """Quem edita a contraparte por fora (recorrência/detalhe) não perde a edição
    porque o campo de ligação também tinha algo digitado."""
    t = _como_a_rota_cria(pool, conta_id, tipo="pagar", nome_digitado="Fornecedor X",
                          contraparte_livre="Nome editado à mão")
    assert t["contraparte"] == "Nome editado à mão"


# ---------------------------------------------------- a mesma trava, via WhatsApp
#
# O caminho do bot (finance.tools_pj) tinha a MESMA falha em potencial: a busca
# por contraparte não filtrava por papel, então um título a pagar podia casar
# com alguém que só é cliente. Ali a contraparte já era sempre salva (isso nunca
# quebrou nesse caminho) — o que faltava era o filtro de papel na busca.

def test_ferramenta_do_bot_titulo_a_pagar_ignora_quem_so_e_cliente(pool, conta_id):
    """O bot (diferente da rota web) NÃO cria cadastro novo — só liga se achar.
    O que este teste trava é que ele não liga na ficha ERRADA: sem o filtro de
    papel, uma dívida a pagar podia acabar na ficha de alguém que só é cliente."""
    from finance.tools_pj import construir_ferramentas_pj

    cli.criar_cliente(pool, conta_id, "Oficina Mecânica Bot")  # só cliente
    ferramentas = construir_ferramentas_pj(pool, conta_id)
    criar = next(f for f in ferramentas if f.nome == "criar_titulo")
    criar.executar({"tipo": "pagar", "descricao": "Peças", "valor": 300,
                    "vencimento": "10/09/2026", "contraparte": "Oficina Mecânica Bot"})

    achado = next(t for t in emp.listar_titulos(pool, conta_id, tipo="pagar", limite=200)
                  if t["contraparte"] == "Oficina Mecânica Bot")
    assert achado["contraparte"] == "Oficina Mecânica Bot", "o nome tem que ser salvo mesmo sem ligar"
    assert achado["cliente_id"] is None, "não podia ligar na ficha de quem só é cliente"


def test_ferramenta_do_bot_titulo_a_receber_liga_no_cliente(pool, conta_id):
    from finance.tools_pj import construir_ferramentas_pj

    cli.criar_cliente(pool, conta_id, "Honorário Fixo Bot")
    ferramentas = construir_ferramentas_pj(pool, conta_id)
    criar = next(f for f in ferramentas if f.nome == "criar_titulo")
    criar.executar({"tipo": "receber", "descricao": "Honorário", "valor": 800,
                    "vencimento": "10/09/2026", "contraparte": "Honorário Fixo Bot"})

    achado = next(t for t in emp.listar_titulos(pool, conta_id, tipo="receber", limite=200)
                  if t["contraparte"] == "Honorário Fixo Bot")
    assert achado["cliente_id"] is not None
