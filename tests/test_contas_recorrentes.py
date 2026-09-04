"""A conta que repete — o ritmo, o valor que ela não sabe, e a trava da duplicata.

Pedido do dono em 04/09/2026: *"vamos ver como colocar um botão em contas a pagar
recorrentes, tipo água, luz, aluguel"*. Ele aprovou a **opção C** do mockup em
`docs/mockups/contas_recorrentes.html`.

O achado que deu o tamanho da mudança: **o motor já existia e estava desligado**.
`titulos.recorrente` é lido pelo `dar_baixa_titulo` desde a 053 — ele já cria
sozinho o título seguinte, herdando a aprovação. O que nunca existiu foi a PORTA:
nem o formulário, nem a linha, nem o editar sabiam ligar o campo, e a única coisa
que o gravava era a ferramenta do agente do WhatsApp. Medido na Prime: **0 de 39
títulos** marcados, e o dono redigitando ENERGIA SOLAR, BANCO DO NORDESTE, ZARB e
IPTU todo mês, na mão.

As 33 contas a pagar abertas dela foram quem escolheu o desenho:

    quinzenal              12   as quinzenas do time
    mensal, valor fixo     11   ZARB, banco, energia solar, contabilidade…
    mensal, valor que muda  6   água, luz, cartão, DAS, FGTS, INSS
    parcelado               2   IPTU 3/6 e 4/6 — repete, mas ACABA
    avulsa                  2   diária do pedreiro, comissão

O que este arquivo protege, e por quê:

  * **o ritmo**, porque "todo mês" sozinho deixaria de fora o maior bloco;
  * **o valor que não vem junto**: quatro contas estão cadastradas com R$ 0,01,
    que é o marcador de "o boleto ainda não chegou". Copiar isso pra frente
    espalharia o centavo por todos os meses seguintes;
  * **a trava da duplicata**, que não é teórica: a ZARB de agosto e a de setembro
    estão AS DUAS abertas, digitadas na mão. Sem a trava, marcar a de agosto e
    pagá-la criaria uma segunda ZARB de setembro em cima da que já está lá;
  * **e o contrário da trava**: FGTS, INSS e DAS têm o MESMO fornecedor e o MESMO
    vencimento sendo três contas diferentes. Uma trava por fornecedor+data
    engoliria duas delas — e conta engolida é pior que conta repetida.
"""
import os
import re
from datetime import date, timedelta

import pytest
from psycopg_pool import ConnectionPool

from finance import empresa as emp
import web.painel_relatorios as rel
from web import portal as pt

HOJE = date.today()

# BANCO PRÓPRIO e esquema à mão, pelo mesmo motivo do test_conciliar_na_empresa:
# escolher migrações a dedo passa aqui (banco descartável sujo de rodadas
# anteriores) e quebra no CI, que começa limpo toda vez — foi o vermelho do #616.
#
# As duas constraints da 196 estão AQUI de propósito, e não só no arquivo da
# migração: é o que garante que o código nunca tenta gravar a combinação que o
# banco de verdade recusa (ritmo sem interruptor, ou zero fora do valor variável).
_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text);
create table pessoas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text,
  email text);
create table clientes (id bigserial primary key, dono_id bigint, pessoa_id bigint,
  nome text);
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
  categoria text, cobranca_link_url text,
  pago_em date, lancamento_id bigint, cliente_id bigint, criado_por bigint,
  aprovacao text not null default 'autorizado', aprovado_por bigint,
  aprovado_em timestamptz, aprovacao_motivo text,
  pago_sem_autorizacao boolean not null default false,
  constraint titulos_periodicidade_ck
    check (periodicidade is null or periodicidade in ('quinzenal','mensal','anual')),
  constraint titulos_periodicidade_exige_recorrente_ck
    check (periodicidade is null or recorrente),
  constraint titulos_valor_ck
    check (valor_centavos > 0 or (valor_variavel and valor_centavos = 0)));
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True)
    dbname = "zaq_recorrentes_test"
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
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Prime Repete')"
                        " returning id").fetchone()[0]
        c.commit()
    return cid


