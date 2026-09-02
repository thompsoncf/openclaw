"""O contrato avisava "campo sem valor" com o dado guardado na aba Clientes.

O DEFEITO
`{cliente.doc}` e `{cliente.endereco}` eram lidos SÓ da linha do orçamento. Quando
o vendedor não digitava o CPF ali, a folha que o cliente abre pra assinar saía com

    ⚠️ Campos sem valor neste contrato: cliente.doc. Avise a PRIME EVENTOS antes
    de assinar.

— e o CPF estava cadastrado, com o orçamento apontando pro cadastro por
`cliente_id`. Medido na conta 34 em 02/09/2026: 12 orçamentos sem documento, em 4
deles o documento estava no cadastro, e DOIS já eram contrato emitido (nº 4,
Beatriz; nº 5, Claudia) com o aviso na cara do cliente.

O vínculo sempre foi pra isso. `web/painel_servicos` grava `cliente_id` ao salvar
e explica em comentário: "o VÍNCULO é o que faz a folha reler o cadastro depois:
sem ele, o texto copiado aqui congelaria pra sempre e corrigir na aba Clientes não
mudaria nada". Faltava alguém consumir o vínculo.

AS DUAS REGRAS QUE ESTE ARQUIVO PRENDE
1. **O orçamento vence.** Só buraco é preenchido — quem digitou um documento
   diferente lá tinha razão (contrato no nome do cônjuge, do pai da noiva, da
   empresa que paga) e o cadastro não desautoriza.
2. **O endereço vem em BLOCO.** Rua de um endereço com cidade de outro é um
   endereço que não existe, e num contrato isso é pior que a falta.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import contrato as ctr

CONTA = 34
OUTRA = 99

_SQL = """
create table contas (id bigserial primary key, nome text);
create table pessoas (id bigserial primary key, nome text, cpf text, cnpj text);
create table clientes (id bigserial primary key, dono_id bigint references contas(id),
  pessoa_id bigint references pessoas(id), nome text,
  endereco text, cep text, cidade text, uf text);
