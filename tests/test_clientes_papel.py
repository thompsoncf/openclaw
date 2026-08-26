"""O PAPEL do cadastro em `clientes`: CLIENTE e/ou FORNECEDOR.

POR QUE ISTO EXISTE. A aba Clientes deixa cadastrar qualquer PF/PJ, mas até
20/08/2026 não existia como dizer QUE TIPO de relação era essa. O efeito
aparecia mais adiante, no card de Títulos a pagar e receber: o campo de "quem"
numa dívida A PAGAR estava rotulado "Cliente" mesmo sendo um fornecedor, e o
nome digitado ali NEM CHEGAVA A SER SALVO — só era ligado quando o título era
A RECEBER.

NÃO É ESCOLHA ÚNICA. A mesma empresa pode te vender material E te contratar
pra um serviço ao mesmo tempo — mesmo padrão de `contas.vende_produto` e
`contas.vende_servico`, já independentes hoje. `eh_cliente` e `eh_fornecedor`
são dois booleanos, não um enum.

`eh_cliente` nasce TRUE em todo mundo (decisão do dono: "só cliente como
está" — ninguém que já cadastra hoje muda de comportamento sem pedir).
`eh_fornecedor` nasce FALSE.
"""
import os
import re
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import clientes as cli

_MIGRACOES = ("064_clientes_lojista.sql",
              "066_pessoas_identidade.sql",
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
def dono_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Teste Clientes Papel') "
            "returning id"
        ).fetchone()[0]
        c.commit()
    return cid


# ---------------------------------------------------- o padrão preserva quem já existe

def test_criar_cliente_padrao_e_so_cliente(pool, dono_id):
    """O padrão de sempre, sem pedir nada explícito — é o que faz TODO cadastro
    existente continuar exatamente como estava."""
    cid = cli.criar_cliente(pool, dono_id, "Ana Beatriz Souza")
    c = cli.obter_cliente(pool, dono_id, cid)
    assert c["eh_cliente"] is True
    assert c["eh_fornecedor"] is False


def test_criar_cliente_pode_marcar_fornecedor_tambem(pool, dono_id):
    """Não é escolha única: os dois podem estar marcados na mesma pessoa."""
    cid = cli.criar_cliente(pool, dono_id, "Construtora Vale Verde",
                            eh_cliente=True, eh_fornecedor=True)
    c = cli.obter_cliente(pool, dono_id, cid)
    assert c["eh_cliente"] is True
    assert c["eh_fornecedor"] is True


def test_criar_cliente_so_fornecedor(pool, dono_id):
    """O caso que a rota de título a pagar passa a usar: um fornecedor puro,
    que nunca compra nada — eh_cliente explicitamente False."""
    cid = cli.criar_cliente(pool, dono_id, "Distribuidora Rio Poti",
                            eh_cliente=False, eh_fornecedor=True)
    c = cli.obter_cliente(pool, dono_id, cid)
    assert c["eh_cliente"] is False
    assert c["eh_fornecedor"] is True


# ---------------------------------------------------- listar por papel

def test_listar_sem_filtro_traz_todos(pool, dono_id):
    cli.criar_cliente(pool, dono_id, "Só Cliente A")
    cli.criar_cliente(pool, dono_id, "Só Fornecedor B", eh_cliente=False, eh_fornecedor=True)
    nomes = {c["nome"] for c in cli.listar_clientes(pool, dono_id)}
    assert {"Só Cliente A", "Só Fornecedor B"} <= nomes


def test_listar_papel_cliente_nao_traz_so_fornecedor(pool, dono_id):
    cli.criar_cliente(pool, dono_id, "Cliente Puro C")
    cli.criar_cliente(pool, dono_id, "Fornecedor Puro D", eh_cliente=False, eh_fornecedor=True)
    nomes = {c["nome"] for c in cli.listar_clientes(pool, dono_id, papel="cliente")}
    assert "Cliente Puro C" in nomes
    assert "Fornecedor Puro D" not in nomes


