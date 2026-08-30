"""Cadastro de cliente/fornecedor: salvar de novo ATUALIZA, nunca duplica.

Levantamento no cadastro da Prime Eventos em 29/08/2026: 23 registros para 19
pessoas. Os 5 excedentes vieram de dois padrões, e nenhuma regra sozinha pega os
dois:

  Ana Clara ×3   telefone igual, nome igual   -> tentativa repetida no orçamento
  Victoria  ×2   telefone igual, nome com typo
  Gilvan    ×2   nome igual, o de fornecedor SEM telefone
  Ronaldo   ×2   idem, e ainda com typo no sobrenome ("VAZ"/"VEZ")

A causa é uma só: em nenhum caminho a tela permitia dizer "essa pessoa já
existe, atualiza ela". Reusar devolvia o id e ignorava o resto — inclusive o
papel. Quem queria marcar um cliente como fornecedor não tinha como, e
cadastrava de novo; por isso os registros de fornecedor estão vazios.
"""
import os
import uuid
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import clientes as cli

# mesmo baseline do test_clientes_papel: init_schema não cria `clientes`
_MIGRACOES = ("064_clientes_lojista.sql", "066_pessoas_identidade.sql",
              "131_pessoa_cnpj.sql", "182_clientes_papel.sql")


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    cli._garantir_cols(p)
    yield p
    p.close()


@pytest.fixture()
def dono(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Loja Teste') "
                        "returning id").fetchone()[0]
        c.commit()
    return cid


def _tel():
    """Telefone único por teste, pra um teste não enxergar o cadastro do outro."""
    return "869" + uuid.uuid4().int.__str__()[:8]


# --- o caso Ana Clara: salvar três vezes ------------------------------------

def test_salvar_o_mesmo_tres_vezes_nao_cria_tres(pool, dono):
    t = _tel()
    a = cli.salvar_cliente(pool, dono, "Ana Clara Marques", telefone=t)
    b = cli.salvar_cliente(pool, dono, "Ana Clara Marques", telefone=t)
    c = cli.salvar_cliente(pool, dono, "Ana Clara Marques", telefone=t)
    assert a["id"] == b["id"] == c["id"]
    assert a["acao"] == "criado"
    assert b["acao"] == "inalterado" and c["acao"] == "inalterado"
    assert len(cli.listar_clientes(pool, dono)) == 1


# --- o caso Victoria: mesmo telefone, nome com typo -------------------------

def test_typo_no_nome_nao_duplica_se_o_telefone_bate(pool, dono):
    t = _tel()
    a = cli.salvar_cliente(pool, dono, "Visctoria Caroline", telefone=t)
    b = cli.salvar_cliente(pool, dono, "Victoria Caroline", telefone=t)
    assert a["id"] == b["id"]
    assert len(cli.listar_clientes(pool, dono)) == 1


# --- o caso Gilvan/Ronaldo: virar fornecedor --------------------------------

def test_marcar_como_fornecedor_liga_a_marca_em_vez_de_criar_outro(pool, dono):
    t = _tel()
    a = cli.salvar_cliente(pool, dono, "GILVAN PEREIRA", telefone=t,
                           endereco="Rua Ulisses Marques, 2790")
    b = cli.salvar_cliente(pool, dono, "GILVAN PEREIRA", telefone=t,
                           eh_cliente=False, eh_fornecedor=True)
    assert a["id"] == b["id"], "criou um segundo Gilvan"
    assert b["papel_mudou"] is True
    assert b["acao"] == "atualizado"
    f = cli.obter_cliente(pool, dono, a["id"])
    assert f["eh_cliente"] and f["eh_fornecedor"], "tem que ser os DOIS"
    assert f["endereco"] == "Rua Ulisses Marques, 2790", "o endereço não pode sumir"


def test_papel_so_liga_nunca_desliga_sozinho(pool, dono):
    """Salvar de novo sem marcar fornecedor não pode APAGAR a marca que existia."""
    t = _tel()
    a = cli.salvar_cliente(pool, dono, "Ronaldo Vaz", telefone=t, eh_fornecedor=True)
    cli.salvar_cliente(pool, dono, "Ronaldo Vaz", telefone=t)   # sem fornecedor
    f = cli.obter_cliente(pool, dono, a["id"])
    assert f["eh_fornecedor"] is True


# --- enriquecer sem destruir ------------------------------------------------

def test_reusar_preenche_o_que_faltava(pool, dono):
    t = _tel()
    a = cli.salvar_cliente(pool, dono, "Camila", telefone=t)
    b = cli.salvar_cliente(pool, dono, "Camila", telefone=t,
                           cidade="Teresina", uf="PI", cep="64049870")
    assert b["id"] == a["id"] and b["acao"] == "atualizado"
    f = cli.obter_cliente(pool, dono, a["id"])
    assert (f["cidade"], f["uf"], f["cep"]) == ("Teresina", "PI", "64049870")