def _abertos(pool, conta_id):
    return emp.listar_titulos(pool, conta_id, status="aberto")


def _acha(pool, conta_id, descricao, venc):
    with pool.connection() as c:
        return c.execute(
            "select id, valor_centavos, periodicidade, valor_variavel, aprovacao"
            "  from titulos where conta_id=%s and descricao=%s and vencimento=%s",
            (conta_id, descricao, venc)).fetchall()


# ═══════════════════ o ritmo ═══════════════════
def test_quinzenal_anda_de_quinze_em_quinze_dias():
    """15 DIAS CORRIDOS, e não "duas vezes por mês": é como a folha da Prime
    anda — a 1ª quinzena de setembro vence 15/09 e a 2ª, 30/09."""
    assert emp.proxima_data(date(2026, 9, 15), "quinzenal") == date(2026, 9, 30)
    assert emp.proxima_data(date(2026, 9, 30), "quinzenal") == date(2026, 10, 15)


def test_mensal_cai_no_mes_seguinte_e_nao_inventa_31_de_fevereiro():
    assert emp.proxima_data(date(2026, 9, 15), "mensal") == date(2026, 10, 15)
    assert emp.proxima_data(date(2026, 12, 10), "mensal") == date(2027, 1, 10)
    assert emp.proxima_data(date(2026, 1, 31), "mensal") == date(2026, 2, 28)


def test_anual_atravessa_o_ano():
    assert emp.proxima_data(date(2026, 9, 30), "anual") == date(2027, 9, 30)
    assert emp.proxima_data(date(2028, 2, 29), "anual") == date(2029, 2, 28)


def test_sem_ritmo_le_se_mensal():
    """Era a única regra que existia antes da 196, e os títulos recorrentes
    antigos dependem de continuar assim."""
    assert emp.proxima_data(date(2026, 9, 15)) == emp.proxima_data(date(2026, 9, 15), "mensal")


# ═══════════════════ ligar e desligar ═══════════════════
def test_pedir_o_ritmo_ja_liga_o_interruptor(pool, conta):
    """As duas metades da mesma verdade. Se cada porta tivesse que lembrar de
    ligar `recorrente` junto, a que esquecesse gravaria uma linha que o banco
    recusa (a constraint da 196)."""
    t = emp.criar_titulo(pool, conta, "pagar", "ÁGUAS DE TERESINA", 9490,
                         HOJE, periodicidade="mensal")
    assert t["recorrente"] is True and t["periodicidade"] == "mensal"


def test_interruptor_sem_ritmo_cai_em_mensal(pool, conta):
    """É o que `recorrente=true` sempre quis dizer — inclusive quando quem chama
    é a ferramenta do agente do WhatsApp, que só conhece o booleano."""
    t = emp.criar_titulo(pool, conta, "pagar", "INTERNET", 9490, HOJE,
                         recorrente=True)
    assert t["periodicidade"] == "mensal"


def test_sem_nada_continua_avulso(pool, conta):
    t = emp.criar_titulo(pool, conta, "pagar", "DIARIAS PEDREIRO", 165000, HOJE)
    assert t["recorrente"] is False and t["periodicidade"] is None


def test_ritmo_que_nao_existe_e_recusado(pool, conta):
    with pytest.raises(ValueError):
        emp.criar_titulo(pool, conta, "pagar", "X", 100, HOJE,
                         periodicidade="semanal")


def test_valor_variavel_nao_existe_fora_da_recorrencia(pool, conta):
    """"Repete a data, não o valor" não quer dizer nada numa conta que não
    repete — e liberar o zero ali abriria a exceção da constraint pro resto da
    base."""
    t = emp.criar_titulo(pool, conta, "pagar", "AVULSA", 5000, HOJE,
                         valor_variavel=True)
    assert t["valor_variavel"] is False


