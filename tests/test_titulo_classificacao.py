"""A conta a pagar ganha categoria, plano de contas e centro de custo (migração 317).

Pedido do dono em 23/09/2026: "no lançamento do contas a pagar já colocar o centro
de custo e plano de contas e categoria". Medido na Prime no mesmo dia: das 20
despesas de setembro nascidas de conta a pagar, 20 tinham plano (classificadas À
MÃO depois) e 1 tinha centro. A porta do extrato perguntava; a do título, não.

O QUE ESTE ARQUIVO FIXA:

  * **a baixa leva a classificação pro caixa**, e a próxima ocorrência herda;
  * **a conciliação só preenche o vazio** — o lançamento que alguém já
    classificou olhando o comprovante não é sobrescrito pelo cadastro da conta;
  * **nada de outra conta, nada inativo, nada desligado** entra — e um id que não
    passa na validação NÃO apaga o que já estava;
  * **a memória casa fornecedor + forma da descrição**: a Jaqueline tem quinzena e
    diária de evento, e a quinzena não pode herdar o plano da diária;
  * **nenhum centro de custo é criado, renomeado ou alterado** (regra do dono,
    23/09/2026) — o teste conta as linhas de `centros_custo` antes e depois.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp

_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "053_modulo_pj.sql",
              "057_natureza_lancamento.sql",
              "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql",
              "067_titulos_cliente.sql",
              "131_pessoa_cnpj.sql",
              "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql",   # 5.1.09 e 5.1.10
              "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql",
              "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql")

CONTA, OUTRA = 611, 612
HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    """Banco próprio, pelo motivo de sempre: a suíte roda em ordem aleatória, e
    teste que depende do que o vizinho deixou no banco ensina a ignorar vermelho."""
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True)
    dbname = "zaq_titulo_classificacao"
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
        c.execute("delete from titulos where conta_id in (%s,%s)", (CONTA, OUTRA))
        c.execute("delete from lancamentos where conta_id in (%s,%s)", (CONTA, OUTRA))
        c.commit()
    yield


def _centro(pool, conta, nome, ativo=True):
    with pool.connection() as c:
        cid = c.execute("insert into centros_custo (conta_id, nome, ativo) "
                        "values (%s,%s,%s) returning id", (conta, nome, ativo)).fetchone()[0]
        c.commit()
    return cid


def _plano(pool, codigo):
    with pool.connection() as c:
        return c.execute("select id from plano_contas where codigo=%s", (codigo,)).fetchone()[0]


def _lanc(pool, lanc_id):
    with pool.connection() as c:
        r = c.execute("select plano_conta_id, centro_custo_id, categoria "
                      "from lancamentos where id=%s", (lanc_id,)).fetchone()
    return {"plano": r[0], "centro": r[1], "categoria": r[2]}


def _tit(pool, tid):
    with pool.connection() as c:
        r = c.execute("select plano_conta_id, centro_custo_id, categoria "
                      "from titulos where id=%s", (tid,)).fetchone()
    return {"plano": r[0], "centro": r[1], "categoria": r[2]}


def _novo(pool, conta=CONTA, desc="ALUGUEL", cp="IMOBILIARIA X", **kw):
    return emp.criar_titulo(pool, conta, "pagar", desc, 150000,
                            kw.pop("venc", HOJE + timedelta(days=5)),
                            contraparte=cp, **kw)


# ═══════════════════════════════════════════ a forma da descrição (sem banco)

@pytest.mark.parametrize("a,b", [
    ("1 QUINZENA SETEMBRO/26 JAQUELINE",
     "2 QUINZENA AGOSTO/26 JAQUELINE (Pago em espécie por Dr. Manoel)"),   # anotação
    ("2ª quinzena agosto/2026 - Iasmim", "1 QUINZENA SETEMBRO/26 IASMIM"),
    ("IPTU 2026 4/6", "IPTU 2026 3/6"),
])
def test_a_mesma_conta_em_ocorrencias_diferentes_tem_a_mesma_forma(a, b):
    assert emp._mesma_forma(emp.forma_da_descricao(a), emp.forma_da_descricao(b))


@pytest.mark.parametrize("a,b", [
    ("1 QUINZENA SETEMBRO/26 JAQUELINE", "DIÁRIA SUPORTE EVENTO"),         # o caso real
    ("COMISSÃO SOBRE VENDAS", "DIÁRIA SUPORTE EVENTO"),
    ("COMISSÃO SOBRE VENDAS", "2 QUINZENA AGOSTO/26 JAQUELINE"),
])
def test_contas_diferentes_do_mesmo_fornecedor_nao_se_confundem(a, b):
    assert not emp._mesma_forma(emp.forma_da_descricao(a), emp.forma_da_descricao(b))


def test_descricao_so_de_numeros_nao_casa_com_nada():
    """Uma forma vazia "caberia" em qualquer outra — e sugeriria o plano de
    qualquer conta do fornecedor. Vazio não casa."""
    assert emp.forma_da_descricao("15/09/2026") == frozenset()
    assert not emp._mesma_forma(frozenset(), emp.forma_da_descricao("ALUGUEL"))


def test_as_categorias_comecam_pelo_padrao_de_cada_tipo():
    """A primeira opção é o que o título sempre recebeu sem ninguém escolher —
    trocá-la mudaria a categoria de quem só não mexeu no campo."""
    assert emp.categorias_titulo("pagar")[0] == emp.CAT_FORNECEDORES
    assert emp.categorias_titulo("receber")[0] == emp.CAT_VENDAS
    assert "Mercado" not in emp.categorias_titulo("receber")
    assert "Salario" not in emp.categorias_titulo("pagar")


def test_categoria_antiga_fora_do_vocabulario_continua_na_lista():
    lista = emp.categorias_titulo("pagar", atual="Categoria de 2025")
    assert lista[-1] == "Categoria de 2025" and lista.count("Categoria de 2025") == 1


# ═══════════════════════════════════════════ criar

def test_criar_grava_plano_e_centro(pool):
    fixa = _centro(pool, CONTA, "DESPESA FIXA")
    pid = _plano(pool, "5.1.10")
    t = _novo(pool, plano_conta_id=pid, centro_custo_id=str(fixa))
    assert _tit(pool, t["id"]) == {"plano": pid, "centro": fixa, "categoria": "Fornecedores"}


def test_criar_sem_classificacao_continua_como_sempre(pool):
    t = _novo(pool)
    assert _tit(pool, t["id"]) == {"plano": None, "centro": None, "categoria": "Fornecedores"}


def test_centro_de_outra_conta_ou_inativo_nao_entra(pool):
    da_vizinha = _centro(pool, OUTRA, "DA VIZINHA")
    desligado = _centro(pool, CONTA, "ANTIGO", ativo=False)
    t1 = _novo(pool, centro_custo_id=da_vizinha)
    t2 = _novo(pool, desc="LUZ", centro_custo_id=desligado)
    assert _tit(pool, t1["id"])["centro"] is None
    assert _tit(pool, t2["id"])["centro"] is None


def test_plano_desligado_pra_conta_nao_entra(pool):
    from finance import plano_contas as pc
    pid = _plano(pool, "5.1.03")
    pc.habilitar(pool, CONTA, pid, False)
    try:
        t = _novo(pool, plano_conta_id=pid)
        assert _tit(pool, t["id"])["plano"] is None
    finally:
        pc.habilitar(pool, CONTA, pid, True)


# ═══════════════════════════════════════════ a baixa leva pro caixa

def test_a_baixa_leva_plano_e_centro_pro_lancamento(pool):
    fixa = _centro(pool, CONTA, "DESPESA FIXA")
    pid = _plano(pool, "5.1.10")
    t = _novo(pool, plano_conta_id=pid, centro_custo_id=fixa)
    r = emp.dar_baixa_titulo(pool, CONTA, t["id"])
    assert r["ok"]
    assert _lanc(pool, r["lancamento_id"])["plano"] == pid
    assert _lanc(pool, r["lancamento_id"])["centro"] == fixa


def test_a_proxima_ocorrencia_herda_a_classificacao(pool):
    """O aluguel classificado em janeiro não volta sem classe em fevereiro."""
    fixa = _centro(pool, CONTA, "DESPESA FIXA")
    pid = _plano(pool, "5.1.01")
    t = _novo(pool, plano_conta_id=pid, centro_custo_id=fixa, periodicidade="mensal")
    r = emp.dar_baixa_titulo(pool, CONTA, t["id"])
    assert r["proximo_titulo_id"]
    assert _tit(pool, r["proximo_titulo_id"])["plano"] == pid
    assert _tit(pool, r["proximo_titulo_id"])["centro"] == fixa


def test_o_juros_da_baixa_nao_herda_o_plano_da_conta(pool):
    """O acréscimo tem plano próprio (6.1.01 Juros e Multas, migração 197) — se
    herdasse o da conta, o juros voltaria a ser custo de fornecedor no DRE."""
    pid = _plano(pool, "5.1.10")
    t = _novo(pool, plano_conta_id=pid, venc=HOJE - timedelta(days=10))
    r = emp.dar_baixa_titulo(pool, CONTA, t["id"], acrescimo_centavos=3000)
    assert _lanc(pool, r["lancamento_acrescimo_id"])["plano"] == _plano(pool, "6.1.01")


# ═══════════════════════════════════════════ a conciliação só preenche o vazio

def _pagamento(pool, plano=None, centro=None, quando=None):
    with pool.connection() as c:
        lid = c.execute(
            """insert into lancamentos (conta_id, tipo, valor_centavos, categoria,
                   descricao, data, origem, natureza, plano_conta_id, centro_custo_id)
               values (%s,'despesa',150000,'Servicos','Pix aluguel',%s,'foto','empresa',%s,%s)
               returning id""",
            (CONTA, quando or HOJE, plano, centro)).fetchone()[0]
        c.commit()
    return lid


def test_conciliar_preenche_o_que_o_pagamento_nao_tem(pool):
    fixa = _centro(pool, CONTA, "DESPESA FIXA")
    pid = _plano(pool, "5.1.10")
    t = _novo(pool, desc="ALUGUEL IMOBILIARIA X", plano_conta_id=pid,
              centro_custo_id=fixa, venc=HOJE)
    lid = _pagamento(pool)
    assert emp.conciliar_titulo(pool, CONTA, t["id"], lid)["ok"]
    assert _lanc(pool, lid) == {"plano": pid, "centro": fixa, "categoria": "Servicos"}


def test_conciliar_nao_sobrescreve_o_que_alguem_ja_classificou(pool):
    fixa = _centro(pool, CONTA, "DESPESA FIXA")
    evento = _centro(pool, CONTA, "DESPESA COM EVENTO")
    t = _novo(pool, desc="ALUGUEL IMOBILIARIA X", plano_conta_id=_plano(pool, "5.1.10"),
              centro_custo_id=fixa, venc=HOJE)
    ja = _plano(pool, "5.1.01")
    lid = _pagamento(pool, plano=ja, centro=evento)
    assert emp.conciliar_titulo(pool, CONTA, t["id"], lid)["ok"]
    assert _lanc(pool, lid)["plano"] == ja and _lanc(pool, lid)["centro"] == evento


def test_conciliar_com_meio_classificado_completa_sem_trocar_a_outra_metade(pool):
    """O caso REAL da Prime: o comprovante chegou com plano (o agente escolhe) e
    sem centro (ninguém escolhe). O centro vem do título; o plano fica o do
    lançamento, mesmo que o título diga outro.

    (O teste acima não pegaria uma troca aqui: com os dois campos preenchidos o
    update nem roda. Foi a mutação que mostrou o buraco.)"""
    fixa = _centro(pool, CONTA, "DESPESA FIXA")
    t = _novo(pool, desc="ALUGUEL IMOBILIARIA X", plano_conta_id=_plano(pool, "5.1.10"),
              centro_custo_id=fixa, venc=HOJE)
    do_agente = _plano(pool, "5.1.01")
    lid = _pagamento(pool, plano=do_agente)
    assert emp.conciliar_titulo(pool, CONTA, t["id"], lid)["ok"]
    assert _lanc(pool, lid)["plano"] == do_agente
    assert _lanc(pool, lid)["centro"] == fixa


# ═══════════════════════════════════════════ editar

def test_editar_poe_troca_e_limpa(pool):
    fixa = _centro(pool, CONTA, "DESPESA FIXA")
    evento = _centro(pool, CONTA, "DESPESA COM EVENTO")
    t = _novo(pool)
    emp.editar_titulo(pool, CONTA, t["id"], centro_custo_id=str(fixa),
                      plano_conta_id=str(_plano(pool, "5.1.10")), categoria="Servicos")
    assert _tit(pool, t["id"]) == {"plano": _plano(pool, "5.1.10"), "centro": fixa,
                                   "categoria": "Servicos"}
    emp.editar_titulo(pool, CONTA, t["id"], centro_custo_id=str(evento))
    assert _tit(pool, t["id"])["centro"] == evento
    emp.editar_titulo(pool, CONTA, t["id"], centro_custo_id="", plano_conta_id="")
    assert _tit(pool, t["id"])["centro"] is None and _tit(pool, t["id"])["plano"] is None


def test_editar_sem_os_campos_nao_mexe_na_classificacao(pool):
    """O form antigo (ou o que só corrige o valor) não pode apagar o centro."""
    fixa = _centro(pool, CONTA, "DESPESA FIXA")
    t = _novo(pool, centro_custo_id=fixa)
    emp.editar_titulo(pool, CONTA, t["id"], valor_centavos=99900)
    assert _tit(pool, t["id"])["centro"] == fixa


def test_id_invalido_nao_serve_pra_apagar(pool):
    """Centro de outra conta, inativo ou inexistente no formulário: IGNORADO. Se
    virasse "vazio", um id adulterado apagaria a classificação."""
    fixa = _centro(pool, CONTA, "DESPESA FIXA")
    da_vizinha = _centro(pool, OUTRA, "DA VIZINHA")
    t = _novo(pool, centro_custo_id=fixa)
    for ruim in (str(da_vizinha), "999999", "abc"):
        emp.editar_titulo(pool, CONTA, t["id"], centro_custo_id=ruim)
        assert _tit(pool, t["id"])["centro"] == fixa, ruim


def test_categoria_fora_do_vocabulario_e_ignorada(pool):
    t = _novo(pool)
    emp.editar_titulo(pool, CONTA, t["id"], categoria="Salario")    # é de receita
    assert _tit(pool, t["id"])["categoria"] == "Fornecedores"


def test_nenhum_centro_de_custo_e_criado_nem_alterado(pool):
    """Regra do dono em 23/09/2026: "não mexer em centro de custos"."""
    fixa = _centro(pool, CONTA, "DESPESA FIXA")
    with pool.connection() as c:
        antes = c.execute("select id, nome, ativo, ordem from centros_custo "
                          "order by id").fetchall()
    t = _novo(pool, centro_custo_id=fixa, periodicidade="mensal")
    emp.editar_titulo(pool, CONTA, t["id"], centro_custo_id="")
    emp.editar_titulo(pool, CONTA, t["id"], centro_custo_id=str(fixa))
    emp.dar_baixa_titulo(pool, CONTA, t["id"])
    emp.memoria_do_fornecedor(pool, CONTA, "IMOBILIARIA X", "ALUGUEL")
    with pool.connection() as c:
        depois = c.execute("select id, nome, ativo, ordem from centros_custo "
                           "order by id").fetchall()
    assert antes == depois


# ═══════════════════════════════════════════ a memória do fornecedor

def _pago(pool, desc, cp, plano=None, centro=None, no_lancamento=False, dias=10,
          conta=CONTA):
    """Uma conta já paga. `no_lancamento`: a classe está SÓ no lançamento, como em
    toda conta da Prime até a 317 (o dono classificava depois, no Financeiro)."""
    kw = {} if no_lancamento else {"plano_conta_id": plano, "centro_custo_id": centro}
    t = emp.criar_titulo(pool, conta, "pagar", desc, 150000, HOJE - timedelta(days=dias),
                         contraparte=cp, **kw)
    r = emp.dar_baixa_titulo(pool, conta, t["id"], data_pagto=HOJE - timedelta(days=dias))
    if no_lancamento:
        with pool.connection() as c:
            c.execute("update lancamentos set plano_conta_id=%s, centro_custo_id=%s "
                      "where id=%s", (plano, centro, r["lancamento_id"]))
            c.commit()
    return t


def test_a_quinzena_da_jaqueline_nao_herda_o_plano_da_diaria(pool):
    """O caso medido na Prime: mesmo fornecedor, duas contas diferentes."""
    quinzena, diaria = _plano(pool, "3.1.02"), _plano(pool, "5.1.09")
    _pago(pool, "2 QUINZENA AGOSTO/26 JAQUELINE (Pago em espécie por Dr. Manoel)",
          "JAQUELINE DUARTE", plano=quinzena, no_lancamento=True, dias=30)
    _pago(pool, "DIÁRIA SUPORTE EVENTO", "JAQUELINE DUARTE", plano=diaria,
          no_lancamento=True, dias=5)                   # a MAIS RECENTE é a diária
    m = emp.memoria_do_fornecedor(pool, CONTA, "JAQUELINE DUARTE",
                                  "1 QUINZENA SETEMBRO/26 JAQUELINE")
    assert m and m["plano_conta_id"] == quinzena, m


def test_conta_nova_do_fornecedor_nao_ganha_sugestao(pool):
    _pago(pool, "DIÁRIA SUPORTE EVENTO", "JAQUELINE DUARTE",
          plano=_plano(pool, "5.1.09"), no_lancamento=True)
    assert emp.memoria_do_fornecedor(pool, CONTA, "JAQUELINE DUARTE",
                                     "COMISSÃO SOBRE VENDAS") is None


def test_a_memoria_prefere_a_classe_do_titulo_a_do_lancamento(pool):
    fixa, evento = _centro(pool, CONTA, "DESPESA FIXA"), _centro(pool, CONTA, "EVENTO")
    t = _pago(pool, "ZARB CONSULTORIA", "ZARB ASSESSORIA", centro=fixa)
    with pool.connection() as c:      # alguém mexeu no lançamento depois
        c.execute("update lancamentos set centro_custo_id=%s where id="
                  "(select lancamento_id from titulos where id=%s)", (evento, t["id"]))
        c.commit()
    m = emp.memoria_do_fornecedor(pool, CONTA, "zarb assessoria ", "ZARB CONSULTORIA")
    assert m["centro_custo_id"] == fixa


def test_a_memoria_nao_atravessa_conta(pool):
    _pago(pool, "ALUGUEL", "IMOBILIARIA X", plano=_plano(pool, "5.1.01"),
          conta=OUTRA)
    assert emp.memoria_do_fornecedor(pool, CONTA, "IMOBILIARIA X", "ALUGUEL") is None


def test_sem_fornecedor_nao_ha_memoria(pool):
    assert emp.memoria_do_fornecedor(pool, CONTA, "  ", "ALUGUEL") is None


# ═══════════════════════════════════════════ a linha mostra

def test_a_lista_traz_codigo_e_nome_pra_linha(pool):
    fixa = _centro(pool, CONTA, "DESPESA FIXA")
    _novo(pool, plano_conta_id=_plano(pool, "5.1.10"), centro_custo_id=fixa)
    t = emp.listar_titulos(pool, CONTA, tipo="pagar")[0]
    assert (t["plano_codigo"], t["centro_nome"]) == ("5.1.10", "DESPESA FIXA")
    assert t["plano_nome"]
