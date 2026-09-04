"""Multa e juros do boleto pago depois do vencimento (migração 197).

Pedido do dono em 04/09/2026: *"vamos implementar a questão dos boletos que são
pagos depois do vencimento, tem que ter o campo juros e mora né"*. Aprovou a
**opção C** do mockup em `docs/mockups/juros_e_multa.html`.

Faltavam DUAS coisas, e a segunda era maior:

1. **O campo.** O "dar baixa" não perguntava nada — nem valor, nem data do
   pagamento. Lançava o valor de face na data de hoje. Aconteceu com ele no
   mesmo dia: o IPTU 2026 3/6 venceu 31/08 e foi baixado em 04/09, quatro dias
   depois, com lançamento de R$ 1.160,85 — exatamente a face.

2. **A régua do extrato.** `pagamento_serve_pro_titulo` exigia valor IDÊNTICO, e
   boleto pago em atraso chega ao extrato COM multa e juros. O débito de
   R$ 2.258,67 nunca fecharia a ZARB de R$ 2.200,00, vencida há 20 dias.
   Sobravam dar baixa (lançando a despesa uma segunda vez, em cima da que veio
   do extrato) ou editar o valor da conta (apagando quanto se devia).

O que este arquivo protege:

  * **a sugestão é a regra da CASA**, lida de `finance/contrato.py` (cláusula 3.4:
    multa 2% + juros de mora 1% ao mês) — e não um número solto aqui;
  * **o acréscimo vira lançamento PRÓPRIO**, em conta financeira, e não engorda o
    principal: senão o juros virava custo de fornecedor no DRE;
  * **os quatro destinos**, que são quatro fatos contábeis e não um com o sinal
    trocado (paguei/recebi × atrasado/adiantado);
  * **o valor do título não muda** — ele continua sendo o que se devia;
  * **a régua nova tem teto e exige atraso**, e continua recusando pagamento a
    MENOS: fechar conta com dinheiro faltando esconderia dívida, que é o pior
    erro deste módulo;
  * **a conciliação NÃO quebra a linha do extrato em duas** — aquilo é dado do
    banco.
"""
import os
import subprocess
from datetime import date, timedelta

import pytest
from psycopg_pool import ConnectionPool

from finance import empresa as emp
from web import portal as pt

HOJE = date.today()

_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text);
create table pessoas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text);
create table clientes (id bigserial primary key, dono_id bigint, pessoa_id bigint, nome text);
create table lancamentos (
  id bigserial primary key, conta_id bigint not null, membro_id bigint,
  tipo text not null, valor_centavos bigint not null, categoria text not null,
  descricao text not null default '', data date not null,
  pagamento text, forma_pagamento text, origem text not null default 'manual',
  comprovante text, chave text, natureza text,
  plano_conta_id bigint, centro_custo_id bigint, vendedor_id bigint);
create table titulos (
  id bigserial primary key, conta_id bigint, tipo text, descricao text,
  contraparte text not null default '', valor_centavos bigint,
  vencimento date, status text not null default 'aberto',
  recorrente boolean not null default false,
  periodicidade text, valor_variavel boolean not null default false,
  acrescimo_centavos int not null default 0, lancamento_acrescimo_id bigint,
  categoria text, cobranca_link_url text,
  pago_em date, lancamento_id bigint, cliente_id bigint, criado_por bigint,
  aprovacao text not null default 'autorizado', aprovado_por bigint,
  aprovado_em timestamptz, aprovacao_motivo text,
  pago_sem_autorizacao boolean not null default false);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_juros_test"
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
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Prime Juros')"
                        " returning id").fetchone()[0]
        c.commit()
    return cid


def _lancs(pool, conta_id):
    with pool.connection() as c:
        return c.execute(
            "select tipo, valor_centavos, categoria, descricao, data from lancamentos"
            " where conta_id=%s order by id", (conta_id,)).fetchall()


def _titulo(pool, conta_id, **kw):
    with pool.connection() as c:
        return c.execute(
            "select acrescimo_centavos, valor_centavos, lancamento_id,"
            "       lancamento_acrescimo_id, pago_em from titulos"
            " where conta_id=%s order by id desc limit 1", (conta_id,)).fetchone()