def test_definir_recorrencia_liga_troca_e_desliga(pool, conta):
    t = emp.criar_titulo(pool, conta, "pagar", "ZARB CONSULTORIA", 220000, HOJE)
    emp.definir_recorrencia(pool, conta, t["id"], "mensal")
    assert _abertos(pool, conta)[0]["periodicidade"] == "mensal"
    emp.definir_recorrencia(pool, conta, t["id"], "quinzenal")
    assert _abertos(pool, conta)[0]["periodicidade"] == "quinzenal"
    emp.definir_recorrencia(pool, conta, t["id"], None)
    linha = _abertos(pool, conta)[0]
    assert linha["periodicidade"] is None and linha["recorrente"] is False


def test_valor_variavel_nao_se_desliga_enquanto_o_valor_e_zero(pool, conta):
    """Senão a própria linha que já está no banco viraria ilegal: valor zero só
    é permitido em conta de valor variável. Pra tirar a marca, põe-se o valor —
    que é a ordem natural de quem recebeu o boleto."""
    t = emp.criar_titulo(pool, conta, "pagar", "ÁGUAS DE TERESINA", 0, HOJE,
                         periodicidade="mensal", valor_variavel=True)
    emp.definir_recorrencia(pool, conta, t["id"], "mensal", valor_variavel=False)
    assert _abertos(pool, conta)[0]["valor_variavel"] is True
    emp.editar_titulo(pool, conta, t["id"], valor_centavos=13890)
    emp.definir_recorrencia(pool, conta, t["id"], "mensal", valor_variavel=False)
    assert _abertos(pool, conta)[0]["valor_variavel"] is False


# ═══════════════════ a baixa cria a próxima ═══════════════════
def test_a_quinzenal_nasce_quinze_dias_depois(pool, conta):
    venc = HOJE - timedelta(days=3)
    t = emp.criar_titulo(pool, conta, "pagar", "1 QUINZENA SETEMBRO/26 BETO",
                         83750, venc, contraparte="ROBERTO LOPES",
                         periodicidade="quinzenal")
    r = emp.dar_baixa_titulo(pool, conta, t["id"])
    assert r["ok"] and r["proximo_titulo_id"]
    prox = _acha(pool, conta, "1 QUINZENA SETEMBRO/26 BETO", venc + timedelta(days=15))
    assert len(prox) == 1 and prox[0][2] == "quinzenal"


def test_a_de_valor_fixo_carrega_o_valor(pool, conta):
    venc = HOJE - timedelta(days=3)
    t = emp.criar_titulo(pool, conta, "pagar", "ZARB CONSULTORIA", 220000, venc,
                         periodicidade="mensal")
    emp.dar_baixa_titulo(pool, conta, t["id"])
    prox = _acha(pool, conta, "ZARB CONSULTORIA", emp.proxima_data(venc, "mensal"))
    assert prox[0][1] == 220000


def test_a_de_valor_variavel_nasce_sem_valor(pool, conta):
    """O R$ 0,01 da água é MARCADOR, não preço: quatro contas da Prime estavam
    assim. Copiar o valor espalharia esse centavo por todos os meses seguintes —
    a conta apareceria paga por um número que nunca foi o dela."""
    venc = HOJE - timedelta(days=3)
    t = emp.criar_titulo(pool, conta, "pagar", "ÁGUAS DE TERESINA", 1, venc,
                         periodicidade="mensal", valor_variavel=True)
    emp.dar_baixa_titulo(pool, conta, t["id"])
    prox = _acha(pool, conta, "ÁGUAS DE TERESINA", emp.proxima_data(venc, "mensal"))
    assert prox[0][1] == 0, "a próxima veio com o valor da anterior"
    assert prox[0][3] is True, "a próxima esqueceu que o valor varia"


