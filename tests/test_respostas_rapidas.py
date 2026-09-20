"""As respostas rápidas: o que o vendedor manda o dia inteiro, a um toque.

O QUE ESTE ARQUIVO PRENDE
 1. duas donas: a DA CONTA (todo mundo vê) e a DO VENDEDOR (só ele) — e uma
    nunca vaza pra quem não é dela;
 2. a lista se enche sozinha: salvar o que está escrito, sem tela de cadastro;
 3. as variáveis (`{nome}`, `{vendedor}`, `{empresa}`) saem prontas, e a marca
    vazia não deixa vírgula órfã na frase;
 4. quem manda na conta é quem mexe na resposta DA EQUIPE.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import respostas_rapidas as rr

CONTA, OUTRA = 1, 2
VEND, COLEGA = 10, 11

_SQL = """
create table contas (id bigserial primary key, nome text);
create table respostas_rapidas (
  id bigserial primary key, conta_id bigint not null references contas(id),
  membro_id bigint, titulo text not null default '', texto text not null,
  usos integer not null default 0, criado_por bigint,
  criado_em timestamptz not null default now());
insert into contas (id, nome) values (1,'Prime'), (2,'Outra');
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_respostas_test"
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


# ----------------------------------------------------------------- as variáveis

def test_as_variaveis_saem_prontas():
    v = rr.variaveis(cliente="Maria Fernanda Souza", vendedor="Thiago Lima", empresa="Prime")
    assert rr.aplicar("Oi {nome}, aqui é o {vendedor} da {empresa}!", v) == \
        "Oi Maria, aqui é o Thiago da Prime!"


def test_cliente_sem_nome_nao_deixa_virgula_orfa():
    """A maioria de quem chega pelo WhatsApp não tem nome na ficha. "Oi , tudo
    bem?" é a cara de mensagem automática — exatamente o que o vendedor não quer
    mandar."""
    v = rr.variaveis(cliente="", vendedor="Thiago", empresa="Prime")
    assert rr.aplicar("Oi {nome}, tudo bem?", v) == "Oi, tudo bem?"
    assert rr.aplicar("{nome} bom dia!", v) == "bom dia!"


def test_marca_desconhecida_fica_visivel():
    """Marca que virasse vazio em silêncio faria a frase sair truncada sem
    ninguém entender por quê. Aparecendo, o vendedor vê e corrige — a tela
    escreve na caixa, não envia."""
    assert rr.aplicar("Valor: {preco}", rr.variaveis()) == "Valor: {preco}"


# -------------------------------------------------------------- as duas donas

def test_a_lista_junta_as_da_equipe_e_as_minhas(pool):
    rr.criar(pool, CONTA, VEND, "O endereço é Av. Fátima 123", da_equipe=True)
    rr.criar(pool, CONTA, VEND, "Posso te ligar agora?")
    rr.criar(pool, CONTA, COLEGA, "Só do colega")
    rr.criar(pool, OUTRA, VEND, "De outra empresa")

    textos = [x["texto"] for x in rr.listar(pool, CONTA, VEND)]
    assert "O endereço é Av. Fátima 123" in textos and "Posso te ligar agora?" in textos
    assert "Só do colega" not in textos, "resposta de colega não aparece"
    assert "De outra empresa" not in textos, "resposta de outra conta não aparece"
    assert [x["equipe"] for x in rr.listar(pool, CONTA, VEND) if x["texto"].startswith("O endereço")] == [True]


def test_as_mais_usadas_ficam_em_cima(pool):
    a = rr.criar(pool, CONTA, VEND, "pouco usada")["id"]
    b = rr.criar(pool, CONTA, VEND, "muito usada")["id"]
    for _ in range(3):
        rr.usar(pool, CONTA, VEND, b)
    rr.usar(pool, CONTA, VEND, a)
    assert [x["texto"] for x in rr.listar(pool, CONTA, VEND)][0] == "muito usada"