def test_listar_papel_fornecedor_nao_traz_so_cliente(pool, dono_id):
    cli.criar_cliente(pool, dono_id, "Cliente Puro E")
    cli.criar_cliente(pool, dono_id, "Fornecedor Puro F", eh_cliente=False, eh_fornecedor=True)
    nomes = {c["nome"] for c in cli.listar_clientes(pool, dono_id, papel="fornecedor")}
    assert "Fornecedor Puro F" in nomes
    assert "Cliente Puro E" not in nomes


def test_quem_e_os_dois_aparece_nos_dois_filtros(pool, dono_id):
    cli.criar_cliente(pool, dono_id, "Dos Dois G", eh_cliente=True, eh_fornecedor=True)
    so_cliente = {c["nome"] for c in cli.listar_clientes(pool, dono_id, papel="cliente")}
    so_fornecedor = {c["nome"] for c in cli.listar_clientes(pool, dono_id, papel="fornecedor")}
    assert "Dos Dois G" in so_cliente
    assert "Dos Dois G" in so_fornecedor


# ---------------------------------------------------- achar por nome respeitando o papel
#
# É a trava que fecha o bug original: um título A PAGAR não pode casar com
# alguém que só é cliente (nunca foi marcado fornecedor), e vice-versa —
# senão a dívida liga na ficha errada, e a ficha errada é pior que nenhuma.

def test_achar_por_nome_sem_papel_acha_qualquer_um(pool, dono_id):
    cli.criar_cliente(pool, dono_id, "Alvo Sem Filtro H", eh_cliente=False, eh_fornecedor=True)
    achado = cli.achar_cliente_por_nome(pool, dono_id, "Alvo Sem Filtro H")
    assert achado is not None


def test_achar_por_nome_papel_fornecedor_ignora_quem_so_e_cliente(pool, dono_id):
    cli.criar_cliente(pool, dono_id, "Padaria Cliente I")  # só cliente, default
    achado = cli.achar_cliente_por_nome(pool, dono_id, "Padaria Cliente I", papel="fornecedor")
    assert achado is None


def test_achar_por_nome_papel_cliente_ignora_quem_so_e_fornecedor(pool, dono_id):
    cli.criar_cliente(pool, dono_id, "Grafica Fornecedor J", eh_cliente=False, eh_fornecedor=True)
    achado = cli.achar_cliente_por_nome(pool, dono_id, "Grafica Fornecedor J", papel="cliente")
    assert achado is None


def test_achar_por_nome_acha_quem_tem_o_papel_certo(pool, dono_id):
    cid = cli.criar_cliente(pool, dono_id, "Distribuidora Rio Poti K",
                            eh_cliente=False, eh_fornecedor=True)
    achado = cli.achar_cliente_por_nome(pool, dono_id, "Distribuidora Rio Poti K",
                                        papel="fornecedor")
    assert achado == cid


def test_achar_por_nome_com_papel_ainda_desempata_por_exato(pool, dono_id):
    """Duas fornecedoras com nomes parecidos: o exato ganha do parcial, mesmo
    com o filtro de papel ligado — a regra de desempate não pode quebrar."""
    cid_exato = cli.criar_cliente(pool, dono_id, "Rio Poti", eh_cliente=False, eh_fornecedor=True)
    cli.criar_cliente(pool, dono_id, "Rio Poti Distribuidora Ltda",
                      eh_cliente=False, eh_fornecedor=True)
    achado = cli.achar_cliente_por_nome(pool, dono_id, "Rio Poti", papel="fornecedor")
    assert achado == cid_exato


# ---------------------------------------------------- atualizar o papel

def test_atualizar_cliente_marca_fornecedor_em_quem_ja_existia(pool, dono_id):
    """O caso do dia a dia: um cliente antigo passa a vender pra empresa também
    — o dono edita o cadastro e marca a segunda caixinha, sem duplicar nada."""
    cid = cli.criar_cliente(pool, dono_id, "Cliente Que Virou Fornecedor L")
    ok = cli.atualizar_cliente(pool, dono_id, cid, eh_fornecedor=True)
    assert ok
    c = cli.obter_cliente(pool, dono_id, cid)
    assert c["eh_cliente"] is True
    assert c["eh_fornecedor"] is True