# ═══════════════ a sugestão ═══════════════
def test_sem_atraso_nao_sugere_nada():
    r = emp.acrescimo_sugerido(220000, date(2026, 9, 15), date(2026, 9, 15))
    assert r == {"dias": 0, "multa_centavos": 0, "juros_centavos": 0, "centavos": 0}
    assert emp.acrescimo_sugerido(220000, date(2026, 9, 15),
                                  date(2026, 9, 10))["centavos"] == 0


def test_a_zarb_vinte_dias_atrasada():
    """O caso real: R$ 2.200,00 vencida em 15/08, olhada em 04/09."""
    r = emp.acrescimo_sugerido(220000, date(2026, 8, 15), date(2026, 9, 4))
    assert r["dias"] == 20
    assert r["multa_centavos"] == 4400        # 2%
    assert r["juros_centavos"] == 1467        # 1% ao mês × 20/30
    assert r["centavos"] == 5867              # R$ 58,67


def test_a_energia_solar_trinta_dias():
    r = emp.acrescimo_sugerido(287777, date(2026, 8, 5), date(2026, 9, 4))
    assert (r["dias"], r["centavos"]) == (30, 5756 + 2878)


def test_o_juros_e_proporcional_aos_dias_com_mes_de_trinta():
    """"1% ao mês" é o que a cláusula diz, e o boleto conta em dias. Mês civil
    daria número diferente em fevereiro pro mesmo atraso."""
    quinze = emp.acrescimo_sugerido(100000, date(2026, 8, 1), date(2026, 8, 16))
    trinta = emp.acrescimo_sugerido(100000, date(2026, 8, 1), date(2026, 8, 31))
    assert quinze["juros_centavos"] == 500 and trinta["juros_centavos"] == 1000


def test_a_regra_vem_do_contrato_da_casa():
    """Não é número inventado no financeiro: é a cláusula 3.4 do contrato que a
    Prime manda pros clientes dela. Um lugar só — se o dono mudar a cláusula, a
    sugestão muda junto."""
    from finance.contrato import REGRAS_PADRAO
    assert emp._regras_da_casa() == (float(REGRAS_PADRAO["multa_atraso_pct"]),
                                     float(REGRAS_PADRAO["juros_mora_pct_mes"]))


def test_conta_sem_valor_nao_gera_juros():
    """A conta de valor variável (196) nasce sem valor: 2% de nada é nada."""
    r = emp.acrescimo_sugerido(0, date(2026, 8, 15), date(2026, 9, 4))
    assert r["dias"] == 20 and r["centavos"] == 0


# ═══════════════ a baixa ═══════════════
def test_o_acrescimo_vira_lancamento_proprio(pool, conta):
    """Somar tudo numa linha só faria o juros virar custo de fornecedor no DRE —
    e "quanto paguei de juros esse ano?" continuaria sem resposta."""
    venc = HOJE - timedelta(days=20)
    t = emp.criar_titulo(pool, conta, "pagar", "ZARB CONSULTORIA", 220000, venc,
                         contraparte="ZARB ASSESSORIA")
    r = emp.dar_baixa_titulo(pool, conta, t["id"], acrescimo_centavos=5867)
    assert r["ok"] and r["lancamento_acrescimo_id"]
    ls = _lancs(pool, conta)
    assert len(ls) == 2
    assert (ls[0][1], ls[0][2]) == (220000, "Fornecedores")
    assert (ls[1][1], ls[1][2]) == (5867, "Juros e multas")
    assert ls[1][0] == "despesa"
    assert "20 dias de atraso" in ls[1][3]


def test_o_valor_do_titulo_nao_muda(pool, conta):
    """"Quanto era" e "quanto saiu" seguem sendo duas perguntas com duas
    respostas. Mexer no valor apagaria o que se devia."""
    venc = HOJE - timedelta(days=20)
    t = emp.criar_titulo(pool, conta, "pagar", "ZARB", 220000, venc)
    emp.dar_baixa_titulo(pool, conta, t["id"], acrescimo_centavos=5867)
    acr, valor, _, acr_id, _ = _titulo(pool, conta)
    assert valor == 220000 and acr == 5867 and acr_id


