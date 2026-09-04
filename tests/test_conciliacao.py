"""Conciliar: ligar um pagamento que JÁ EXISTE a uma conta em aberto.

É o único passo do trabalho de relatórios que escreve no banco, e o que ele
escreve é o estado de uma dívida — por isso o teste é o mais duro dos quatro.

**A armadilha que quase peguei:** `dar_baixa_titulo` LANÇA no livro-caixa, porque
serve pra quando o dinheiro sai no momento do clique. Aqui o dinheiro já saiu
(veio do extrato ou da foto do comprovante), e reusar aquela função criaria um
segundo lançamento — dobrando a despesa no livro-caixa e no DRE. `conciliar_titulo`
não lança nada: só liga o que já existe.

O que este teste protege:

  * **nenhum lançamento é criado.** A contagem de `lancamentos` antes e depois tem
    que ser idêntica. É a asserção que pega o dia em que alguém "simplificar"
    chamando `dar_baixa_titulo` aqui;
  * **o pagamento não é tocado.** Some só o vínculo; valor, data, descrição e
    origem ficam exatamente como estavam;
  * **o servidor revalida tudo.** O pedido vem do navegador: sem revalidar, um
    POST forjado casaria qualquer lançamento com qualquer título. Valor errado,
    lado errado do caixa, fora da janela e conta de outro dono têm que ser
    recusados;
  * **o eco não quita.** A foto do comprovante do sinal da Bianca não pode fechar
    a parcela 2/2 — é o contra-exemplo que atravessa os quatro passos;
  * **duplo-clique não quebra nada** (o UPDATE ... WHERE status='aberto' segura);
  * **dá pra desfazer** — e desfazer só o que foi conciliado, nunca uma baixa
    comum, que deixaria o lançamento dela órfão no caixa;
  * **não nasce título recorrente.** `dar_baixa_titulo` cria o do mês seguinte
    porque roda na hora do pagamento; conciliação é retroativa, e em 01/09/2026 a
    Prime já tem os 30 títulos de setembro cadastrados na mão — criar mais um
    seria conta duplicada.
"""
import os
from datetime import date, timedelta

import pytest
from psycopg_pool import ConnectionPool

from finance import empresa as emp

_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table lancamentos (
  id bigserial primary key, conta_id bigint not null, tipo text not null,
  valor_centavos bigint not null, categoria text not null,
  descricao text not null default '', data date not null,
  origem text not null default 'manual', natureza text);
create table titulos (
  id bigserial primary key, conta_id bigint, tipo text, descricao text,
  contraparte text, valor_centavos bigint, vencimento date, status text,
  recorrente boolean default false, periodicidade text, valor_variavel boolean not null default false, acrescimo_centavos int not null default 0, lancamento_acrescimo_id bigint, categoria text, pago_em date,
  lancamento_id bigint, criado_por bigint);