"""


@pytest.fixture()
def pool():
    dbname = "zaq_contrato_cadastro"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into contas (id, nome) values (%s,'Prime Eventos'),(%s,'Outra')",
                  (CONTA, OUTRA))
        c.commit()
    yield p
    p.close()


def _cadastro(pool, *, dono=CONTA, cpf="07714809388", cnpj=None,
              endereco="RUA DAS FLORES, 100", cep="64000000",
              cidade="Teresina", uf="PI"):
    """A Claudia como está na produção: cliente vinculado a uma pessoa com CPF."""
    with pool.connection() as c:
        pid = c.execute("insert into pessoas (nome, cpf, cnpj) values (%s,%s,%s) returning id",
                        ("Claudia Maria Almeida de Carvalho", cpf, cnpj)).fetchone()[0]
        cid = c.execute(
            """insert into clientes (dono_id, pessoa_id, nome, endereco, cep, cidade, uf)
               values (%s,%s,'Claudia Maria Almeida de Carvalho',%s,%s,%s,%s) returning id""",
            (dono, pid, endereco, cep, cidade, uf)).fetchone()[0]
        c.commit()
    return cid


def _orc(**over):
    """Um orçamento como o 18 da produção: sem documento e sem endereço."""
    base = {"cliente": "Claudia Maria Almeida de Carvalho",
            "empresa": "Claudia Maria Almeida de Carvalho",
            "cnpj": "", "endereco": "", "cep": "", "cidade": "", "uf": ""}
    base.update(over)
    return base


# ══════════════════════════ o documento ══════════════════════════

def test_documento_vazio_vem_do_cadastro(pool):
    """O CASO DO ORÇAMENTO 18: CPF cadastrado, contrato dizendo que falta."""
    cid = _cadastro(pool)
    o = ctr.completar_do_cadastro(pool, CONTA, _orc(), cid)
    assert o["cnpj"] == "07714809388"


def test_documento_digitado_no_orcamento_vence_o_cadastro(pool):
    """Contrato no nome do cônjuge, do pai da noiva, da empresa que paga — quem
    digitou lá tinha razão, e o cadastro não pode desautorizar."""
    cid = _cadastro(pool, cpf="07714809388")
    o = ctr.completar_do_cadastro(pool, CONTA, _orc(cnpj="111.222.333-44"), cid)
    assert o["cnpj"] == "111.222.333-44"


def test_cnpj_do_cadastro_serve_quando_nao_ha_cpf(pool):
    cid = _cadastro(pool, cpf=None, cnpj="52752898000158")
    assert ctr.completar_do_cadastro(pool, CONTA, _orc(), cid)["cnpj"] == "52752898000158"


def test_cadastro_sem_documento_nenhum_nao_inventa(pool):
    cid = _cadastro(pool, cpf=None, cnpj=None)
    assert ctr.completar_do_cadastro(pool, CONTA, _orc(), cid)["cnpj"] == ""


def test_documento_so_de_espaco_conta_como_vazio(pool):
    cid = _cadastro(pool)
    assert ctr.completar_do_cadastro(pool, CONTA, _orc(cnpj="   "), cid)["cnpj"] == "07714809388"


# ══════════════════════════ o endereço, em bloco ══════════════════════════

def test_endereco_vazio_vem_inteiro_do_cadastro(pool):
    cid = _cadastro(pool)
    o = ctr.completar_do_cadastro(pool, CONTA, _orc(), cid)
    assert (o["endereco"], o["cep"], o["cidade"], o["uf"]) == \
           ("RUA DAS FLORES, 100", "64000000", "Teresina", "PI")


def test_endereco_do_orcamento_vence_inteiro(pool):
    cid = _cadastro(pool)
    o = ctr.completar_do_cadastro(pool, CONTA, _orc(
        endereco="AV BEIRA RIO, 900", cep="64001000", cidade="Timon", uf="MA"), cid)
    assert (o["endereco"], o["cidade"], o["uf"]) == ("AV BEIRA RIO, 900", "Timon", "MA")


def test_nao_mistura_a_rua_de_um_com_a_cidade_do_outro(pool):
    """A REGRA DO BLOCO. Orçamento com logradouro e sem cidade não pode puxar a
    cidade do cadastro: sairia um endereço que não existe em lugar nenhum — pior
    que a falta, porque parece completo."""
    cid = _cadastro(pool, endereco="RUA DAS FLORES, 100", cidade="Teresina", uf="PI")
    o = ctr.completar_do_cadastro(pool, CONTA, _orc(endereco="AV BEIRA RIO, 900"), cid)
    assert o["endereco"] == "AV BEIRA RIO, 900"
    assert o["cidade"] == "", "puxou a cidade do OUTRO endereço"
    assert o["cep"] == "" and o["uf"] == ""


def test_cidade_do_lead_nao_impede_o_endereco_do_cadastro(pool):
    """Cidade/UF sozinhas costumam vir do lead, não de um endereço — não são
    logradouro, então não seguram o bloco. Mas o que o orçamento já dizia sobre a
    cidade é respeitado quando o cadastro não tem."""
    cid = _cadastro(pool)
    o = ctr.completar_do_cadastro(pool, CONTA, _orc(cidade="Teresina", uf="PI"), cid)
    assert o["endereco"] == "RUA DAS FLORES, 100"

    cid2 = _cadastro(pool, endereco="RUA B, 2", cidade=None, uf=None, cep=None)
    o2 = ctr.completar_do_cadastro(pool, CONTA, _orc(cidade="Parnaíba", uf="PI"), cid2)
    assert o2["endereco"] == "RUA B, 2"
    assert (o2["cidade"], o2["uf"]) == ("Parnaíba", "PI"), "apagou o que o orçamento sabia"


# ══════════════════════════ os limites ══════════════════════════

def test_sem_vinculo_devolve_o_orcamento_como_veio(pool):
    _cadastro(pool)
    o = _orc()
    assert ctr.completar_do_cadastro(pool, CONTA, o, None) == o


def test_cadastro_de_outra_conta_nao_vaza(pool):
    """O escopo é por `dono_id`. Sem ele, um id chutado traria o CPF de um cliente
    de outra empresa pra dentro de um contrato."""
    alheio = _cadastro(pool, dono=OUTRA)
    o = ctr.completar_do_cadastro(pool, CONTA, _orc(), alheio)
    assert o["cnpj"] == "" and o["endereco"] == ""


def test_cliente_inexistente_nao_quebra(pool):
    o = _orc()
    assert ctr.completar_do_cadastro(pool, CONTA, o, 999999) == o


def test_banco_fora_nao_derruba_o_contrato(pool):
    """Tolerante de propósito: o contrato tem que sair mesmo assim — com o aviso
    de sempre, que é exatamente o que ele já fazia antes desta função existir."""
    class _Explode:
        def connection(self):
            raise RuntimeError("sem banco")
    o = _orc()
    assert ctr.completar_do_cadastro(_Explode(), CONTA, o, 1) == o


def test_nao_muda_o_dicionario_recebido(pool):
    """Quem chamou pode reusar o original — devolver o mesmo objeto mutado faria a
    prévia e a folha divergirem conforme a ordem das chamadas."""
    cid = _cadastro(pool)
    o = _orc()
    novo = ctr.completar_do_cadastro(pool, CONTA, o, cid)
    assert o["cnpj"] == "" and novo["cnpj"] == "07714809388"
    assert novo is not o


def test_campos_que_nao_sao_do_cadastro_ficam_intactos(pool):
    cid = _cadastro(pool)
    o = ctr.completar_do_cadastro(
        pool, CONTA, _orc(numero=18, evento={"data": "15/01/2027"}, setup_centavos=775000), cid)
    assert o["numero"] == 18 and o["evento"] == {"data": "15/01/2027"}
    assert o["setup_centavos"] == 775000


# ══════════════════════════ o aviso, de ponta a ponta ══════════════════════════

def test_a_clausula_para_de_acusar_falta(pool):
    """O que o cliente vê. `montar` devolve as faltas que viram o ⚠️ na folha."""
    cid = _cadastro(pool)
    clausulas = [{"titulo": "Objeto",
                  "corpo": "{cliente.nome}, CPF/CNPJ {cliente.doc}, residente em "
                           "{cliente.endereco}, {cliente.cidade}/{cliente.uf}."}]

    cru = ctr.contexto(orcamento=_orc(), modelo={}, empresa={})
    _texto, faltas_antes = ctr.montar(clausulas, cru)
    assert "cliente.doc" in faltas_antes and "cliente.endereco" in faltas_antes

    completo = ctr.contexto(
        orcamento=ctr.completar_do_cadastro(pool, CONTA, _orc(), cid), modelo={}, empresa={})
    texto, faltas = ctr.montar(clausulas, completo)
    assert faltas == [], f"ainda falta: {faltas}"
    assert "07714809388" in texto[0]["corpo"]
    assert "RUA DAS FLORES, 100" in texto[0]["corpo"]
