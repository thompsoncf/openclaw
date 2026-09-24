"""Entrega 4: o card de Despesas por centro, e o planejamento da semana com o saldo informado.

Duas metades, pedidas pelo dono em 23/09/2026:

  * pedido 2 — "um card com 3 tipos de despesas: fixa, eventual, investimento".
    Os três já existiam como CENTROS DE CUSTO da Prime, criados por ele. O card
    de Despesas do Financeiro passa a quebrar a linha "Empresa" por centro, com
    "Sem centro" em âmbar — o tamanho do que escapou nunca some;
  * pedido 3, etapa A — "saldo da conta para planejamento de contas a pagar".
    O dono digita o saldo de cada banco; a aba Empresa responde "tenho X, devo Y
    até o fim da semana, sobra ou falta Z".

O QUE ESTE ARQUIVO FIXA:

  * a soma da quebra FECHA com a linha "Empresa" (só despesa de empresa entra);
  * saldo é HISTÓRICO: informar de novo não apaga o anterior, tirar um banco é
    uma linha nova, e nada de outra conta aparece;
  * sem saldo NÃO há sobra — a tela diz "informe", e não calcula um rombo contra
    zero;
  * saldo velho (3 dias ou mais) é dito como velho;
  * cheque especial (saldo negativo) entra na conta.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp
from finance import saldo_informado as si
from finance.livro_caixa import LivroCaixa
from finance.models import Lancamento, Tipo

_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "053_modulo_pj.sql",
              "057_natureza_lancamento.sql",
              "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql",
              "067_titulos_cliente.sql",
              "131_pessoa_cnpj.sql",
              "132_plano_contas_centros_custo.sql",
              "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql",
              "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql",
              "320_saldo_bancario_informado.sql",
              "325_tipo_despesa.sql")

CONTA, OUTRA = 631, 632
HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True)
    dbname = "zaq_planejamento_semana"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    from contas import equipe as _eq
    _eq.garantir_tabela(p)
    with p.connection() as c:
        for cid, nome in ((CONTA, "Prime Eventos"), (OUTRA, "Vizinha")):
            c.execute("insert into contas (id, nome, tipo) values (%s,%s,'pj') "
                      "on conflict (id) do nothing", (cid, nome))
        c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _limpa(pool):
    with pool.connection() as c:
        for t in ("saldo_bancario_informado", "titulos", "lancamentos", "centros_custo"):
            c.execute(f"delete from {t} where conta_id in (%s,%s)", (CONTA, OUTRA))
        c.commit()
    yield


# ═══════════════════════════════════════════ a quebra por TIPO
#
# A 1ª versão (#820) quebrava por centro de custo; o dono corrigiu em 24/09/2026 —
# "fixa, eventual, investimento não é centro de custo" (migração 325). A quebra
# agora lê `tipo_despesa`, e o centro não entra nela.

def _despesa(pool, valor, natureza="empresa", tipo_d=None, quando=None, tipo=Tipo.DESPESA,
             conta=CONTA, centro=None):
    lanc = Lancamento(tipo=tipo, valor_centavos=valor, categoria="Servicos",
                      descricao=f"gasto {valor}", data=quando or HOJE, origem="foto",
                      natureza=natureza, centro_custo_id=centro, tipo_despesa=tipo_d)
    LivroCaixa(pool, conta).adicionar(lanc, forcar=True)


def test_a_quebra_soma_so_empresa_e_fecha_com_a_linha_empresa(pool):
    from finance import plano_contas as pc
    area = pc.criar_centro(pool, CONTA, "BUFFET")["id"]
    _despesa(pool, 150000, tipo_d="fixa")
    _despesa(pool, 50000, tipo_d="fixa", centro=area)       # o centro não muda o tipo
    _despesa(pool, 300000, tipo_d="investimento")
    _despesa(pool, 70000, centro=area)                      # empresa sem tipo
    _despesa(pool, 99900, natureza="pessoal", tipo_d="fixa")  # não entra
    _despesa(pool, 12300, natureza=None)                    # a definir: não entra
    _despesa(pool, 44400, tipo_d="fixa", quando=HOJE - timedelta(days=45))  # outro mês
    _despesa(pool, 88800, tipo_d="fixa", conta=OUTRA)                        # outra conta
    q = LivroCaixa(pool, CONTA).despesas_empresa_por_tipo(HOJE.year, HOJE.month)
    assert q["tipos"] == [("fixa", 200000), ("eventual", 0), ("investimento", 300000)]
    assert q["sem_tipo"] == 70000 and q["total"] == 570000
    empresa = LivroCaixa(pool, CONTA).resumo_mes_quebra(HOJE.year, HOJE.month)["empresa"]
    assert q["total"] == empresa["despesas"], "a quebra tem que fechar com a linha Empresa"


def test_o_card_mostra_a_quebra_por_tipo_com_o_sem_tipo_em_ambar():
    from tests.test_financeiro_layout import _desenha
    html = _desenha(quebra_tipo={"tipos": [("fixa", 1540838), ("eventual", 393300),
                                           ("investimento", 591913)],
                                 "sem_tipo": 750000, "total": 3276051})
    i = html.index('class="fin-quebra fin-quebra-cc fin-quebra-tipo"')
    bloco = html[i:html.index("</div>\n</div>", i)]
    assert "Empresa por tipo" in bloco and "centro" not in bloco.lower()
    assert bloco.index("Fixa") < bloco.index("Eventual") < bloco.index("Investimento") \
        < bloco.index("Sem tipo")
    assert "15.408,38" in bloco
    assert "var(--ambar)" in bloco[bloco.index("Sem tipo") - 200:]


def test_sem_quebra_o_card_fica_como_era():
    from tests.test_financeiro_layout import _desenha
    marca = 'class="fin-quebra fin-quebra-cc fin-quebra-tipo"'
    assert marca not in _desenha()
    assert marca not in _desenha(quebra_tipo={"tipos": [], "sem_tipo": 0, "total": 0})


def test_tudo_sem_tipo_ainda_aparece():
    """Setembro da Prime em 24/09: 91% do valor sem tipo. O card não pode
    esconder isso — é justamente o que ele existe pra mostrar."""
    from tests.test_financeiro_layout import _desenha
    html = _desenha(quebra_tipo={"tipos": [("fixa", 0), ("eventual", 0),
                                           ("investimento", 0)],
                                 "sem_tipo": 5090391, "total": 5090391})
    assert "Sem tipo" in html and "50.903,91" in html


# ═══════════════════════════════════════════ o saldo informado

def test_informar_de_novo_guarda_o_historico_e_vale_o_ultimo(pool):
    si.informar(pool, CONTA, "Sicoob", 1000000)
    si.informar(pool, CONTA, "  sicoob ", 1840000)          # mesmo banco
    si.informar(pool, CONTA, "Banco do Nordeste", -25000)   # cheque especial
    atuais = {s["banco"]: s["valor_centavos"] for s in si.atuais(pool, CONTA)}
    assert atuais == {"Sicoob": 1840000, "Banco do Nordeste": -25000}
    with pool.connection() as c:
        n = c.execute("select count(*) from saldo_bancario_informado where conta_id=%s",
                      (CONTA,)).fetchone()[0]
    assert n == 3, "informar de novo não pode apagar o informe anterior"


def test_tirar_um_banco_nao_apaga_e_informar_traz_de_volta(pool):
    si.informar(pool, CONTA, "Sicob", 500000)                # digitado errado
    si.informar(pool, CONTA, "Sicoob", 1840000)
    assert si.arquivar(pool, CONTA, "sicob")
    assert [s["banco"] for s in si.atuais(pool, CONTA)] == ["Sicoob"]
    si.informar(pool, CONTA, "Sicob", 1000)
    assert {s["banco"] for s in si.atuais(pool, CONTA)} == {"Sicob", "Sicoob"}
    with pool.connection() as c:
        assert c.execute("select count(*) from saldo_bancario_informado where conta_id=%s",
                         (CONTA,)).fetchone()[0] == 4
    assert not si.arquivar(pool, CONTA, "banco que nunca existiu")


def test_o_saldo_de_uma_conta_nao_aparece_na_outra(pool):
    si.informar(pool, OUTRA, "Sicoob", 999900)
    assert si.atuais(pool, CONTA) == []
    assert not si.arquivar(pool, CONTA, "Sicoob")


def test_o_saldo_envelhece(pool):
    si.informar(pool, CONTA, "Sicoob", 100)
    si.informar(pool, CONTA, "Banco do Nordeste", 100)
    with pool.connection() as c:
        c.execute("update saldo_bancario_informado set informado_em = now() - interval '3 days' "
                  "where conta_id=%s and banco='Sicoob'", (CONTA,))
        c.commit()
    idade = {s["banco"]: (s["dias"], s["velho"]) for s in si.atuais(pool, CONTA)}
    assert idade == {"Sicoob": (3, True), "Banco do Nordeste": (0, False)}


def test_ontem_e_contado_no_relogio_de_brasilia(pool):
    """23h de ontem em Teresina é 02h de hoje em UTC. Pra quem informou, foi ontem."""
    si.informar(pool, CONTA, "Sicoob", 100)
    agora = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)          # 09h em Brasília
    ontem_23h_br = datetime(2026, 9, 24, 2, 0, tzinfo=timezone.utc)    # 23h do dia 23
    with pool.connection() as c:
        c.execute("update saldo_bancario_informado set informado_em=%s where conta_id=%s",
                  (ontem_23h_br, CONTA))
        c.commit()
    assert si.atuais(pool, CONTA, agora=agora)[0]["dias"] == 1


# ═══════════════════════════════════════════ o planejamento

def _conta_a(pool, tipo, valor, venc):
    emp.criar_titulo(pool, CONTA, tipo, f"conta {valor}", valor, venc, contraparte="X")


def test_sem_saldo_nao_existe_sobra(pool):
    _conta_a(pool, "pagar", 1567154, HOJE - timedelta(days=8))
    p = si.planejamento(pool, CONTA)
    assert p["saldo_centavos"] is None and p["sobra_centavos"] is None
    assert p["atrasadas_centavos"] == 1567154


def test_a_conta_da_semana_bate_com_o_mockup(pool):
    """Os números do mockup aprovado: 18.400 − 15.671,54 − 6.008,95 = −3.280,49."""
    si.informar(pool, CONTA, "Sicoob", 1000000)
    si.informar(pool, CONTA, "Banco do Nordeste", 840000)
    _conta_a(pool, "pagar", 1567154, HOJE - timedelta(days=8))       # atrasada
    _conta_a(pool, "pagar", 600895, HOJE + timedelta(days=3))        # na semana
    _conta_a(pool, "pagar", 999999, HOJE + timedelta(days=20))       # fora da semana
    _conta_a(pool, "receber", 354000, HOJE - timedelta(days=4))      # vencida a receber
    p = si.planejamento(pool, CONTA)
    assert (p["saldo_centavos"], p["atrasadas_centavos"], p["a_vencer_centavos"]) == \
           (1840000, 1567154, 600895)
    assert p["sobra_centavos"] == -328049
    assert p["receber_vencidas_centavos"] == 354000 and p["cobrar_cobre"] is True


def test_com_sobra_nao_se_fala_em_cobrar(pool):
    si.informar(pool, CONTA, "Sicoob", 5000000)
    _conta_a(pool, "pagar", 100000, HOJE)
    _conta_a(pool, "receber", 354000, HOJE - timedelta(days=4))
    p = si.planejamento(pool, CONTA)
    assert p["sobra_centavos"] == 4900000 and p["cobrar_cobre"] is False


def test_o_cheque_especial_entra_na_conta(pool):
    si.informar(pool, CONTA, "Sicoob", -50000)
    _conta_a(pool, "pagar", 10000, HOJE + timedelta(days=1))
    assert si.planejamento(pool, CONTA)["sobra_centavos"] == -60000


# ═══════════════════════════════════════════ a tela da Empresa

def _empresa(planej):
    """A aba Empresa de verdade, com o planejamento que a rota entrega."""
    from tests.test_portal_js_sintaxe import PAGINAS, _BASE_CTX, _CONTA
    import web.portal as pt
    ctx = dict(_BASE_CTX)
    ctx.update(PAGINAS["empresa"])
    ctx.update(planej=planej)
    return pt._env.get_template("empresa").render(conta=_CONTA, **ctx)


def _pl(**kw):
    base = {"saldos": [], "saldo_centavos": None, "algum_velho": False,
            "atrasadas_centavos": 1567154, "n_atrasadas": 7,
            "a_vencer_centavos": 600895, "n_a_vencer": 3, "sobra_centavos": None,
            "receber_vencidas_centavos": 354000, "n_receber_vencidas": 2,
            "cobrar_cobre": False, "dias": 7}
    base.update(kw)
    return base


def _card(html):
    i = html.index('id="planejamento"')
    return html[i:html.index('id="titulos"', i)]


def test_sem_saldo_o_card_pede_o_saldo_e_nao_inventa_sobra():
    c = _card(_empresa(_pl()))
    assert "— informe abaixo" in c and "precisa do saldo" in c
    assert "Falta" not in c
    assert "Banco (ex: Sicoob)" in c


def test_com_falta_o_card_diz_quanto_e_o_que_cobrar_resolve():
    agora = datetime.now()
    c = _card(_empresa(_pl(
        saldos=[{"banco": "Sicoob", "valor_centavos": 1840000, "informado_em": agora,
                 "dias": 0, "velho": False}],
        saldo_centavos=1840000, sobra_centavos=-328049, cobrar_cobre=True)))
    assert "R$ 18.400,00" in c and "− R$ 3.280,49" in c
    assert "Falta R$ 3.280,49 pra cobrir a semana" in c
    assert "cobrar cobre a diferença" in c
    assert "informado hoje" in c


def test_saldo_velho_pede_pra_atualizar():
    c = _card(_empresa(_pl(
        saldos=[{"banco": "Sicoob", "valor_centavos": 100, "informado_em": datetime.now(),
                 "dias": 4, "velho": True}],
        saldo_centavos=100, sobra_centavos=-2168000, algum_velho=True)))
    assert 'class="plj-banco velho"' in c and "há 4 dias — atualize" in c
    assert "algum saldo está desatualizado" in c


def test_nome_de_banco_nao_quebra_a_pagina():
    """O nome é texto do usuário e vai parar num atributo, num confirm e na tela
    — e neste Environment o autoescape está DESLIGADO (select_autoescape não
    reconhece "empresa" como HTML), então o `|e` de cada lugar é o que segura."""
    c = _card(_empresa(_pl(
        saldos=[{"banco": 'Banco "X" <b>', "valor_centavos": 100,
                 "informado_em": datetime.now(), "dias": 0, "velho": False}],
        saldo_centavos=100, sobra_centavos=0)))
    bancos = c.split('class="plj-bancos"')[1]
    assert 'Banco "X" <b>' not in bancos, "o nome saiu cru dentro do HTML"
    assert bancos.count("Banco &#34;X&#34; &lt;b&gt;") == 4   # value, nome, aria, confirm
