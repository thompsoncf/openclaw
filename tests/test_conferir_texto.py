"""O aviso "talvez já paga" para de acusar quem não foi pago.

Em 04/09/2026, com a aba nova no ar, o dono perguntou o que a coluna fazia. Fui
conferir o que ela estava DIZENDO na Prime e achei que ela errava em duas das
cinco linhas que avisava:

    título aberto                        candidatos que a régua via
    ─────────────────────────────────    ──────────────────────────────────────
    2 QUINZENA AGOSTO/26 JAQUELINE       824 Pedro Yan · 833 Thiago · 832 Jonas
    1 QUINZENA SETEMBRO/26 JAQUELINE     824 Pedro Yan · 833 Thiago · 832 Jonas
    1 QUINZENA SETEMBRO/26 THIAGO        824 Pedro Yan · 833 Thiago · 832 Jonas
    1 QUINZENA SETEMBRO/26 PEDRO YAN     824 Pedro Yan · 833 Thiago · 832 Jonas

Os três pagamentos de R$ 1.500,00 por perto são: 824 "Serviço prestado - Pedro
Yan ... 2ª quinzena ago/26", 833 o mesmo pro Thiago, e 832 "Acordo cliente -
Jonas Barros Castro Neto - reembolso prejuízo". **Nenhum é da Jaqueline** — as
duas contas dela, R$ 3.000,00 de dívida real, apareciam como "talvez pagas". E
mesmo pro Thiago e pro Pedro Yan o dinheiro é da 2ª quinzena de AGOSTO, não da 1ª
de SETEMBRO: casar ali fecharia setembro com o dinheiro de agosto e sumiria com a
dívida — a mesma família de erro da parcela 2/2 da Bianca.

**O contrapeso, que é o que decide o desenho: deixar de avisar é pior que avisar
à toa.** Perder um aviso custa pagar duas vezes; dinheiro que sai. Por isso a
régua nova (`emp.texto_contradiz`) recusa só por CONTRADIÇÃO, nunca por falta de
parecença. São duas, com condições de confiança diferentes:

  * **período** — os dois textos dizem um período e os períodos são outros. Vale
    venha o texto de onde vier, porque é fato, não palpite;
  * **nome** — e SÓ quando dá pra confiar no texto do pagamento. Extrato de banco
    escreve "Pagamento Pix 58.608.090 0001-88": recusar por aí mataria a única
    dica boa que a produção inteira produziu (a ZARB, que veio do extrato).

O teste guarda os dois lados dessa balança. As recusas certas, e — mais
importante — as recusas que NÃO podem acontecer.
"""
import os
from datetime import date, timedelta

import pytest
from psycopg_pool import ConnectionPool

from finance import empresa as emp
import web.painel_relatorios as rel

HOJE = date.today()

# Os textos, como estão na produção da Prime (leitura de 04/09/2026).
T_JAQ_AGO = {"descricao": "2 QUINZENA AGOSTO/26 JAQUELINE",
             "contraparte": "JAQUELINE DUARTE"}
T_JAQ_SET = {"descricao": "1 QUINZENA SETEMBRO/26 JAQUELINE",
             "contraparte": "JAQUELINE DUARTE"}
T_THIAGO = {"descricao": "1 QUINZENA SETEMBRO/26 THIAGO",
            "contraparte": "THIAGO CESAR BORGES PINHEIRO"}
T_PEDRO = {"descricao": "1 QUINZENA SETEMBRO/26 PEDRO YAN",
           "contraparte": "PEDRO YAN MENDES VALENÇA XIMENES"}
T_ZARB = {"descricao": "ZARB CONSULTORIA", "contraparte": "ZARB ASSESSORIA"}
T_BETO = {"descricao": "2 QUINZENA AGOSTO/26 BETO", "contraparte": "ROBERTO LOPES"}

L_824 = {"descricao": "Serviço prestado - Pedro Yan Mendes Valenca Ximenes - "
                      "2ª quinzena ago/26", "origem": "foto"}
L_833 = {"descricao": "Serviço prestado - Thiago Cesar Borges Pinheiro - "
                      "2ª quinzena ago/26", "origem": "foto"}
L_832 = {"descricao": "Acordo cliente - Jonas Barros Castro Neto - reembolso "
                      "prejuízo", "origem": "foto"}
L_657 = {"descricao": "Pagamento Pix 58.608.090 0001-88", "origem": "extrato"}


# ═══════════════ o caso que motivou o conserto ═══════════════
@pytest.mark.parametrize("lanc,apelido", [(L_824, "824 Pedro Yan"),
                                          (L_833, "833 Thiago"),
                                          (L_832, "832 Jonas")])
