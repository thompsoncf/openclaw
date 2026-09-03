"""Contas a pagar / a receber avisam quando a conta PARECE já paga.

Em produção, 01/09/2026: 38 títulos abertos, 11 vencidos somando R$ 27.170,85 —
um deles há 58 dias — e ZERO baixas feitas por gente. O dinheiro sai pelo extrato
e pela foto do comprovante, que nascem em `lancamentos`; o título fica aberto pra
sempre porque ninguém clica em "pago". A coluna "Conferir" não resolve isso: ela
deixa de esconder.

**Esta é a metade perigosa do trabalho, e o teste existe pra manter ela
perigosa-mente conservadora.** Rodando o casamento por valor + janela nos 11
títulos vencidos da produção saem duas sugestões, e uma delas casaria a
"parcela 2/2 da Bianca Oliveira" (R$ 750, vencida em 31/08) com o dinheiro do
SINAL — a parcela 1. Se isso virasse baixa automática, o sistema fecharia uma
dívida que a cliente ainda tem e lançaria receita que não entrou.

O que este teste protege:

  * **nada é baixado.** A função só devolve dica; nenhum título muda de status;
  * **a janela não alcança o mês vizinho.** 15 dias é o maior número que não
    chega na ocorrência seguinte de uma conta mensal. Foi o erro que eu cometi
    lendo os 30 títulos da Prime: 20 tinham lançamento de mesmo valor e NENHUM
    estava pago — os títulos eram de setembro e os pagamentos, de agosto;
  * **lançamento que já é a baixa de um título não vira candidato de outro.** É a
    trava que tira o eco do sinal da Bianca da conta;
  * **com mais de um candidato a tela conta, não escolhe.** Dizer qual seria
    chute, e chute em tela de dinheiro é o que esta coluna existe pra evitar.
"""
import os
from datetime import date, timedelta

import pytest
from psycopg_pool import ConnectionPool

import web.painel_relatorios as rel

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
  recorrente boolean default false, categoria text, cobranca_link_url text,
  pago_em date, lancamento_id bigint, cliente_id bigint, criado_por bigint,
  aprovacao text not null default 'autorizado', aprovado_por bigint,
  aprovado_em timestamptz, aprovacao_motivo text,
  pago_sem_autorizacao boolean not null default false);   -- 195
-- `membros` entra porque `listar_titulos` passou a dizer QUEM lançou e QUEM
-- liberou o título (195). Duas colunas de nome, dois left joins.
create table membros (id bigserial primary key, conta_id bigint,
  nome text, email text);