def test_atualizar_cliente_pode_desmarcar_cliente(pool, dono_id):
    cid = cli.criar_cliente(pool, dono_id, "Vira Só Fornecedor M")
    cli.atualizar_cliente(pool, dono_id, cid, eh_cliente=False, eh_fornecedor=True)
    c = cli.obter_cliente(pool, dono_id, cid)
    assert c["eh_cliente"] is False
    assert c["eh_fornecedor"] is True


def test_atualizar_cliente_nao_mexe_no_papel_se_nao_for_pedido(pool, dono_id):
    """Editar telefone não pode apagar o papel sem querer — só os campos
    passados em **campos entram no UPDATE."""
    cid = cli.criar_cliente(pool, dono_id, "Só Telefone Muda N", eh_fornecedor=True)
    cli.atualizar_cliente(pool, dono_id, cid, telefone="86999990000")
    c = cli.obter_cliente(pool, dono_id, cid)
    assert c["eh_cliente"] is True
    assert c["eh_fornecedor"] is True


# ---------------------------------------------------- reaproveitar relação existente

def test_puxar_ou_criar_reusa_sem_mudar_o_papel_ja_marcado(pool, dono_id):
    """achar_ou_criar (fluxo de venda no PDV) não pode rebaixar um fornecedor pra
    só-cliente sem querer, só porque ele também comprou um dia."""
    cid = cli.criar_cliente(pool, dono_id, "Rebaixamento Não O", cpf="11122233396",
                            eh_cliente=False, eh_fornecedor=True)
    de_novo = cli.achar_ou_criar(pool, dono_id, "Rebaixamento Não O", cpf="11122233396")
    assert de_novo == cid
    c = cli.obter_cliente(pool, dono_id, cid)
    assert c["eh_fornecedor"] is True, "o papel de fornecedor sumiu ao reusar a relação"


# ---------------------------------------------------- as rotas passam o papel adiante
#
# web/portal.py não tem harness de TestClient com sessão (mesma limitação que
# tests/test_titulos_pagos_na_tela.py já documenta pra este arquivo) — então a
# fiação das rotas é travada inspecionando a FONTE, no mesmo padrão.

def test_rota_de_cadastro_recebe_e_repassa_o_papel():
    import inspect
    from web import portal
    fonte = inspect.getsource(portal.painel_clientes_novo)
    assert 'eh_cliente: str = Form("")' in fonte
    assert 'eh_fornecedor: str = Form("")' in fonte
    assert "eh_cliente=bool(eh_cliente)" in fonte
    assert "eh_fornecedor=bool(eh_fornecedor)" in fonte


def test_rota_de_edicao_recebe_e_repassa_o_papel():
    import inspect
    from web import portal
    fonte = inspect.getsource(portal.painel_cliente_editar)
    assert '"eh_cliente": bool(eh_cliente)' in fonte
    assert '"eh_fornecedor": bool(eh_fornecedor)' in fonte


def test_rota_de_listagem_aceita_o_filtro_de_papel():
    import inspect
    from web import portal
    fonte = inspect.getsource(portal.painel_clientes)
    assert 'papel: str = ""' in fonte
    assert 'papel=papel_filtro or None' in fonte


# ---------------------------------------------------- dedup sem documento (26/08)
#
# `resolver_pessoa` so' funde por CPF/CNPJ exato — sem documento (o caso comum
# de um lead de WhatsApp: so' nome e telefone), toda chamada criava uma PESSOA
# nova, e como `puxar_ou_criar_cliente` casa a relacao por pessoa_id, um
# `pessoa_id` novo garantia um `clientes` novo tambem. Salvar o mesmo orcamento
# (`_espelhar_cliente`) ou o mesmo cadastro do formulario dez vezes sem CPF
# duplicava o cliente dez vezes. A correcao reusa, dentro do MESMO lojista, o
# padrao que `achar_ou_criar` (fluxo do PDV) ja usava: sem documento, telefone
# igual dentro da propria loja reusa a relacao em vez de criar outra.