def test_a_jaqueline_para_de_ser_acusada(lanc, apelido):
    """R$ 3.000,00 de dívida real que a tela dizia estar talvez paga."""
    assert emp.texto_contradiz(T_JAQ_AGO, lanc), \
        f"{apelido} continua servindo pra conta da Jaqueline"
    assert emp.texto_contradiz(T_JAQ_SET, lanc)


def test_o_dinheiro_de_agosto_nao_fecha_a_conta_de_setembro():
    """É o caso perigoso, e o motivo de o conserto não ser só "compare o nome":
    filtrando SÓ por nome, o título "1 QUINZENA SETEMBRO/26 THIAGO" ficaria com um
    candidato único — o 833, que é do Thiago mesmo — e a tela promoveria isso a
    botão ✓. Um clique fecharia setembro com o dinheiro de agosto."""
    motivo = emp.texto_contradiz(T_THIAGO, L_833)
    assert motivo and "período" in motivo, \
        "o nome bate; quem tem que recusar aqui é o período"
    assert emp.texto_contradiz(T_PEDRO, L_824)


def test_a_zarb_continua_de_pe():
    """A única sugestão boa que a produção inteira produziu — e ela vem do
    EXTRATO, cujo texto é um CNPJ. Uma régua de nome ingênua mataria justamente
    esta."""
    assert emp.texto_contradiz(T_ZARB, L_657) is None


# ═══════════════ as recusas que NÃO podem acontecer ═══════════════
def test_o_apelido_nao_recusa_o_pagamento_certo():
    """Na Prime o título é "2 QUINZENA AGOSTO/26 BETO" e o fornecedor é "ROBERTO
    LOPES". Comparação por igualdade estrita recusaria o dinheiro do próprio
    Beto — por isso a régua é de contenção: "beto" está dentro de "roberto"."""
    pago_ao_beto = {"descricao": "Pagamento a Roberto Lopes - 2ª quinzena ago/26",
                    "origem": "foto"}
    assert emp.texto_contradiz(T_BETO, pago_ao_beto) is None


def test_a_contraparte_conta_junto_da_descricao():
    """É ela que carrega o nome de verdade quando a descrição usa apelido. Se a
    régua olhasse só a descrição, o teste acima falharia."""
    assert "roberto" in emp.assinatura_de_nome(T_BETO["descricao"],
                                               T_BETO["contraparte"])


@pytest.mark.parametrize("texto", ["Pagamento Pix 58.608.090 0001-88",
                                   "PAGAMENTO", "Transferência", "TED 12345",
                                   "", "   ", "Pagto ref 2026"])
def test_texto_que_nao_nomeia_ninguem_nunca_recusa(texto):
    """Assinatura vazia = o texto não disse quem é. Não dá pra contradizer com o
    que não foi dito, e recusar aqui seria exatamente "recusar por falta de
    parecença" — o lado caro da balança."""
    assert emp.assinatura_de_nome(texto) == set()
    assert emp.texto_contradiz(T_JAQ_AGO, {"descricao": texto, "origem": "foto"}) is None


def test_extrato_nunca_recusa_por_nome():
    """O banco escreve o que quer. Só o período — que é fato — vale num texto de
    extrato."""
    extrato_com_outro_nome = {"descricao": "Pix enviado PEDRO YAN MENDES",
                              "origem": "extrato"}
    assert emp.texto_contradiz(T_JAQ_AGO, extrato_com_outro_nome) is None
    # mas o período, esse vale de qualquer origem
    extrato_outro_mes = {"descricao": "Pix 1 quinzena setembro", "origem": "extrato"}
    assert emp.texto_contradiz(T_JAQ_AGO, extrato_outro_mes)


def test_sem_periodo_de_um_dos_lados_nao_ha_contradicao_de_periodo():
    """"ZARB CONSULTORIA" não fala de mês. Comparar com um pagamento que fala
    seria inventar uma discordância que ninguém declarou."""
    assert emp.periodo_no_texto(T_ZARB["descricao"]) is None
    assert emp.texto_contradiz(T_ZARB, {"descricao": "Pagamento ZARB agosto",
                                        "origem": "foto"}) is None


def test_o_mesmo_periodo_nao_recusa():
    assert emp.texto_contradiz(T_JAQ_AGO, {"descricao": "Jaqueline Duarte - "
                                           "2ª quinzena ago/26", "origem": "foto"}) is None