def test_salvar_incompleto_depois_nao_apaga_o_que_ja_tinha(pool, dono):
    """A trava mais importante: segundo save vazio não pode destruir o primeiro.
    É exatamente a forma dos registros de fornecedor do Gilvan e do Ronaldo —
    só o nome, tudo o mais em branco."""
    t = _tel()
    a = cli.salvar_cliente(pool, dono, "Luccas Vinicius", telefone=t,
                           cidade="Teresina", uf="PI", endereco="Rua Major Ricardo",
                           email="luccas@exemplo.com")
    cli.salvar_cliente(pool, dono, "Luccas Vinicius", telefone=t)
    f = cli.obter_cliente(pool, dono, a["id"])
    assert f["cidade"] == "Teresina"
    assert f["endereco"] == "Rua Major Ricardo"
    assert f["email"] == "luccas@exemplo.com"


# --- telefone em formatos diferentes ----------------------------------------

def test_mesmo_numero_com_e_sem_o_55_e_a_mesma_pessoa(pool, dono):
    """Na Prime convivem 13 registros em '8699…' e 6 em '5586…'. Comparando
    texto exato eles nunca se encontram."""
    a = cli.salvar_cliente(pool, dono, "Bianca Oliveira", telefone="86995187064")
    b = cli.salvar_cliente(pool, dono, "Bianca Oliveira", telefone="558695187064")
    assert a["id"] == b["id"]


def test_numero_curto_demais_nao_funde(pool, dono):
    """Menos de 8 dígitos não casa ninguém — duplicar é menos grave que fundir
    duas pessoas diferentes."""
    a = cli.salvar_cliente(pool, dono, "Fulano", telefone="1234")
    b = cli.salvar_cliente(pool, dono, "Sicrano", telefone="1234")
    assert a["id"] != b["id"]


# --- o retorno honesto ------------------------------------------------------

def test_o_retorno_diz_o_que_aconteceu(pool, dono):
    t = _tel()
    assert cli.salvar_cliente(pool, dono, "Eline", telefone=t)["acao"] == "criado"
    assert cli.salvar_cliente(pool, dono, "Eline", telefone=t)["acao"] == "inalterado"
    r = cli.salvar_cliente(pool, dono, "Eline", telefone=t, cidade="Teresina")
    assert r["acao"] == "atualizado" and r["papel_mudou"] is False
    r2 = cli.salvar_cliente(pool, dono, "Eline", telefone=t, eh_fornecedor=True)
    assert r2["papel_mudou"] is True


def test_criar_cliente_continua_devolvendo_o_id(pool, dono):
    """Compatibilidade: cinco chamadores usam o retorno como int."""
    cid = cli.criar_cliente(pool, dono, "Sarah Maria", telefone=_tel())
    assert isinstance(cid, int) and cid > 0


# --- o tipo que a empresa mais usa ------------------------------------------

def test_tipo_predominante_segue_o_que_a_empresa_cadastra(pool, dono):
    """A tela abria sempre em PJ. Na Prime, 23 de 23 clientes são PF — a
    vendedora trocava o botão toda vez."""
    assert cli.tipo_predominante(pool, dono) == "pj", "sem cadastro, mantém o padrão antigo"
    for i in range(3):
        cli.salvar_cliente(pool, dono, f"Pessoa {i}", cpf=_CPFS[i])
    assert cli.tipo_predominante(pool, dono) == "pf"


# CPFs válidos (dígito verificador confere) — resolver_pessoa recusa inválido
_CPFS = ["52998224725", "11144477735", "39053344705"]


def test_tipo_predominante_nao_quebra_sem_cadastro(pool):
    with pool.connection() as c:
        vazio = c.execute("insert into contas (tipo,nome) values ('pj','Vazia') "
                          "returning id").fetchone()[0]
        c.commit()
    assert cli.tipo_predominante(pool, vazio) == "pj"


# --- a mensagem que a tela mostra -------------------------------------------

def test_o_aviso_diz_a_verdade_nos_quatro_casos():
    """Antes saía "Cliente cadastrado." em TODOS os casos — criou, reusou em
    silêncio, ou não fez nada. Sem saber se pegou, a vendedora salvava de novo.
    Foi assim que a Ana Clara virou três cadastros em cinco minutos."""
    from web.portal import _aviso_do_cadastro as av
    assert av({"nome": "Ana Clara", "acao": "criado", "papel_mudou": False}) == \
        "Ana Clara cadastrado."
    assert "papel" in av({"nome": "Gilvan", "acao": "atualizado", "papel_mudou": True})
    assert "completei" in av({"nome": "Camila", "acao": "atualizado", "papel_mudou": False})
    assert "Nada mudou" in av({"nome": "Eline", "acao": "inalterado", "papel_mudou": False})


def test_o_aviso_nunca_mente_dizendo_que_cadastrou():
    """A frase 'cadastrado.' seca só pode aparecer quando REALMENTE criou."""
    from web.portal import _aviso_do_cadastro as av
    for acao in ("atualizado", "inalterado"):
        msg = av({"nome": "X", "acao": acao, "papel_mudou": False})
        assert "já estava cadastrado" in msg