def test_a_proxima_herda_a_liberacao(pool, conta):
    """O aluguel liberado em janeiro não volta a perguntar em fevereiro — fila
    que repete pergunta respondida é fila que alguém desliga."""
    venc = HOJE - timedelta(days=3)
    t = emp.criar_titulo(pool, conta, "pagar", "CONTABILIDADE", 100000, venc,
                         periodicidade="mensal")
    emp.decidir_aprovacao(pool, conta, [t["id"]], "autorizado")
    emp.dar_baixa_titulo(pool, conta, t["id"])
    prox = _acha(pool, conta, "CONTABILIDADE", emp.proxima_data(venc, "mensal"))
    assert prox[0][4] == "autorizado"


def test_conta_avulsa_nao_gera_nada(pool, conta):
    t = emp.criar_titulo(pool, conta, "pagar", "DIARIAS PEDREIRO", 165000, HOJE)
    r = emp.dar_baixa_titulo(pool, conta, t["id"])
    assert r["ok"] and r["proximo_titulo_id"] is None


# ═══════════════════ a trava da duplicata ═══════════════════
def test_nao_cria_a_de_setembro_se_o_dono_ja_digitou(pool, conta):
    """O caso REAL da Prime: a ZARB de agosto e a de setembro estão as duas
    abertas, digitadas na mão. Marcar a de agosto e pagá-la criaria uma segunda
    ZARB de setembro em cima da que já está lá."""
    agosto = HOJE - timedelta(days=20)
    setembro = emp.proxima_data(agosto, "mensal")
    t_ago = emp.criar_titulo(pool, conta, "pagar", "ZARB CONSULTORIA", 220000,
                             agosto, periodicidade="mensal")
    emp.criar_titulo(pool, conta, "pagar", "ZARB CONSULTORIA", 220000, setembro)
    r = emp.dar_baixa_titulo(pool, conta, t_ago["id"])
    assert r["ok"] and r["proximo_titulo_id"] is None
    assert len(_acha(pool, conta, "ZARB CONSULTORIA", setembro)) == 1


def test_a_trava_ignora_maiuscula_e_espaco(pool, conta):
    agosto = HOJE - timedelta(days=20)
    setembro = emp.proxima_data(agosto, "mensal")
    t_ago = emp.criar_titulo(pool, conta, "pagar", "Energia Solar", 287777,
                             agosto, periodicidade="mensal")
    emp.criar_titulo(pool, conta, "pagar", "  ENERGIA SOLAR  ", 287777, setembro)
    emp.dar_baixa_titulo(pool, conta, t_ago["id"])
    with pool.connection() as c:
        n = c.execute("select count(*) from titulos where conta_id=%s and"
                      " vencimento=%s", (conta, setembro)).fetchone()[0]
    assert n == 1


def test_a_trava_nao_engole_conta_diferente_do_mesmo_fornecedor_no_mesmo_dia(pool, conta):
    """FGTS, INSS e DAS têm o MESMO fornecedor ("IMPOSTOS (FGTS/INSS/DAS)") e o
    MESMO vencimento sendo três contas diferentes. Uma trava por fornecedor+data
    engoliria duas — e conta engolida é pior que conta repetida: a repetida a
    pessoa vê e apaga; a engolida ninguém procura."""
    agosto = HOJE - timedelta(days=20)
    setembro = emp.proxima_data(agosto, "mensal")
    das = emp.criar_titulo(pool, conta, "pagar", "DAS", 45000, agosto,
                           contraparte="IMPOSTOS (FGTS/INSS/DAS)",
                           periodicidade="mensal")
    emp.criar_titulo(pool, conta, "pagar", "FGTS", 13400, setembro,
                     contraparte="IMPOSTOS (FGTS/INSS/DAS)")
    r = emp.dar_baixa_titulo(pool, conta, das["id"])
    assert r["proximo_titulo_id"], "o DAS do mês seguinte foi engolido pelo FGTS"