# ═══════════════ a leitura do período ═══════════════
def test_mes_so_por_NOME():
    """"IPTU 2026 3/6" tem dois números que pareceriam março e junho. Ler dígito
    como mês transformaria toda parcela "3/6" numa contradição inventada."""
    assert emp.periodo_no_texto("IPTU 2026 3/6") is None
    assert emp.periodo_no_texto("Parcela 2/12") is None


@pytest.mark.parametrize("texto,esperado", [
    ("1 QUINZENA SETEMBRO/26 THIAGO", (1, 9)),
    ("2ª quinzena ago/26", (2, 8)),
    ("2 QUINZENA AGOSTO/26 JAQUELINE", (2, 8)),
    ("quinzena 1 de março", (1, 3)),
    ("FGTS AGOSTO/26", (None, 8)),
    ("MENSALIDADE GESTÃO CLICK", None),
    ("ZARB CONSULTORIA", None),
])
def test_o_que_a_regua_le_de_periodo(texto, esperado):
    assert emp.periodo_no_texto(texto) == esperado


# ═══════════════ as duas pontas têm que concordar ═══════════════
_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table pessoas (id bigserial primary key, nome text);
create table clientes (id bigserial primary key, dono_id bigint, pessoa_id bigint,
  nome text);
create table lancamentos (
  id bigserial primary key, conta_id bigint not null, tipo text not null,
  valor_centavos bigint not null, categoria text not null,
  descricao text not null default '', data date not null,
  origem text not null default 'manual', natureza text);
create table titulos (
  id bigserial primary key, conta_id bigint, tipo text, descricao text,
  contraparte text, valor_centavos bigint, vencimento date, status text,
  recorrente boolean default false, periodicidade text, valor_variavel boolean not null default false, categoria text, cobranca_link_url text,
  pago_em date, lancamento_id bigint, cliente_id bigint, criado_por bigint,
  aprovacao text not null default 'autorizado', aprovado_por bigint,
  aprovado_em timestamptz, aprovacao_motivo text,
  pago_sem_autorizacao boolean not null default false);
create table membros (id bigserial primary key, conta_id bigint,
  nome text, email text);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True)
    dbname = "zaq_conferir_texto_test"
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
def prime(pool):
    """A Prime como ela está: os quatro títulos de R$ 1.500,00, a ZARB, e os
    quatro pagamentos que a régua enxerga."""
    with pool.connection() as c:
        c.execute("truncate contas, clientes, pessoas, lancamentos, titulos "
                  "restart identity")
        c.execute("insert into contas (nome) values ('Prime Eventos')")
        for desc, contra, venc in [
                (T_JAQ_AGO["descricao"], T_JAQ_AGO["contraparte"], -1),
                (T_JAQ_SET["descricao"], T_JAQ_SET["contraparte"], 9),
                (T_THIAGO["descricao"], T_THIAGO["contraparte"], 9),
                (T_PEDRO["descricao"], T_PEDRO["contraparte"], 9)]:
            c.execute("""insert into titulos (conta_id,tipo,descricao,contraparte,
                         valor_centavos,vencimento,status,categoria)
                         values (1,'pagar',%s,%s,150000,%s,'aberto','Fornecedores')""",
                      (desc, contra, HOJE + timedelta(days=venc)))
        c.execute("""insert into titulos (conta_id,tipo,descricao,contraparte,
                     valor_centavos,vencimento,status,categoria)
                     values (1,'pagar',%s,%s,220000,%s,'aberto','Fornecedores')""",
                  (T_ZARB["descricao"], T_ZARB["contraparte"],
                   HOJE - timedelta(days=17)))
        for lanc, cent, dias in [(L_824, 150000, -3), (L_833, 150000, -3),
                                 (L_832, 150000, -2), (L_657, 220000, -22)]:
            c.execute("""insert into lancamentos (conta_id,tipo,valor_centavos,
                         categoria,descricao,data,origem,natureza)
                         values (1,'despesa',%s,'Outros',%s,%s,%s,'empresa')""",
                      (cent, lanc["descricao"], HOJE + timedelta(days=dias),
                       lanc["origem"]))
        c.commit()
    return 1


def test_a_prime_inteira_passa_de_cinco_avisos_pra_um(pool, prime):
    """O número que o dono vê no topo da aba. Era "5 · R$ 8.200,00", com duas
    linhas simplesmente erradas; vira "1 · R$ 2.200,00", que é a ZARB — a única
    que estava certa."""
    d = rel._dados_titulos_abertos(pool, prime, "pagar")
    com_aviso = [(r["descricao"], r["talvez"]) for r in d["linhas"] if r["talvez"]]
    assert len(com_aviso) == 1, f"ainda avisa demais: {com_aviso}"
    assert com_aviso[0][0] == "ZARB CONSULTORIA"
    assert ("Talvez já pago", "1 · R$ 2.200,00") in d["metricas"]