def test_criar_cliente_sem_documento_reusa_pelo_telefone_na_mesma_loja(pool, dono_id):
    cid = cli.criar_cliente(pool, dono_id, "Josiany Rayra Soares dos Santos",
                            telefone="86998192489")
    de_novo = cli.criar_cliente(pool, dono_id, "Josiany Rayra Soares dos Santos",
                                telefone="(86) 99819-2489")
    assert de_novo == cid, "salvar o mesmo lead sem CPF de novo duplicou o cliente"
    assert cli.contar_clientes(pool, dono_id) == 1


def test_criar_cliente_sem_documento_e_sem_telefone_nao_tem_como_casar(pool, dono_id):
    """Sem CPF/CNPJ e sem telefone nao existe chave nenhuma pra reusar — os dois
    continuam virando cadastros distintos, como sempre foi."""
    c1 = cli.criar_cliente(pool, dono_id, "Sem Contato Nenhum P")
    c2 = cli.criar_cliente(pool, dono_id, "Sem Contato Nenhum P")
    assert c1 != c2


def test_criar_cliente_com_documento_nao_muda_pra_dedup_por_telefone(pool, dono_id):
    """Com CPF/CNPJ, quem manda e' o documento (chave forte) — dois clientes
    diferentes que por acaso compartilham telefone (familia) nao podem ser
    fundidos so' porque o numero bate."""
    c1 = cli.criar_cliente(pool, dono_id, "Irmã Uma Q", telefone="86988887777",
                           cpf="52998224725")
    c2 = cli.criar_cliente(pool, dono_id, "Irmã Duas Q", telefone="86988887777",
                           cpf="11144477735")
    assert c1 != c2


def test_dedup_por_telefone_sem_documento_nao_vaza_pra_outro_lojista(pool):
    """O dedup e' `buscar_por_telefone(dono_id, ...)` — isolado por loja. O
    mesmo telefone em duas lojas diferentes tem que continuar sendo dois
    cadastros, um em cada base."""
    with pool.connection() as c:
        loja_a = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Loja A Dedup') returning id"
        ).fetchone()[0]
        loja_b = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Loja B Dedup') returning id"
        ).fetchone()[0]
        c.commit()
    ca = cli.criar_cliente(pool, loja_a, "Cliente Das Duas Lojas R", telefone="86977776666")
    cb = cli.criar_cliente(pool, loja_b, "Cliente Das Duas Lojas R", telefone="86977776666")
    assert ca != cb
    assert cli.obter_cliente(pool, loja_a, ca) is not None
    assert cli.obter_cliente(pool, loja_b, cb) is not None


def test_render_nao_pisa_no_papel_do_operador_logado():
    """A colisão de nome que quase aconteceu: `_render` seta `ctx["papel"]` como
    o papel do OPERADOR logado (dono/gestor/vendedor) via `ctx.setdefault`, e o
    menu inteiro depende disso (`_dono = papel=='dono'`). Se a rota de Clientes
    passasse `papel=` pro filtro (cliente/fornecedor) pro contexto, o
    `setdefault` NÃO sobrescreveria — a página inteira acharia que o operador
    logado tem o papel "cliente" ou "fornecedor", e o menu sumiria. Por isso o
    filtro usa a chave `papel_filtro`, nunca `papel`."""
    import inspect
    from web import portal
    fonte = inspect.getsource(portal.painel_clientes)
    assert "papel_filtro=papel_filtro" in fonte
    assert not re.search(r"_render\(\"clientes\".*?\bpapel=papel_filtro\b", fonte, re.S), (
        "a rota está passando o filtro pro contexto sob a chave `papel` — "
        "isso pisa no papel do operador logado (dono/gestor/vendedor)")