def test_sem_acrescimo_continua_um_lancamento_so(pool, conta):
    """Conta paga no prazo não ganha uma linha de R$ 0,00 no caixa."""
    t = emp.criar_titulo(pool, conta, "pagar", "CONTABILIDADE", 100000, HOJE)
    r = emp.dar_baixa_titulo(pool, conta, t["id"])
    assert len(_lancs(pool, conta)) == 1
    assert r["lancamento_acrescimo_id"] is None


def test_a_data_do_pagamento_e_respeitada(pool, conta):
    """Quem paga na sexta e registra na segunda tem uma data pra informar — antes
    a baixa cravava hoje, sem perguntar."""
    ontem = HOJE - timedelta(days=1)
    t = emp.criar_titulo(pool, conta, "pagar", "INTERNET", 9490, HOJE)
    emp.dar_baixa_titulo(pool, conta, t["id"], data_pagto=ontem)
    assert _lancs(pool, conta)[0][4] == ontem
    assert _titulo(pool, conta)[4] == ontem


# ═══════════════ os quatro destinos ═══════════════
@pytest.mark.parametrize("tipo,acr,esperado", [
    ("pagar",    5867,  ("despesa", "Juros e multas")),
    ("pagar",   -2000,  ("receita", "Descontos obtidos")),
    ("receber",  5867,  ("receita", "Juros recebidos")),
    ("receber", -2000,  ("despesa", "Descontos concedidos")),
])
def test_os_quatro_destinos_do_acrescimo(pool, conta, tipo, acr, esperado):
    """São quatro FATOS contábeis, não um com o sinal trocado: o desconto que eu
    obtive é receita minha; o que eu concedi é despesa."""
    venc = HOJE - timedelta(days=20) if acr > 0 else HOJE + timedelta(days=5)
    t = emp.criar_titulo(pool, conta, tipo, "CONTA X", 100000, venc)
    emp.dar_baixa_titulo(pool, conta, t["id"], acrescimo_centavos=acr)
    segundo = _lancs(pool, conta)[1]
    assert (segundo[0], segundo[2]) == esperado
    assert segundo[1] == abs(acr), "o lançamento do acréscimo vai sempre positivo"


# ═══════════════ a régua do extrato ═══════════════
def _lanc(valor, dias, tipo="despesa"):
    return {"valor_centavos": valor, "data": HOJE + timedelta(days=dias),
            "tipo": tipo, "origem": "extrato", "descricao": "PAGAMENTO PIX"}


def _tit(valor=220000, venc_dias=-20, tipo="pagar"):
    return {"valor_centavos": valor, "vencimento": HOJE + timedelta(days=venc_dias),
            "tipo": tipo, "status": "aberto", "descricao": "ZARB CONSULTORIA",
            "contraparte": "ZARB ASSESSORIA"}


def test_o_extrato_aceita_o_boleto_pago_com_juros():
    """O caso que ia acontecer: a ZARB vencida há 20 dias, paga com juros,
    chegando pelo OFX. Antes: "O valor do pagamento não bate com o da conta"."""
    assert emp.pagamento_serve_pro_titulo(_tit(), _lanc(225867, 0)) is None


def test_pagar_a_menos_continua_recusado():
    """Pode ser pagamento parcial, pode ser outra conta parecida — e fechar aqui
    esconderia dívida, que é o pior erro deste módulo."""
    assert emp.pagamento_serve_pro_titulo(_tit(), _lanc(200000, 0)) is not None


def test_sobra_sem_atraso_e_recusada():
    """Sem atraso não há multa nem juros que expliquem a diferença."""
    erro = emp.pagamento_serve_pro_titulo(_tit(venc_dias=3), _lanc(225867, 0))
    assert erro is not None