def test_a_trava_so_olha_conta_aberta(pool, conta):
    """Uma conta do mês que vem já PAGA não é motivo pra não criar a próxima —
    ela não está mais esperando ninguém."""
    agosto = HOJE - timedelta(days=20)
    setembro = emp.proxima_data(agosto, "mensal")
    velha = emp.criar_titulo(pool, conta, "pagar", "SEGURANÇA", 25190, setembro)
    emp.dar_baixa_titulo(pool, conta, velha["id"])
    t_ago = emp.criar_titulo(pool, conta, "pagar", "SEGURANÇA", 25190, agosto,
                             periodicidade="mensal")
    r = emp.dar_baixa_titulo(pool, conta, t_ago["id"])
    assert r["proximo_titulo_id"]


# ═══════════════════ conta sem valor não se paga ═══════════════════
def test_baixa_recusa_conta_sem_valor(pool, conta):
    """R$ 0,00 no livro-caixa é pior que despesa nenhuma: some do DRE sem sumir
    da lista."""
    t = emp.criar_titulo(pool, conta, "pagar", "EQUATORIAL", 0, HOJE,
                         periodicidade="mensal", valor_variavel=True)
    r = emp.dar_baixa_titulo(pool, conta, t["id"])
    assert not r["ok"] and "valor" in r["erro"].lower()
    with pool.connection() as c:
        assert c.execute("select count(*) from lancamentos where conta_id=%s",
                         (conta,)).fetchone()[0] == 0
    assert _abertos(pool, conta)[0]["status"] == "aberto"


def test_posto_o_valor_a_baixa_volta_a_funcionar(pool, conta):
    t = emp.criar_titulo(pool, conta, "pagar", "EQUATORIAL", 0, HOJE,
                         periodicidade="mensal", valor_variavel=True)
    emp.editar_titulo(pool, conta, t["id"], valor_centavos=48700)
    assert emp.dar_baixa_titulo(pool, conta, t["id"])["ok"]


def test_editar_nao_zera_o_valor_de_conta_comum(pool, conta):
    """`_reais_para_centavos` devolve 0 pra texto que não é número — digitar
    "mil reais" no campo zerava o título, e desde a 196 o zero é reservado."""
    t = emp.criar_titulo(pool, conta, "pagar", "GESTÃO CLICK", 21411, HOJE)
    emp.editar_titulo(pool, conta, t["id"], valor_centavos=0)
    assert _abertos(pool, conta)[0]["valor_centavos"] == 21411


# ═══════════════════ o que a lista devolve ═══════════════════
def test_a_lista_traz_o_ritmo_e_a_data_da_proxima(pool, conta):
    venc = HOJE + timedelta(days=5)
    emp.criar_titulo(pool, conta, "pagar", "CARTÃO DE CRÉDITO", 0, venc,
                     periodicidade="mensal", valor_variavel=True)
    linha = _abertos(pool, conta)[0]
    assert linha["periodicidade"] == "mensal"
    assert linha["valor_variavel"] is True
    assert linha["proxima"] == emp.proxima_data(venc, "mensal")


def test_conta_avulsa_nao_promete_proxima(pool, conta):
    emp.criar_titulo(pool, conta, "pagar", "COMISSÃO", 5000, HOJE)
    assert _abertos(pool, conta)[0]["proxima"] is None


def test_titulo_pago_nao_promete_proxima(pool, conta):
    """Em título pago ela já nasceu (ou foi barrada pela trava) — prometer outra
    ali seria anunciar uma segunda."""
    t = emp.criar_titulo(pool, conta, "pagar", "PISCINEIRO", 27000,
                         HOJE - timedelta(days=2), periodicidade="mensal")
    emp.dar_baixa_titulo(pool, conta, t["id"])
    pagos = emp.listar_titulos(pool, conta, status="pago")
    assert pagos[0]["proxima"] is None