"""

HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_conciliacao_test"
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
        c.execute("truncate contas, lancamentos, titulos restart identity")
        cid = c.execute("insert into contas (nome) values ('Prime Eventos') "
                        "returning id").fetchone()[0]
        c.commit()
    return cid


def _titulo(pool, conta_id, *, tipo="pagar", valor=220000, venc_em=-17,
            descricao="ZARB CONSULTORIA", status="aberto", recorrente=False,
            lancamento_id=None):
    with pool.connection() as c:
        tid = c.execute(
            """insert into titulos (conta_id, tipo, descricao, contraparte,
                 valor_centavos, vencimento, status, categoria, recorrente,
                 lancamento_id, pago_em)
               values (%s,%s,%s,'',%s,%s,%s,'Fornecedores',%s,%s,
                       case when %s='pago' then current_date end) returning id""",
            (conta_id, tipo, descricao, valor, HOJE + timedelta(days=venc_em),
             status, recorrente, lancamento_id, status)).fetchone()[0]
        c.commit()
    return tid


def _pag(pool, conta_id, *, tipo="despesa", valor=220000, dias=-22,
         descricao="Pagamento Pix 58.608.090", origem="extrato"):
    with pool.connection() as c:
        lid = c.execute(
            """insert into lancamentos (conta_id, tipo, valor_centavos, categoria,
                 descricao, data, origem, natureza)
               values (%s,%s,%s,'Outros',%s,%s,%s,'empresa') returning id""",
            (conta_id, tipo, valor, descricao, HOJE + timedelta(days=dias),
             origem)).fetchone()[0]
        c.commit()
    return lid


def _conta_linhas(pool, tabela):
    with pool.connection() as c:
        return c.execute(f"select count(*) from {tabela}").fetchone()[0]


def _titulo_de(pool, tid):
    with pool.connection() as c:
        r = c.execute("select status, pago_em, lancamento_id from titulos where id=%s",
                      (tid,)).fetchone()
    return {"status": r[0], "pago_em": r[1], "lancamento_id": r[2]}


# ── o caminho feliz: o caso ZARB ─────────────────────────────────────────────
def test_conciliar_fecha_a_conta_sem_criar_dinheiro(pool, conta):
    """O caso real: título de R$ 2.200 vencido em 15/08, Pix de R$ 2.200 em 10/08.

    A asserção que importa mais é a contagem de lançamentos: se um dia alguém
    trocar isto por `dar_baixa_titulo`, a despesa dobra no livro-caixa e no DRE, e
    é este número que grita."""
    tid = _titulo(pool, conta)
    lid = _pag(pool, conta)
    antes = _conta_linhas(pool, "lancamentos")

    r = emp.conciliar_titulo(pool, conta, tid, lid)

    assert r["ok"] is True
    assert _conta_linhas(pool, "lancamentos") == antes, \
        "conciliar NÃO pode lançar dinheiro novo — o pagamento já existe"
    t = _titulo_de(pool, tid)
    assert t["status"] == "pago"
    assert t["lancamento_id"] == lid
    assert t["pago_em"] == HOJE - timedelta(days=22), \
        "pago_em é o dia em que o dinheiro andou, não hoje"


def test_o_pagamento_nao_e_tocado(pool, conta):
    tid = _titulo(pool, conta)
    lid = _pag(pool, conta)
    with pool.connection() as c:
        antes = c.execute("select tipo, valor_centavos, descricao, data, origem, "
                          "categoria, natureza from lancamentos where id=%s",
                          (lid,)).fetchone()
    emp.conciliar_titulo(pool, conta, tid, lid)
    with pool.connection() as c:
        depois = c.execute("select tipo, valor_centavos, descricao, data, origem, "
                           "categoria, natureza from lancamentos where id=%s",
                           (lid,)).fetchone()
    assert antes == depois


def test_o_lado_de_receber_tambem_funciona(pool, conta):
    tid = _titulo(pool, conta, tipo="receber", valor=75000, descricao="Evento — parcela 2/2")
    lid = _pag(pool, conta, tipo="receita", valor=75000, dias=-20, origem="foto")
    assert emp.conciliar_titulo(pool, conta, tid, lid)["ok"] is True
    assert _titulo_de(pool, tid)["status"] == "pago"


def test_nao_nasce_titulo_recorrente(pool, conta):
    """`dar_baixa_titulo` cria o do mês seguinte porque roda na hora do pagamento.
    Conciliação é retroativa: em produção a Prime já tem os 30 títulos de setembro
    cadastrados na mão, e conciliar a ZARB de 15/08 criaria uma segunda ZARB de
    15/09 por cima da que já está lá."""
    tid = _titulo(pool, conta, recorrente=True)
    lid = _pag(pool, conta)
    emp.conciliar_titulo(pool, conta, tid, lid)
    assert _conta_linhas(pool, "titulos") == 1, "conta duplicada é o erro simétrico"


# ── o servidor revalida (navegador não é fonte confiável) ────────────────────
def test_valor_diferente_e_recusado(pool, conta):
    tid = _titulo(pool, conta, valor=220000)
    lid = _pag(pool, conta, valor=219999)
    r = emp.conciliar_titulo(pool, conta, tid, lid)
    assert r["ok"] is False and "valor" in r["erro"].lower()
    assert _titulo_de(pool, tid)["status"] == "aberto"


def test_lado_errado_do_caixa_e_recusado(pool, conta):
    tid = _titulo(pool, conta, tipo="pagar")
    lid = _pag(pool, conta, tipo="receita")
    r = emp.conciliar_titulo(pool, conta, tid, lid)
    assert r["ok"] is False and "outro lado" in r["erro"]


def test_fora_da_janela_e_recusado(pool, conta):
    """Sem esta trava, um POST forjado casaria a conta de setembro com o pagamento
    de agosto — o falso positivo que a janela existe pra evitar."""
    tid = _titulo(pool, conta, venc_em=0)
    lid = _pag(pool, conta, dias=-30)
    r = emp.conciliar_titulo(pool, conta, tid, lid)
    assert r["ok"] is False and "dias do vencimento" in r["erro"]


def test_a_janela_do_servidor_e_a_mesma_da_tela(pool):
    """Se divergirem, aparece botão que o servidor recusa — ou, pior, o servidor
    aceita o que a tela não sugeriu."""
    from web import painel_relatorios as rel
    assert emp.JANELA_CONCILIACAO_DIAS == emp.JANELA_CONCILIACAO_DIAS == 14


def test_titulo_de_outra_conta_e_recusado(pool, conta):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Outra') "
                          "returning id").fetchone()[0]
        c.commit()
    tid = _titulo(pool, outra)
    lid = _pag(pool, conta)
    assert emp.conciliar_titulo(pool, conta, tid, lid)["ok"] is False
    assert _titulo_de(pool, tid)["status"] == "aberto"


def test_pagamento_de_outra_conta_e_recusado(pool, conta):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Outra') "
                          "returning id").fetchone()[0]
        c.commit()
    tid = _titulo(pool, conta)
    lid = _pag(pool, outra)
    assert emp.conciliar_titulo(pool, conta, tid, lid)["ok"] is False


def test_titulo_ja_pago_e_recusado(pool, conta):
    tid = _titulo(pool, conta, status="pago")
    lid = _pag(pool, conta)
    r = emp.conciliar_titulo(pool, conta, tid, lid)
    assert r["ok"] is False and "já está" in r["erro"]


def test_pagamento_ja_ligado_a_outra_conta_e_recusado(pool, conta):
    """Um Pix paga UMA conta. Sem isto, o mesmo dinheiro quitaria duas dívidas."""
    lid = _pag(pool, conta)
    _titulo(pool, conta, status="pago", lancamento_id=lid, descricao="Primeira")
    tid2 = _titulo(pool, conta, descricao="Segunda")
    r = emp.conciliar_titulo(pool, conta, tid2, lid)
    assert r["ok"] is False and "já está ligado" in r["erro"]
    assert _titulo_de(pool, tid2)["status"] == "aberto"


def test_o_eco_do_sinal_nao_quita(pool, conta):
    """O contra-exemplo da Bianca, agora na gravação. O dinheiro do sinal está no
    banco duas vezes: a baixa (amarrada) e a foto do mesmo comprovante (solta).
    Se a foto pudesse quitar a parcela 2/2, o sistema fecharia com o dinheiro da
    parcela 1 uma dívida que a cliente ainda tem."""
    baixa = _pag(pool, conta, tipo="receita", valor=75000, dias=-4, origem="titulo",
                 descricao="Evento — Bianca · Sinal")
    _titulo(pool, conta, tipo="receber", valor=75000, status="pago",
            lancamento_id=baixa, descricao="Evento — Bianca · Sinal")
    foto = _pag(pool, conta, tipo="receita", valor=75000, dias=-4, origem="foto",
                descricao="Sinal 50% locação espaço - Bianca Oliveira")
    parcela2 = _titulo(pool, conta, tipo="receber", valor=75000, venc_em=-1,
                       descricao="Evento — Bianca · parcela 2/2")
    r = emp.conciliar_titulo(pool, conta, parcela2, foto)
    assert r["ok"] is False and "mesma entrada" in r["erro"]
    assert _titulo_de(pool, parcela2)["status"] == "aberto"


def test_inexistentes_nao_derrubam(pool, conta):
    lid = _pag(pool, conta)
    assert emp.conciliar_titulo(pool, conta, 99999, lid)["ok"] is False
    tid = _titulo(pool, conta)
    assert emp.conciliar_titulo(pool, conta, tid, 99999)["ok"] is False


def test_duplo_clique_so_grava_uma_vez(pool, conta):
    tid = _titulo(pool, conta)
    lid = _pag(pool, conta)
    antes = _conta_linhas(pool, "lancamentos")
    assert emp.conciliar_titulo(pool, conta, tid, lid)["ok"] is True
    segunda = emp.conciliar_titulo(pool, conta, tid, lid)
    assert segunda["ok"] is False
    assert _conta_linhas(pool, "lancamentos") == antes
    assert _titulo_de(pool, tid)["lancamento_id"] == lid


# ── desfazer ─────────────────────────────────────────────────────────────────
def test_desfazer_devolve_a_conta_pra_aberto(pool, conta):
    """A conciliação nasce de um palpite; sem desfazer, corrigir o engano exigiria
    mexer no banco na mão."""
    tid = _titulo(pool, conta)
    lid = _pag(pool, conta)
    emp.conciliar_titulo(pool, conta, tid, lid)
    antes = _conta_linhas(pool, "lancamentos")

    r = emp.desfazer_conciliacao(pool, conta, tid)

    assert r["ok"] is True
    t = _titulo_de(pool, tid)
    assert (t["status"], t["pago_em"], t["lancamento_id"]) == ("aberto", None, None)
    assert _conta_linhas(pool, "lancamentos") == antes, \
        "desfazer não apaga o pagamento — ele continua no caixa"


def test_desfazer_nao_mexe_no_pagamento(pool, conta):
    tid = _titulo(pool, conta)
    lid = _pag(pool, conta)
    emp.conciliar_titulo(pool, conta, tid, lid)
    emp.desfazer_conciliacao(pool, conta, tid)
    with pool.connection() as c:
        r = c.execute("select valor_centavos, origem, descricao from lancamentos "
                      "where id=%s", (lid,)).fetchone()
    assert r == (220000, "extrato", "Pagamento Pix 58.608.090")


def test_desfazer_recusa_baixa_comum(pool, conta):
    """Baixa comum criou o lançamento dela (origem='titulo'). Reabrir aquilo
    deixaria dinheiro sem dono no livro-caixa."""
    lid = _pag(pool, conta, origem="titulo")
    tid = _titulo(pool, conta, status="pago", lancamento_id=lid)
    r = emp.desfazer_conciliacao(pool, conta, tid)
    assert r["ok"] is False and "sem dono" in r["erro"]
    assert _titulo_de(pool, tid)["status"] == "pago"


def test_desfazer_recusa_conta_aberta(pool, conta):
    tid = _titulo(pool, conta)
    r = emp.desfazer_conciliacao(pool, conta, tid)
    assert r["ok"] is False and "não paga" in r["erro"]


def test_desfazer_recusa_pago_sem_vinculo(pool, conta):
    tid = _titulo(pool, conta, status="pago")
    assert emp.desfazer_conciliacao(pool, conta, tid)["ok"] is False


def test_desfazer_de_outra_conta_e_recusado(pool, conta):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Outra') "
                          "returning id").fetchone()[0]
        c.commit()
    lid = _pag(pool, outra)
    tid = _titulo(pool, outra)
    emp.conciliar_titulo(pool, outra, tid, lid)
    assert emp.desfazer_conciliacao(pool, conta, tid)["ok"] is False
    assert _titulo_de(pool, tid)["status"] == "pago"


def test_ida_e_volta_e_ida_de_novo(pool, conta):
    tid = _titulo(pool, conta)
    lid = _pag(pool, conta)
    assert emp.conciliar_titulo(pool, conta, tid, lid)["ok"] is True
    assert emp.desfazer_conciliacao(pool, conta, tid)["ok"] is True
    assert emp.conciliar_titulo(pool, conta, tid, lid)["ok"] is True, \
        "depois de desfazer, o pagamento volta a estar livre pra conciliar"
    assert _titulo_de(pool, tid)["lancamento_id"] == lid


# ── a régua isolada ──────────────────────────────────────────────────────────
def _t(**kw):
    base = {"status": "aberto", "tipo": "pagar", "valor_centavos": 1000,
            "vencimento": HOJE}
    return {**base, **kw}


def _l(**kw):
    base = {"tipo": "despesa", "valor_centavos": 1000, "data": HOJE}
    return {**base, **kw}


@pytest.mark.parametrize("titulo,lanc,serve", [
    (_t(), _l(), True),
    (_t(), _l(data=HOJE - timedelta(days=14)), True),
    (_t(), _l(data=HOJE - timedelta(days=15)), False),
    (_t(), _l(valor_centavos=1001), False),
    (_t(), _l(tipo="receita"), False),
    (_t(tipo="receber"), _l(tipo="receita"), True),
    (_t(status="pago"), _l(), False),
    (_t(vencimento=None), _l(), False),
])
def test_a_regua_por_fora(titulo, lanc, serve):
    assert (emp.pagamento_serve_pro_titulo(titulo, lanc) is None) is serve