def test_sobra_acima_do_teto_e_recusada():
    """Afrouxar sem limite deixaria um pagamento de R$ 5.000 fechar uma conta de
    R$ 2.200 — pior que o problema que estamos resolvendo."""
    teto = int(220000 * emp.ACRESCIMO_TETO_PCT / 100)
    assert emp.pagamento_serve_pro_titulo(_tit(), _lanc(220000 + teto, 0)) is None
    assert emp.pagamento_serve_pro_titulo(_tit(), _lanc(220000 + teto + 1, 0)) is not None


def test_o_acrescimo_do_pagamento_distingue_zero_de_recusa():
    """Zero é resposta legítima (pagamento exato); None é recusa. Quem chama tem
    que poder separar os dois."""
    assert emp.acrescimo_do_pagamento(_tit(), _lanc(220000, 0)) == 0
    assert emp.acrescimo_do_pagamento(_tit(), _lanc(225867, 0)) == 5867
    assert emp.acrescimo_do_pagamento(_tit(), _lanc(200000, 0)) is None


def test_a_janela_do_atraso_abre_mas_nao_e_infinita():
    """Os 14 dias existem pra não confundir duas ocorrências de MESMO valor de
    uma conta que repete. O boleto com juros não tem o mesmo valor de nenhuma
    outra — e o atraso dele quase sempre passa de 14 dias (a ZARB, 20). Com a
    janela curta, o conserto não pegaria o caso que o motivou."""
    assert emp.pagamento_serve_pro_titulo(
        _tit(venc_dias=-40), _lanc(225867, 0)) is None            # 40 dias: passa
    longe = emp.pagamento_serve_pro_titulo(
        _tit(venc_dias=-(emp.JANELA_ATRASO_DIAS + 1)), _lanc(225867, 0))
    assert longe is not None, "depois de 90 dias já é renegociação, não atraso"


def test_sem_acrescimo_a_janela_curta_continua_valendo():
    """A régua afrouxou pro pagamento COM juros e só pra ele: pagamento exato
    longe do vencimento segue sendo suspeito de ser de outro mês."""
    erro = emp.pagamento_serve_pro_titulo(_tit(venc_dias=-40), _lanc(220000, 0))
    assert erro is not None and "dias do" in erro


def test_a_dica_encontra_o_pagamento_com_juros(pool, conta):
    venc = HOJE - timedelta(days=20)
    t = emp.criar_titulo(pool, conta, "pagar", "ZARB CONSULTORIA", 220000, venc,
                         contraparte="ZARB ASSESSORIA")
    with pool.connection() as c:
        c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos,
                       categoria, descricao, data, origem, natureza)
                     values (%s,'despesa',225867,'Outros','PIX ZARB',%s,'extrato','empresa')""",
                  (conta, venc + timedelta(days=1)))
        c.commit()
    abertos = emp.listar_titulos(pool, conta, status="aberto")
    achados = emp.pagamentos_candidatos(pool, conta, abertos, "pagar")
    assert t["id"] in achados, "o pagamento com juros não foi sugerido"
    assert achados[t["id"]]["centavos"] == 225867, "o botão tem que mostrar o que SAIU"
    assert achados[t["id"]]["acrescimo_centavos"] == 5867


def test_conciliar_guarda_o_acrescimo_sem_quebrar_a_linha_do_extrato(pool, conta):
    """Ali o dinheiro já entrou como UMA linha do banco, com o valor cheio.
    Quebrá-la em duas seria reescrever dado vindo do extrato."""
    venc = HOJE - timedelta(days=20)
    t = emp.criar_titulo(pool, conta, "pagar", "ZARB CONSULTORIA", 220000, venc,
                         contraparte="ZARB ASSESSORIA")
    with pool.connection() as c:
        lid = c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos,
                             categoria, descricao, data, origem, natureza)
                           values (%s,'despesa',225867,'Outros','PIX ZARB',%s,'extrato','empresa')
                           returning id""", (conta, venc + timedelta(days=1))).fetchone()[0]
        c.commit()
    r = emp.conciliar_titulo(pool, conta, t["id"], lid)
    assert r["ok"]
    acr, valor, lanc_id, acr_id, _ = _titulo(pool, conta)
    assert acr == 5867 and valor == 220000
    assert acr_id is None, "conciliação não cria segundo lançamento"
    assert len(_lancs(pool, conta)) == 1, "o extrato continua com uma linha só"


