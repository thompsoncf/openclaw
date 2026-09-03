"""Títulos a pagar/receber: corrigir a descrição e apagar (o que faltava no portal).

- editar_descricao_titulo: troca só a descrição, não mexe em valor/vencimento.
- apagar_titulo: a trava é `lancamento_id is null` — "nada no livro-caixa depende
  deste título" —, e não o status. Some com o aberto; recusa o pago que virou
  lançamento; e LIBERA o pago cujo lançamento não existe (a FK é `on delete set
  null`, então apagar o lançamento no financeiro deixa o título pago apontando pra
  nada — e ele ficava preso pra sempre, porque a tela só lista os abertos).
"""
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp

_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "053_modulo_pj.sql",
              "195_titulo_aprovacao.sql",
              "057_natureza_lancamento.sql",
              "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql",
              "067_titulos_cliente.sql",
              "131_pessoa_cnpj.sql")


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
            "insert into contas (tipo, nome) values ('pj', 'Teste Titulos') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def test_editar_descricao_nao_mexe_no_resto(pool, conta_id):
    t = emp.criar_titulo(pool, conta_id, "pagar", "Aluguel do pnto",
                         50000, date(2026, 8, 10))
    ok = emp.editar_descricao_titulo(pool, conta_id, t["id"], "  Aluguel do ponto  ")
    assert ok
    achado = next(x for x in emp.listar_titulos(pool, conta_id) if x["id"] == t["id"])
    assert achado["descricao"] == "Aluguel do ponto"   # corrigido e sem espaços
    assert achado["valor_centavos"] == 50000           # valor intacto
    assert achado["vencimento"] == date(2026, 8, 10)   # vencimento intacto


def test_editar_descricao_vazia_nao_apaga(pool, conta_id):
    t = emp.criar_titulo(pool, conta_id, "pagar", "Internet", 12000, date(2026, 8, 5))
    assert emp.editar_descricao_titulo(pool, conta_id, t["id"], "   ") is False
    achado = next(x for x in emp.listar_titulos(pool, conta_id) if x["id"] == t["id"])
    assert achado["descricao"] == "Internet"


def test_editar_descricao_so_da_propria_conta(pool, conta_id):
    t = emp.criar_titulo(pool, conta_id, "pagar", "Luz", 20000, date(2026, 8, 8))
    # outra conta não consegue mexer
    assert emp.editar_descricao_titulo(pool, conta_id + 99999, t["id"], "hack") is False


def test_editar_valor_do_titulo(pool, conta_id):
    t = emp.criar_titulo(pool, conta_id, "pagar", "Fornecedor", 100000, date(2026, 8, 9))
    # edita SÓ o valor (descrição None) — não mexe na descrição
    assert emp.editar_titulo(pool, conta_id, t["id"], valor_centavos=175050) is True
    achado = next(x for x in emp.listar_titulos(pool, conta_id) if x["id"] == t["id"])
    assert achado["valor_centavos"] == 175050
    assert achado["descricao"] == "Fornecedor"     # descrição intacta


def test_editar_descricao_e_valor_juntos(pool, conta_id):
    t = emp.criar_titulo(pool, conta_id, "pagar", "Agua", 5000, date(2026, 8, 9))
    assert emp.editar_titulo(pool, conta_id, t["id"], descricao="Água/esgoto",
                             valor_centavos=6250) is True
    achado = next(x for x in emp.listar_titulos(pool, conta_id) if x["id"] == t["id"])
    assert achado["descricao"] == "Água/esgoto"
    assert achado["valor_centavos"] == 6250


def test_editar_titulo_valor_negativo_ignorado(pool, conta_id):
    t = emp.criar_titulo(pool, conta_id, "pagar", "X", 8000, date(2026, 8, 9))
    # valor negativo não entra; sem outros campos, nada muda
    assert emp.editar_titulo(pool, conta_id, t["id"], valor_centavos=-100) is False
    achado = next(x for x in emp.listar_titulos(pool, conta_id) if x["id"] == t["id"])
    assert achado["valor_centavos"] == 8000


def test_titulo_liga_ao_cliente_e_aparece_na_ficha(pool, conta_id):
    from finance import clientes as cli
    cid = cli.criar_cliente(pool, conta_id, "Padaria do Zé")
    # honorário/venda a prazo ligado ao cliente
    t = emp.criar_titulo(pool, conta_id, "receber", "Honorário mensal", 80000,
                         date(2026, 8, 5), cliente_id=cid)
    assert t["cliente_id"] == cid
    # aparece em listar_titulos com o nome do cliente
    achado = next(x for x in emp.listar_titulos(pool, conta_id) if x["id"] == t["id"])
    assert achado["cliente_id"] == cid
    assert achado["cliente_nome"] == "Padaria do Zé"