"""

HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_relatorios_conferir_test"
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
        c.execute("truncate contas, clientes, pessoas, lancamentos, titulos restart identity")
        cid = c.execute("insert into contas (nome) values ('Prime Eventos') "
                        "returning id").fetchone()[0]
        c.commit()
    return cid


def _titulo(pool, conta_id, *, tipo="pagar", valor=220000, venc_em=-17,
            descricao="ZARB CONSULTORIA", status="aberto", lancamento_id=None):
    with pool.connection() as c:
        tid = c.execute(
            """insert into titulos (conta_id, tipo, descricao, contraparte,
                 valor_centavos, vencimento, status, categoria, lancamento_id)
               values (%s,%s,%s,'',%s,%s,%s,'Fornecedores',%s) returning id""",
            (conta_id, tipo, descricao, valor, HOJE + timedelta(days=venc_em),
             status, lancamento_id)).fetchone()[0]
        c.commit()
    return tid


def _pag(pool, conta_id, *, tipo="despesa", valor=220000, dias=-22,
         descricao="Pagamento Pix", origem="extrato"):
    with pool.connection() as c:
        lid = c.execute(
            """insert into lancamentos (conta_id, tipo, valor_centavos, categoria,
                 descricao, data, origem, natureza)
               values (%s,%s,%s,'Outros',%s,%s,%s,'empresa') returning id""",
            (conta_id, tipo, valor, descricao, HOJE + timedelta(days=dias),
             origem)).fetchone()[0]
        c.commit()
    return lid


def _abertos(pool, conta_id, tipo="pagar"):
    return rel._dados_titulos_abertos(pool, conta_id, tipo)


# ── o caso que funciona ──────────────────────────────────────────────────────
def test_o_caso_da_zarb(pool, conta):
    """Título de R$ 2.200 vencido em 15/08, Pix de R$ 2.200 em 10/08 — cinco dias
    antes. É a única sugestão boa que a produção inteira produziu."""
    _titulo(pool, conta)
    _pag(pool, conta)
    linha = _abertos(pool, conta)["linhas"][0]
    assert linha["conferir"].startswith("parece pago em ")
    assert linha["conferir_cor"] == "aviso"


def test_o_lado_de_receber_fala_recebido(pool, conta):
    _titulo(pool, conta, tipo="receber", descricao="Evento — parcela 2/2")
    _pag(pool, conta, tipo="receita")
    assert _abertos(pool, conta, "receber")["linhas"][0]["conferir"].startswith(
        "parece recebido em ")


def test_titulo_sem_pagamento_por_perto_fica_limpo(pool, conta):
    """BANCO DO NORDESTE, R$ 2.400 vencido — não há pagamento nenhum desse valor.
    A coluna tem que ficar VAZIA, não inventar dúvida."""
    _titulo(pool, conta, valor=240000, descricao="BANCO DO NORDESTE")
    assert _abertos(pool, conta)["linhas"][0]["conferir"] == ""
    assert "aviso_config" not in _abertos(pool, conta)


# ── a janela ─────────────────────────────────────────────────────────────────
def test_a_janela_nao_alcanca_o_mes_vizinho(pool, conta):
    """O erro que eu cometi na análise: os 30 títulos a pagar da Prime são de
    SETEMBRO e os lançamentos de mesmo valor são as contas recorrentes de AGOSTO.
    Casar por valor sem olhar a data marcaria 20 títulos como "já pagos" — todos
    errados. 30 dias de distância tem que passar longe."""
    _titulo(pool, conta, valor=81050, venc_em=0, descricao="2ª QUINZENA INSIGHT")
    _pag(pool, conta, valor=81050, dias=-30)
    assert _abertos(pool, conta)["linhas"][0]["conferir"] == ""


@pytest.mark.parametrize("dias,tem_dica", [
    (-14, True), (14, True), (-15, False), (15, False), (0, True),
])
def test_a_borda_da_janela(pool, conta, dias, tem_dica):
    _titulo(pool, conta, venc_em=0)
    _pag(pool, conta, dias=dias)
    assert bool(_abertos(pool, conta)["linhas"][0]["conferir"]) is tem_dica


def test_a_janela_e_de_catorze_dias(pool):
    """Fixa o número, que foi MEDIDO e não escolhido: com 15 a régua devolvia 3
    dicas na Prime e 2 eram falsas — conta quinzenal tem espaçamento de 15 dias,
    então a janela alcançava a ocorrência vizinha. Aumentar traz os falsos de
    volta."""
    assert rel._JANELA_CANDIDATO_DIAS == 14


def test_a_quinzena_vizinha_nao_vira_dica(pool, conta):
    """O caso real que derrubou a janela de 15: o título "2ª quinzena agosto/26 —
    Jaqueline" vence em 05/09 e o pagamento da 1ª quinzena caiu em 21/08 —
    exatamente 15 dias antes, mesmo valor."""
    _titulo(pool, conta, valor=85000, venc_em=0,
            descricao="2 QUINZENA AGOSTO/26 JAQUELINE")
    _pag(pool, conta, valor=85000, dias=-15, origem="foto",
         descricao="1ª quinzena agosto/26 - Jacqueline Duarte")
    assert _abertos(pool, conta)["linhas"][0]["conferir"] == ""


# ── as travas contra o falso positivo ────────────────────────────────────────
def test_pagamento_ja_amarrado_a_outro_titulo_nao_conta(pool, conta):
    """O contra-exemplo da Bianca. O sinal (parcela 1) já foi baixado e tem um
    título apontando pra ele; ele NÃO pode virar candidato da parcela 2, senão a
    tela sugere fechar uma dívida com dinheiro que já foi de outra."""
    lid = _pag(pool, conta, tipo="receita", valor=75000, dias=-4,
               descricao="Evento — Bianca · Sinal", origem="titulo")
    _titulo(pool, conta, tipo="receber", valor=75000, venc_em=-30,
            descricao="Evento — Bianca · Sinal", status="pago", lancamento_id=lid)
    _titulo(pool, conta, tipo="receber", valor=75000, venc_em=-1,
            descricao="Evento — Bianca · parcela 2/2")
    linhas = _abertos(pool, conta, "receber")["linhas"]
    assert len(linhas) == 1, "só a parcela 2 está em aberto"
    assert linhas[0]["conferir"] == "", \
        "o dinheiro do sinal não pode ser sugerido como pagamento da parcela 2"


def test_o_eco_do_sinal_tambem_nao_conta(pool, conta):
    """O contra-exemplo da Bianca, como ele REALMENTE está em produção — e o furo
    que a primeira versão desta função deixou passar.

    O sinal de R$ 750 está no banco duas vezes: a baixa do título (amarrada, que a
    trava 1 barra) e a foto do mesmo comprovante (solta, que passava). Sem a trava
    do gêmeo, a tela sugeria a foto como pagamento da parcela 2/2 — fechando com o
    dinheiro da parcela 1 uma dívida que a cliente ainda tem."""
    lid = _pag(pool, conta, tipo="receita", valor=75000, dias=-4,
               descricao="Evento — Bianca · Sinal", origem="titulo")
    _pag(pool, conta, tipo="receita", valor=75000, dias=-4,
         descricao="Sinal 50% locação espaço - Bianca Oliveira", origem="foto")
    _titulo(pool, conta, tipo="receber", valor=75000, venc_em=-30,
            descricao="Evento — Bianca · Sinal", status="pago", lancamento_id=lid)
    _titulo(pool, conta, tipo="receber", valor=75000, venc_em=-1,
            descricao="Evento — Bianca · parcela 2/2")
    linhas = _abertos(pool, conta, "receber")["linhas"]
    assert len(linhas) == 1
    assert linhas[0]["conferir"] == "", \
        "a foto do sinal é eco do mesmo dinheiro, não o pagamento da parcela 2"


def test_o_gemeo_so_e_barrado_no_mesmo_dia_e_valor(pool, conta):
    """A trava não pode virar uma peneira larga: pagamento legítimo de mesmo valor
    em OUTRO dia continua sendo candidato."""
    _pag(pool, conta, valor=220000, dias=-25, descricao="Baixa antiga", origem="titulo")
    _pag(pool, conta, valor=220000, dias=-3, descricao="Pix de verdade")
    _titulo(pool, conta, venc_em=0)
    assert _abertos(pool, conta)["linhas"][0]["conferir"].startswith("parece pago em ")


def test_varios_candidatos_a_tela_conta_e_nao_escolhe(pool, conta):
    _titulo(pool, conta, venc_em=0)
    _pag(pool, conta, dias=-3)
    _pag(pool, conta, dias=+4)
    assert _abertos(pool, conta)["linhas"][0]["conferir"] == "2 pagamentos iguais por perto"


def test_o_candidato_mais_perto_do_vencimento_e_o_mostrado(pool, conta):
    _titulo(pool, conta, venc_em=0)
    _pag(pool, conta, dias=-12)
    d = _abertos(pool, conta)
    assert rel._fmt(HOJE - timedelta(days=12)) in d["linhas"][0]["conferir"]


def test_o_lado_errado_do_caixa_nao_casa(pool, conta):
    """Uma conta a PAGAR não pode casar com uma receita de mesmo valor."""
    _titulo(pool, conta, tipo="pagar")
    _pag(pool, conta, tipo="receita")
    assert _abertos(pool, conta)["linhas"][0]["conferir"] == ""


def test_pagamento_de_outra_conta_nao_casa(pool, conta):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Outra') "
                          "returning id").fetchone()[0]
        c.commit()
    _titulo(pool, conta)
    _pag(pool, outra)
    assert _abertos(pool, conta)["linhas"][0]["conferir"] == ""


def test_valor_diferente_nao_casa(pool, conta):
    _titulo(pool, conta, valor=220000)
    _pag(pool, conta, valor=219999)
    assert _abertos(pool, conta)["linhas"][0]["conferir"] == ""


# ── nada é escrito ───────────────────────────────────────────────────────────
def test_a_dica_nao_baixa_titulo_nenhum(pool, conta):
    """A regra da casa: fechar dívida por engano é perder informação do cliente.
    Quem dá baixa é gente, no passo 4."""
    tid = _titulo(pool, conta)
    _pag(pool, conta)
    _abertos(pool, conta)
    with pool.connection() as c:
        status, pago, lanc = c.execute(
            "select status, pago_em, lancamento_id from titulos where id=%s",
            (tid,)).fetchone()
    assert (status, pago, lanc) == ("aberto", None, None)


# ── o resumo em cima ─────────────────────────────────────────────────────────
def test_a_metrica_e_o_aviso_somam_os_suspeitos(pool, conta):
    _titulo(pool, conta, valor=220000, descricao="ZARB")
    _pag(pool, conta, valor=220000)
    _titulo(pool, conta, valor=116085, descricao="IPTU 3/6", venc_em=-1)
    d = _abertos(pool, conta)
    assert ("Talvez já pago", "1 · R$ 2.200,00") in d["metricas"]
    assert "aviso_config" in d
    assert "1 conta em aberto tem um pagamento" in d["aviso_config"]
    assert "a baixa continua sendo sua" in d["aviso_config"]


def test_sem_suspeito_nao_tem_aviso(pool, conta):
    _titulo(pool, conta, valor=240000, descricao="BANCO DO NORDESTE")
    d = _abertos(pool, conta)
    assert "aviso_config" not in d
    assert ("Talvez já pago", "0 · R$ 0,00") in d["metricas"]


def test_a_aba_sem_titulo_nao_quebra(pool, conta):
    d = _abertos(pool, conta)
    assert d["linhas"] == []
    assert rel._pagamentos_candidatos(pool, conta, [], "pagar") == {}


def test_titulo_sem_vencimento_nao_derruba(pool, conta):
    with pool.connection() as c:
        c.execute("""insert into titulos (conta_id, tipo, descricao, contraparte,
                       valor_centavos, vencimento, status, categoria)
                     values (%s,'pagar','Sem data','',1000,null,'aberto','x')""",
                  (conta,))
        c.commit()
    assert _abertos(pool, conta)["linhas"][0]["conferir"] == ""


# ── a coluna na tabela ───────────────────────────────────────────────────────
def test_a_coluna_conferir_e_tag_e_a_elastica_continua_a_contraparte(pool, conta):
    cols = _abertos(pool, conta)["colunas"]
    por_chave = {c["chave"]: c for c in cols}
    assert por_chave["conferir"]["tag"] is True
    assert [c["chave"] for c in cols if c["flex"]] == ["contraparte"]


def test_as_abas_de_compromisso_nao_mudaram_de_fonte(pool, conta):
    """O passo 3 acrescenta informação; não pode ter trocado a origem dos dados
    dessas duas abas — título aberto é dívida em aberto."""
    fonte = open(rel.__file__, encoding="utf-8").read()
    corpo = fonte.split("def _dados_titulos_abertos")[1].split("\ndef ")[0]
    assert "emp.listar_titulos" in corpo and 'status="aberto"' in corpo