def test_recorrente_antigo_sem_ritmo_le_se_mensal(pool, conta):
    """Migração 196: quem já era recorrente foi backfillado pra 'mensal', mas o
    código não pode depender disso — linha nula tem que continuar mensal."""
    with pool.connection() as c:
        c.execute("""insert into titulos (conta_id, tipo, descricao, contraparte,
                       valor_centavos, vencimento, status, recorrente, categoria)
                     values (%s,'pagar','ALUGUEL ANTIGO','',150000,%s,'aberto',
                             true,'Fornecedores')""", (conta, HOJE))
        c.commit()
    linha = _abertos(pool, conta)[0]
    assert linha["periodicidade"] == "mensal"
    assert linha["proxima"] == emp.proxima_data(HOJE, "mensal")


# ═══════════════════ a tela ═══════════════════
def _linha(**campos):
    """Renderiza o TRECHO REAL do template, com os nomes que a rota passa.

    Olhar só o texto do template não bastaria aqui: `{% for %}` sobre uma
    variável que não chegou NÃO falha no Jinja — sai vazio. Um erro no nome
    entregaria um seletor com uma opção só ("não repete"), e nenhum teste de
    string veria isso.
    """
    from jinja2 import DictLoader, Environment
    tpl = pt._EMPRESA
    i = tpl.index("{% macro tit_linha")
    j = tpl.index('{% else %}<div class="mut" style="font-size:.85rem">'
                  "Nenhum título em aberto")
    env = Environment(loader=DictLoader({"t": tpl[i:j]}))
    env.filters["brl"] = pt.brl
    env.filters["n2"] = lambda v: f"{v:.2f}"
    t = {"id": 1, "descricao": "ÁGUAS DE TERESINA", "contraparte": "ÁGUAS DE TERESINA",
         "valor_centavos": 0, "aprovacao": "aguardando", "tipo": "pagar",
         "vencimento": date(2026, 9, 21), "atrasado": False, "prazo": "em 17 dias",
         "cliente_nome": None, "cliente_id": None, "criado_nome": None,
         "aprovado_nome": None, "aprovacao_motivo": None, "sem_fornecedor": False,
         "conciliar": None, "cobranca_link_url": None,
         "periodicidade": "mensal", "valor_variavel": True,
         "proxima": date(2026, 10, 21)}
    t.update(campos)
    blocos = [{"titulo": "⏳ Esperando liberação", "cor": "esp", "decide": True,
               "itens": [t], "centavos": t["valor_centavos"], "dica": ""}]
    return env.get_template("t").render(
        tit_blocos=blocos, pode_liberar=True,
        RITMOS=[("quinzenal", "a cada 15 dias"), ("mensal", "todo mês"),
                ("anual", "todo ano")],
        RITMO_SELO={"quinzenal": "quinzenal", "mensal": "mensal", "anual": "anual"})


def test_o_seletor_lista_os_tres_ritmos_e_marca_o_atual():
    html = _linha(periodicidade="quinzenal")
    for rotulo in ("não repete", "a cada 15 dias", "todo mês", "todo ano"):
        assert rotulo in html, rotulo
    assert 'value="quinzenal" selected' in html


def test_conta_avulsa_abre_o_seletor_em_nao_repete():
    html = _linha(periodicidade=None, valor_variavel=False,
                  valor_centavos=165000, proxima=None)
    assert 'value="" selected' in html
    assert "💧 valor muda" not in html, "a caixa do valor só existe em conta que repete"
    assert "próxima:" not in html


def test_a_linha_mostra_o_selo_do_ritmo_e_a_proxima():
    html = _linha()
    assert "🔁 mensal" in html
    assert "próxima: 21/10" in html


def test_sem_valor_a_linha_pede_o_valor_em_vez_de_oferecer_baixa():
    # só a LINHA: o total do bloco continua sendo uma soma, e soma de conta sem
    # valor é zero mesmo — o que não pode é a CONTA aparecer valendo R$ 0,00.
    linha = _linha()
    linha = linha[linha.index('<div class="tit-lin">'):]
    assert "— informar" in linha and "R$ 0,00" not in linha
    assert "✎ pôr o valor" in linha and "dar baixa ✓" not in linha