# ═══════════════ a tela ═══════════════
def test_o_painel_da_baixa_tem_a_data_e_o_acrescimo():
    assert 'name="pago_em"' in pt._EMPRESA
    assert 'name="acrescimo"' in pt._EMPRESA
    assert 'class="tit-baixa"' in pt._EMPRESA


def test_dar_baixa_abre_o_painel_em_vez_de_gravar_direto():
    """Ela gravava o valor de face na data de hoje sem perguntar nada, e as duas
    coisas estavam erradas."""
    i = pt._EMPRESA.index("{% macro tit_linha")
    j = pt._EMPRESA.index("{% for t in titulos_pagos %}")
    macro = pt._EMPRESA[i:j]
    assert 'onclick="titBaixaToggle(this)"' in macro
    assert "dar baixa ✓</button>" in macro


def test_a_regra_da_casa_chega_na_tela_como_numero():
    """Escrever "2%" no JavaScript criaria uma segunda verdade, que divergiria na
    primeira vez que o dono mudasse a cláusula."""
    assert 'data-multa="{{ MULTA_ATRASO_PCT }}"' in pt._EMPRESA
    assert 'data-juros="{{ JUROS_MORA_PCT_MES }}"' in pt._EMPRESA
    fonte = open(pt.__file__, encoding="utf-8").read()
    i = fonte.index('return _render("empresa", request')
    assert "MULTA_ATRASO_PCT=_multa_pct" in fonte[i - 1200:i + 400]


def test_a_conta_paga_mostra_o_juros_e_o_total():
    pagos = pt._EMPRESA[pt._EMPRESA.index("{% for t in titulos_pagos %}"):]
    assert "t.acrescimo_centavos" in pagos
    assert "(t.valor_centavos + t.acrescimo_centavos)|brl" in pagos


def test_o_javascript_do_painel_e_valido():
    """A lição do `'?\\n\\n'`: um `\\n` de Python virando quebra de linha de
    verdade dentro de uma string JS derruba o bloco `<script>` INTEIRO, sem erro
    visível. Aqui o bloco novo passa pelo `node --check`."""
    import re
    blocos = re.findall(r"<script>(.*?)</script>", pt._EMPRESA, re.S)
    jinja_free = [b for b in blocos if "{{" not in b and "{%" not in b]
    assert any("titBaixaConta" in b for b in jinja_free), \
        "o bloco do painel da baixa não está entre os conferidos"
    for b in jinja_free:
        r = subprocess.run(["node", "--check", "-"], input=b, text=True,
                           capture_output=True)
        assert r.returncode == 0, r.stderr


def test_o_conversor_preserva_o_sinal():
    """Valor de conta negativo é erro de digitação; acréscimo negativo é o
    desconto de quem pagou adiantado. Um conversor só escolheria errado pra
    metade dos casos."""
    assert pt._acrescimo_para_centavos("58,67") == 5867
    assert pt._acrescimo_para_centavos("-20,00") == -2000
    assert pt._acrescimo_para_centavos("−20,00") == -2000   # o menos U+2212
    assert pt._acrescimo_para_centavos("") == 0
    assert pt._acrescimo_para_centavos("mil reais") == 0


def test_a_rota_da_baixa_aceita_data_e_acrescimo():
    fonte = open(pt.__file__, encoding="utf-8").read()
    i = fonte.index('@router.post("/painel/empresa/titulo/{titulo_id}/baixa")')
    corpo = fonte[i:i + 1800]
    assert "pago_em: str = Form" in corpo and "acrescimo: str = Form" in corpo
    assert "_acrescimo_para_centavos(acrescimo)" in corpo


def test_o_template_compila():
    from jinja2 import Environment
    Environment().parse(pt._EMPRESA)