def test_resumo_carteira_agrupa_por_cliente(pool, conta_id):
    from finance import clientes as cli
    from datetime import timedelta
    a = cli.criar_cliente(pool, conta_id, "Cliente A")
    b = cli.criar_cliente(pool, conta_id, "Cliente B")
    hoje = date.today()
    # A: 2 honorários (um vencido = atrasado), B: 1 no futuro
    emp.criar_titulo(pool, conta_id, "receber", "Hon jul", 80000,
                     hoje - timedelta(days=5), cliente_id=a)   # atrasado
    emp.criar_titulo(pool, conta_id, "receber", "Hon ago", 80000,
                     hoje + timedelta(days=20), cliente_id=a)
    emp.criar_titulo(pool, conta_id, "receber", "Hon B", 50000,
                     hoje + timedelta(days=10), cliente_id=b)
    cart = emp.resumo_carteira(pool, conta_id)
    assert cart["total_centavos"] == 210000
    assert cart["atrasado_centavos"] == 80000
    assert cart["n_clientes"] == 2
    # ordenado por valor: A (160k) antes de B (50k)
    assert cart["clientes"][0]["nome"] == "Cliente A"
    assert cart["clientes"][0]["total_centavos"] == 160000
    assert cart["clientes"][0]["atrasado"] is True
    assert cart["clientes"][1]["nome"] == "Cliente B"
    assert cart["clientes"][1]["atrasado"] is False


def test_achar_cliente_por_nome(pool, conta_id):
    from finance import clientes as cli
    cid = cli.criar_cliente(pool, conta_id, "Mercado Central")
    assert cli.achar_cliente_por_nome(pool, conta_id, "Mercado Central") == cid
    assert cli.achar_cliente_por_nome(pool, conta_id, "mercado central") == cid  # case-insensitive
    assert cli.achar_cliente_por_nome(pool, conta_id, "Central") == cid          # parcial único
    assert cli.achar_cliente_por_nome(pool, conta_id, "Inexistente") is None
    # ambíguo: dois "Mercado" -> não chuta
    cli.criar_cliente(pool, conta_id, "Mercado Sul")
    assert cli.achar_cliente_por_nome(pool, conta_id, "Mercado") is None


def test_apagar_titulo_aberto(pool, conta_id):
    t = emp.criar_titulo(pool, conta_id, "receber", "Venda X", 30000, date(2026, 8, 1))
    assert emp.apagar_titulo(pool, conta_id, t["id"]) is True
    assert all(x["id"] != t["id"] for x in emp.listar_titulos(pool, conta_id, status="aberto"))
    # apagar de novo: já não existe
    assert emp.apagar_titulo(pool, conta_id, t["id"]) is False


def test_apagar_titulo_pago_sem_lancamento_no_caixa(pool, conta_id):
    """O caso que ficava preso: baixado, mas sem nada no caixa apontando pra ele.

    Acontece de verdade — `titulos.lancamento_id` é `on delete set null`, então quem
    apaga o lançamento no financeiro deixa o título 'pago' órfão. Ele sumia da tela
    (que só lista 'aberto') e nenhum caminho o removia. Visto em produção.
    """
    t = emp.criar_titulo(pool, conta_id, "receber", "Sinal de teste", 30000,
                         date(2026, 8, 5))
    emp.dar_baixa_titulo(pool, conta_id, t["id"])
    # o financeiro apaga o lançamento: a FK zera o vínculo e o título fica pago e órfão
    with pool.connection() as c:
        lanc_id = c.execute("select lancamento_id from titulos where id=%s",
                            (t["id"],)).fetchone()[0]
        assert lanc_id is not None, "a baixa tem que ter lançado no caixa"
        c.execute("delete from lancamentos where id=%s", (lanc_id,))
        c.commit()
        assert c.execute("select status, lancamento_id from titulos where id=%s",
                         (t["id"],)).fetchone() == ("pago", None)

    assert emp.apagar_titulo(pool, conta_id, t["id"]) is True
    assert all(x["id"] != t["id"] for x in emp.listar_titulos(pool, conta_id, status="pago"))


def test_apagar_nao_atravessa_conta(pool, conta_id):
    """A trava do caixa não pode ofuscar a de escopo: conta vizinha não apaga nada."""
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Vizinha') "
                          "returning id").fetchone()[0]
        c.commit()
    t = emp.criar_titulo(pool, conta_id, "pagar", "Meu", 10000, date(2026, 8, 9))
    assert emp.apagar_titulo(pool, outra, t["id"]) is False
    assert any(x["id"] == t["id"] for x in emp.listar_titulos(pool, conta_id, status="aberto"))


def test_apagar_nao_atinge_titulo_pago(pool, conta_id):
    t = emp.criar_titulo(pool, conta_id, "pagar", "Fornecedor Y", 45000, date(2026, 8, 3))
    r = emp.dar_baixa_titulo(pool, conta_id, t["id"])   # vira 'pago' + lançamento
    assert r.get("ok", True) is not False
    # título pago NÃO pode ser apagado por aqui (o lançamento no caixa é real)
    assert emp.apagar_titulo(pool, conta_id, t["id"]) is False
    # segue existindo como pago
    pagos = emp.listar_titulos(pool, conta_id, status="pago")
    assert any(x["id"] == t["id"] for x in pagos)