def test_nenhuma_conta_da_jaqueline_fica_marcada(pool, prime):
    d = rel._dados_titulos_abertos(pool, prime, "pagar")
    for r in d["linhas"]:
        if "JAQUELINE" in r["descricao"]:
            assert r["talvez"] == "", \
                "R$ 1.500,00 que ela tem a receber marcados como talvez pagos"


def test_o_aviso_sobra_so_na_zarb(pool, prime):
    """Depois do conserto a dica existe uma vez só, e na linha certa.

    Era o BOTÃO que se contava aqui. Em 04/09/2026 ele saiu do relatório e foi
    pra aba Empresa, por escolha do dono — o que sobra nesta tela é o aviso, que
    é informação e não ação. A régua que decide continua a mesma, e é ela que
    este teste guarda."""
    d = rel._dados_titulos_abertos(pool, prime, "pagar")
    com_aviso = [r["descricao"] for r in d["linhas"] if r["talvez"]]
    assert com_aviso == ["ZARB CONSULTORIA"]
    assert "acao" not in d, "o relatório voltou a ter coluna de ação"


def test_a_gravacao_recusa_o_que_a_tela_deixou_de_sugerir(pool, prime):
    """As duas pontas usam a MESMA régua, e é por isso que ela mora em
    `finance/empresa.py`. Um formulário aberto antes da mudança — ou um POST
    forjado — ainda tentaria casar o dinheiro do Thiago com a conta da Jaqueline;
    a gravação tem que recusar sozinha."""
    with pool.connection() as c:
        tit = c.execute("select id from titulos where descricao=%s",
                        (T_JAQ_SET["descricao"],)).fetchone()[0]
        lan = c.execute("select id from lancamentos where descricao=%s",
                        (L_833["descricao"],)).fetchone()[0]
        antes = c.execute("select count(*) from lancamentos").fetchone()[0]
    r = emp.conciliar_titulo(pool, prime, tit, lan)
    assert r["ok"] is False
    with pool.connection() as c:
        assert c.execute("select status from titulos where id=%s",
                         (tit,)).fetchone()[0] == "aberto"
        assert c.execute("select count(*) from lancamentos").fetchone()[0] == antes, \
            "a recusa não pode lançar nada"


def test_a_gravacao_continua_aceitando_a_zarb(pool, prime):
    """A régua nova não pode ter fechado o caminho bom junto."""
    with pool.connection() as c:
        tit = c.execute("select id from titulos where descricao='ZARB CONSULTORIA'"
                        ).fetchone()[0]
        lan = c.execute("select id from lancamentos where valor_centavos=220000"
                        ).fetchone()[0]
        antes = c.execute("select count(*) from lancamentos").fetchone()[0]
    r = emp.conciliar_titulo(pool, prime, tit, lan)
    assert r["ok"] is True, r
    with pool.connection() as c:
        assert c.execute("select status from titulos where id=%s",
                         (tit,)).fetchone()[0] == "pago"
        assert c.execute("select count(*) from lancamentos").fetchone()[0] == antes, \
            "conciliar não lança dinheiro novo — é a razão de não ser dar_baixa_titulo"


def test_a_regua_do_texto_esta_no_finance_e_nao_na_tela():
    """Se ela nascer na tela, a gravação fica sem ela e um POST forjado passa —
    que é o motivo de `pagamento_serve_pro_titulo` existir separada."""
    fonte = open(emp.__file__, encoding="utf-8").read()
    corpo = fonte.split("def pagamentos_candidatos")[1].split("\ndef ")[0]
    assert "texto_contradiz" in corpo, "a busca por candidato tem que CHAMAR a régua"
    da_tela = open(rel.__file__, encoding="utf-8").read()
    assert "_SEM_IDENTIDADE" not in da_tela, "a régua não pode ter cópia na tela"
    assert "def pagamentos_candidatos" not in da_tela, \
        "a busca por candidato voltou pra tela — duas telas perguntam isso agora"
    servidor = open(emp.__file__, encoding="utf-8").read()
    trecho = servidor.split("def pagamento_serve_pro_titulo")[1].split("\ndef ")[0]
    assert "texto_contradiz" in trecho, \
        "a gravação revalida a janela mas não o texto — as pontas divergiram"