def test_usar_conta_e_devolve_o_texto(pool):
    i = rr.criar(pool, CONTA, VEND, "O pacote sai por R$ 5.000")["id"]
    r = rr.usar(pool, CONTA, VEND, i)
    assert r["ok"] and r["texto"] == "O pacote sai por R$ 5.000"
    assert rr.listar(pool, CONTA, VEND)[0]["usos"] == 1


def test_nao_abre_resposta_de_colega_nem_por_id_adivinhado(pool):
    i = rr.criar(pool, CONTA, COLEGA, "a do colega")["id"]
    assert rr.usar(pool, CONTA, VEND, i) == {"ok": False, "erro": "nao_encontrada"}
    assert rr.usar(pool, OUTRA, COLEGA, i) == {"ok": False, "erro": "nao_encontrada"}


# --------------------------------------------------------------- salvar e apagar

def test_o_titulo_sai_do_proprio_texto(pool):
    """Pedir um título antes de salvar é o atrito que faz ninguém salvar nada."""
    rr.criar(pool, CONTA, VEND, "Bom dia! " + "x" * 200)
    t = rr.listar(pool, CONTA, VEND)[0]["titulo"]
    assert t.startswith("Bom dia!") and len(t) <= rr.LIMITE_TITULO + 1 and t.endswith("…")


def test_salvar_a_mesma_frase_duas_vezes_nao_duplica(pool):
    """O vendedor toca em salvar duas vezes sem querer e a lista viraria eco."""
    a = rr.criar(pool, CONTA, VEND, "Chegou o comprovante?")
    b = rr.criar(pool, CONTA, VEND, "Chegou o comprovante?")
    assert b["id"] == a["id"] and b.get("repetida")
    assert len(rr.listar(pool, CONTA, VEND)) == 1
    # ...mas a MESMA frase pode existir uma vez da equipe e uma vez do vendedor
    rr.criar(pool, CONTA, VEND, "Chegou o comprovante?", da_equipe=True)
    assert len(rr.listar(pool, CONTA, VEND)) == 2


def test_vazio_nao_vira_resposta(pool):
    assert rr.criar(pool, CONTA, VEND, "   ")["erro"] == "vazio"
    assert rr.listar(pool, CONTA, VEND) == []


def test_a_lista_tem_teto(pool):
    for i in range(rr.TETO_POR_DONO):
        rr.criar(pool, CONTA, VEND, f"frase {i}")
    assert rr.criar(pool, CONTA, VEND, "mais uma")["erro"] == "cheio"
    # o teto é POR DONO: a da equipe continua podendo entrar
    assert rr.criar(pool, CONTA, VEND, "a da equipe", da_equipe=True)["ok"]


def test_vendedor_nao_apaga_a_da_equipe(pool):
    """Senão um vendedor apagaria o preço oficial da empresa pra todo mundo."""
    i = rr.criar(pool, CONTA, VEND, "O preço da empresa", da_equipe=True)["id"]
    assert rr.apagar(pool, CONTA, VEND, i)["ok"] is False
    assert len(rr.listar(pool, CONTA, VEND)) == 1
    assert rr.apagar(pool, CONTA, VEND, i, manda_na_conta=True)["ok"] is True
    assert rr.listar(pool, CONTA, VEND) == []


def test_cada_um_apaga_a_sua(pool):
    minha = rr.criar(pool, CONTA, VEND, "minha")["id"]
    dele = rr.criar(pool, CONTA, COLEGA, "dele")["id"]
    assert rr.apagar(pool, CONTA, VEND, dele)["ok"] is False
    assert rr.apagar(pool, CONTA, VEND, minha)["ok"] is True


def test_tabela_faltando_nao_derruba_a_conversa(pool):
    """Deploy pela metade não pode tirar a tela do vendedor do ar: sem a tabela, a
    lista vem vazia e a conversa segue."""
    with pool.connection() as c:
        c.execute("drop table respostas_rapidas")
        c.commit()
    assert rr.listar(pool, CONTA, VEND) == []