def test_com_valor_a_linha_volta_a_oferecer_baixa():
    html = _linha(valor_centavos=220000, valor_variavel=False)
    assert "dar baixa ✓" in html and "pôr o valor" not in html
    assert "R$ 2.200,00" in html


def test_a_rota_passa_o_vocabulario_dos_ritmos():
    """O selo e as opções do seletor têm que dizer a MESMA coisa — escrever "a
    cada 15 dias" em dois lugares é escrever duas verdades que divergem na
    primeira correção."""
    fonte = open(pt.__file__, encoding="utf-8").read()
    i = fonte.index('return _render("empresa", request')
    corpo = fonte[i - 900:i + 400]
    assert "RITMOS=ritmos" in corpo and "RITMO_SELO=" in corpo
    for chave in emp.PERIODICIDADES:
        assert f'"{chave}"' in corpo, chave



def test_o_formulario_de_criar_tem_o_ritmo_e_o_valor_que_muda():
    """O campo existe desde a 053 e NENHUMA tela sabia ligá-lo — é por isso que
    0 de 39 títulos da Prime estavam marcados."""
    assert 'name="periodicidade"' in pt._EMPRESA
    assert 'name="valor_variavel"' in pt._EMPRESA
    for chave in ("quinzenal", "mensal", "anual"):
        assert f'value="{chave}"' in pt._EMPRESA


def test_a_tela_nao_promete_mais_recorrencia_ao_detalhar():
    """O texto embaixo do formulário dizia "contraparte/recorrência aparecem ao
    detalhar", e recorrência não aparecia em lugar nenhum."""
    assert "recorrência aparecem ao detalhar" not in pt._EMPRESA


def test_a_linha_grava_na_rota_de_recorrencia():
    assert "/painel/empresa/titulo/{{ t.id }}/recorrencia" in pt._EMPRESA


def _tabela(dados: dict) -> str:
    """A tabela do relatório pelo Jinja de verdade, como o portal roda."""
    from jinja2 import DictLoader, Environment
    corpo = pt._RELATORIOS.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    env = Environment(loader=DictLoader({"t": "<table><tbody>" + corpo + "</tbody></table>"}))
    env.filters["brl"] = pt.brl
    return env.get_template("t").render(dados=dados, request=None)


def test_o_relatorio_tambem_escreve_informar_no_lugar_de_zero(pool, conta):
    """As duas telas têm que concordar: a Empresa diz "— informar" e o relatório
    de Contas a pagar dizia "R$ 0,00" pra mesma conta."""
    emp.criar_titulo(pool, conta, "pagar", "ÁGUAS DE TERESINA", 0,
                     HOJE + timedelta(days=5), contraparte="ÁGUAS DE TERESINA",
                     periodicidade="mensal", valor_variavel=True)
    dados = rel._dados_titulos_abertos(pool, conta, "pagar")
    html = _tabela(dados)
    assert "— informar" in html and "R$ 0,00" not in html


def test_o_relatorio_continua_escrevendo_dinheiro_quando_ha_dinheiro(pool, conta):
    emp.criar_titulo(pool, conta, "pagar", "ZARB CONSULTORIA", 220000,
                     HOJE + timedelta(days=5), contraparte="ZARB ASSESSORIA")
    html = _tabela(rel._dados_titulos_abertos(pool, conta, "pagar"))
    assert "R$ 2.200,00" in html and "— informar" not in html


def test_a_coluna_de_valor_do_relatorio_declara_o_texto():
    fonte = open(rel.__file__, encoding="utf-8").read()
    trecho = fonte[fonte.index("def _dados_titulos_abertos"):]
    trecho = trecho[:trecho.index("def ", 10)]
    assert 'zero="— informar"' in trecho


def test_o_template_compila():
    from jinja2 import Environment
    Environment().parse(pt._EMPRESA)
    Environment().parse(pt._RELATORIOS)
