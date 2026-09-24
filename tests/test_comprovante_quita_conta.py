"""O comprovante que chega pelo WhatsApp pergunta se quita a conta — e só fecha com o sim.

Entrega 3 do plano aprovado em 23/09/2026. Medido na Prime no mesmo dia: 85 dos
165 lançamentos da conta nasceram de comprovante mandado pelo dono, e 9 das 13
contas a pagar abertas tinham o pagamento igual já no caixa — a conta ficava
aberta, contando como dívida e aparecendo entre as atrasadas.

O QUE ESTE ARQUIVO FIXA:

  * **a régua é a da tela**, e os casos são os da Prime: a 1ª quinzena não quita a
    2ª, agosto não quita setembro, o Thiago não quita o Pedro — e o "Águas de
    Teresina" de R$ 86,22 quita a conta de R$ 86,22;
  * **a pergunta só vai pro dono**, com o módulo Empresa, e nunca em gasto
    pessoal nem em registro que a trava de duplicata barrou;
  * **o texto manda perguntar e esperar o sim**, e proíbe a baixa (que lançaria
    o dinheiro de novo);
  * **quitar liga, não lança**: o caixa tem o mesmo número de linhas antes e
    depois, e o centro proposto só preenche o que está vazio.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp
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
              "143_plano_contas_locacao_buffet_servicos.sql",
              "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql",
              "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql")

CONTA = 621
HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True)
    dbname = "zaq_comprovante_quita"
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
        c.execute("insert into contas (id, nome, tipo) values (%s,'Prime Eventos','pj') "
                  "on conflict (id) do nothing", (CONTA,))
        c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _limpa(pool, monkeypatch):
    with pool.connection() as c:
        c.execute("delete from titulos where conta_id=%s", (CONTA,))
        c.execute("delete from lancamentos where conta_id=%s", (CONTA,))
        c.execute("delete from centros_custo where conta_id=%s", (CONTA,))
        c.commit()
    # o módulo Empresa ligado: a pergunta só existe com ele
    monkeypatch.setattr(emp, "modulo_pj_ativo", lambda _p, _c: True)
    yield


def _conta(pool, desc, cp, valor, venc, **kw):
    return emp.criar_titulo(pool, CONTA, "pagar", desc, valor, venc, contraparte=cp, **kw)


def _comprovante(pool, desc, valor, quando=HOJE, origem="foto"):
    """Um comprovante já registrado, como o agente registra."""
    lanc = Lancamento(tipo=Tipo.DESPESA, valor_centavos=valor, categoria="Servicos",
                      descricao=desc, data=quando, origem=origem, natureza="empresa")
    return LivroCaixa(pool, CONTA).adicionar(lanc, forcar=True).id


def _ids(contas):
    return [t["id"] for t in contas]


# ═══════════════════════════════════════════ a régua, com os casos da Prime

def test_a_agua_de_86_22_quita_a_conta_de_86_22(pool):
    """O caso que chegou às 20:57 de 23/09/2026."""
    t = _conta(pool, "ÁGUAS DE TERESINA", "ÁGUAS DE TERESINA", 8622, HOJE - timedelta(days=2))
    lid = _comprovante(pool, "Águas de Teresina - conta de água", 8622)
    assert _ids(emp.contas_que_o_pagamento_quita(pool, CONTA, lid)) == [t["id"]]


def test_a_primeira_quinzena_nao_quita_a_segunda(pool):
    _conta(pool, "2 QUINZENA SETEMBRO/26 PEDRO YAN", "PEDRO YAN MENDES VALENÇA XIMENES",
           150000, HOJE + timedelta(days=7))
    lid = _comprovante(pool, "1ª quinzena setembro/2026 - Pedro Yan Mendes Valença Ximenes",
                       150000)
    assert emp.contas_que_o_pagamento_quita(pool, CONTA, lid) == []


def test_agosto_nao_quita_setembro(pool):
    _conta(pool, "1 QUINZENA SETEMBRO/26 INSIGHT", "INSIGHT ASSESSORIA  ADMINISTRATIVA",
           81050, HOJE - timedelta(days=6))
    lid = _comprovante(pool, "2ª quinzena agosto/2026 - Irisnalva Bezerra de Sousa (Insight)",
                       81050)
    assert emp.contas_que_o_pagamento_quita(pool, CONTA, lid) == []


def test_o_pix_do_thiago_nao_quita_a_conta_do_pedro(pool):
    _conta(pool, "SERVIÇO PRESTADO PEDRO YAN", "PEDRO YAN MENDES VALENÇA XIMENES",
           150000, HOJE)
    lid = _comprovante(pool, "Serviço prestado - Thiago Cesar Borges Pinheiro", 150000)
    assert emp.contas_que_o_pagamento_quita(pool, CONTA, lid) == []


def test_pagou_a_menos_nao_quita(pool):
    """Fechar aqui esconderia dívida — o pior erro deste módulo."""
    _conta(pool, "ZARB CONSULTORIA", "ZARB ASSESSORIA", 220000, HOJE)
    lid = _comprovante(pool, "ZARB Assessoria - consultoria", 200000)
    assert emp.contas_que_o_pagamento_quita(pool, CONTA, lid) == []


def test_pagou_atrasado_com_juros_quita_e_diz_o_acrescimo(pool):
    t = _conta(pool, "ZARB CONSULTORIA", "ZARB ASSESSORIA", 220000, HOJE - timedelta(days=20))
    lid = _comprovante(pool, "ZARB Assessoria - consultoria", 225867)
    c = emp.contas_que_o_pagamento_quita(pool, CONTA, lid)
    assert _ids(c) == [t["id"]] and c[0]["acrescimo_centavos"] == 5867


def test_duas_contas_iguais_voltam_as_duas_e_a_mais_perto_primeiro(pool):
    longe = _conta(pool, "SEGURANÇA", "SECURITY", 25190, HOJE - timedelta(days=10))
    perto = _conta(pool, "SEGURANÇA", "SECURITY", 25190, HOJE + timedelta(days=2))
    lid = _comprovante(pool, "Mensalidade Security", 25190)
    assert _ids(emp.contas_que_o_pagamento_quita(pool, CONTA, lid)) == [perto["id"], longe["id"]]


def test_pagamento_que_ja_quita_uma_conta_nao_quita_outra(pool):
    a = _conta(pool, "ALUGUEL", "IMOB X", 150000, HOJE)
    _conta(pool, "ALUGUEL", "IMOB X", 150000, HOJE + timedelta(days=1))
    lid = _comprovante(pool, "Pix aluguel Imob X", 150000)
    assert emp.conciliar_titulo(pool, CONTA, a["id"], lid)["ok"]
    assert emp.contas_que_o_pagamento_quita(pool, CONTA, lid) == []


def test_o_eco_de_uma_baixa_nao_e_dinheiro_novo(pool):
    """Mesmo dia e valor de um lançamento que JÁ é a baixa de uma conta: é o
    comprovante daquela baixa, não um segundo pagamento."""
    a = _conta(pool, "LUZ", "EQUATORIAL", 90000, HOJE)
    _conta(pool, "LUZ", "EQUATORIAL", 90000, HOJE + timedelta(days=1))
    emp.dar_baixa_titulo(pool, CONTA, a["id"], data_pagto=HOJE)
    lid = _comprovante(pool, "Conta de luz Equatorial", 90000)
    assert emp.contas_que_o_pagamento_quita(pool, CONTA, lid) == []


# ═══════════════════════════════════════════ a pergunta no retorno do lançamento

def _lancar(pool, papel="dono", **entrada):
    from finance.tools import construir_ferramentas
    fs = {f.nome: f for f in construir_ferramentas(LivroCaixa(pool, CONTA), papel=papel)}
    base = {"valor": 86.22, "categoria": "Contas de casa", "origem": "foto",
            "descricao": "Águas de Teresina - conta de água", "natureza": "empresa"}
    base.update(entrada)
    return fs["lancar_despesa"].executar(base)


def test_o_dono_recebe_a_pergunta_com_os_ids_e_a_regra(pool):
    t = _conta(pool, "ÁGUAS DE TERESINA", "ÁGUAS DE TERESINA", 8622, HOJE - timedelta(days=2))
    r = _lancar(pool)
    assert "CONTA EM ABERTO QUE ESTE PAGAMENTO PODE QUITAR" in r
    assert f"titulo_id={t['id']}" in r and "lancamento_id=" in r
    assert "SO' se ele confirmar" in r
    assert "NUNCA use dar_baixa_titulo" in r


def test_o_centro_da_conta_vai_na_pergunta(pool):
    from finance import plano_contas as pc
    fixa = pc.criar_centro(pool, CONTA, "DESPESA FIXA")["id"]
    _conta(pool, "ÁGUAS DE TERESINA", "ÁGUAS DE TERESINA", 8622, HOJE - timedelta(days=2),
           centro_custo_id=fixa)
    assert "centro proposto: DESPESA FIXA" in _lancar(pool)


def test_com_duas_contas_a_pergunta_manda_escolher(pool):
    _conta(pool, "ÁGUA", "ÁGUAS DE TERESINA", 8622, HOJE)
    _conta(pool, "ÁGUA", "ÁGUAS DE TERESINA", 8622, HOJE - timedelta(days=5))
    r = _lancar(pool)
    assert "Sao 2 contas" in r and "NAO escolha" in r


@pytest.mark.parametrize("como", ["membro", "pessoal", "sem_modulo", "sem_conta"])
def test_quem_nao_decide_nao_recebe_pergunta(pool, monkeypatch, como):
    if como != "sem_conta":
        _conta(pool, "ÁGUAS DE TERESINA", "ÁGUAS DE TERESINA", 8622, HOJE)
    if como == "sem_modulo":
        monkeypatch.setattr(emp, "modulo_pj_ativo", lambda _p, _c: False)
    r = _lancar(pool, papel="membro" if como == "membro" else "dono",
                natureza="pessoal" if como == "pessoal" else "empresa")
    assert "registrada" in r
    assert "PODE QUITAR" not in r


def test_registro_barrado_como_duplicata_nao_pergunta(pool):
    _conta(pool, "ÁGUAS DE TERESINA", "ÁGUAS DE TERESINA", 8622, HOJE)
    _lancar(pool)
    r = _lancar(pool)                     # o mesmo comprovante, de novo
    assert "NAO registrei" in r and "PODE QUITAR" not in r


# ═══════════════════════════════════════════ quitar: liga, não lança

def _quitar(pool, **e):
    from finance.tools_pj import construir_ferramentas_pj
    fs = {f.nome: f for f in construir_ferramentas_pj(pool, CONTA)}
    return fs["quitar_conta_com_pagamento"].executar(e)


def _n_lancamentos(pool):
    with pool.connection() as c:
        return c.execute("select count(*) from lancamentos where conta_id=%s",
                         (CONTA,)).fetchone()[0]


def test_quitar_fecha_a_conta_sem_lancar_dinheiro_novo(pool):
    t = _conta(pool, "ÁGUAS DE TERESINA", "ÁGUAS DE TERESINA", 8622, HOJE - timedelta(days=2))
    lid = _comprovante(pool, "Águas de Teresina - conta de água", 8622)
    antes = _n_lancamentos(pool)
    r = _quitar(pool, titulo_id=t["id"], lancamento_id=lid)
    assert "Conta quitada" in r and "nenhum dinheiro novo" in r
    assert _n_lancamentos(pool) == antes
    aberta = [x["id"] for x in emp.listar_titulos(pool, CONTA, status="aberto")]
    assert t["id"] not in aberta


def test_quitar_o_par_errado_diz_por_que_e_nao_fecha(pool):
    """O id trocado pelo modelo esbarra na mesma régua da tela."""
    t = _conta(pool, "2 QUINZENA SETEMBRO/26 PEDRO YAN", "PEDRO YAN", 150000, HOJE)
    lid = _comprovante(pool, "1ª quinzena setembro/2026 - Pedro Yan", 150000)
    r = _quitar(pool, titulo_id=t["id"], lancamento_id=lid)
    assert r.startswith("NÃO quitei") and "continua aberta" in r
    assert t["id"] in [x["id"] for x in emp.listar_titulos(pool, CONTA, status="aberto")]


def test_o_centro_confirmado_so_preenche_o_vazio(pool):
    from finance import plano_contas as pc
    fixa = pc.criar_centro(pool, CONTA, "DESPESA FIXA")["id"]
    evento = pc.criar_centro(pool, CONTA, "DESPESA COM EVENTO")["id"]
    t1 = _conta(pool, "ÁGUA", "ÁGUAS", 8622, HOJE)
    l1 = _comprovante(pool, "Águas - conta", 8622)
    _quitar(pool, titulo_id=t1["id"], lancamento_id=l1, centro_custo="despesa fixa")
    t2 = _conta(pool, "LUZ", "EQUATORIAL", 9000, HOJE)
    l2 = _comprovante(pool, "Equatorial - luz", 9000)
    with pool.connection() as c:
        c.execute("update lancamentos set centro_custo_id=%s where id=%s", (evento, l2))
        c.commit()
    _quitar(pool, titulo_id=t2["id"], lancamento_id=l2, centro_custo="DESPESA FIXA")
    with pool.connection() as c:
        r = dict(c.execute("select id, centro_custo_id from lancamentos where id in (%s,%s)",
                           (l1, l2)).fetchall())
    assert r == {l1: fixa, l2: evento}


def test_a_persona_da_empresa_traz_os_centros_e_a_regra(pool):
    from finance import plano_contas as pc
    from finance.tools_pj import bloco_persona_pj
    pc.criar_centro(pool, CONTA, "INVESTIMENTO")
    p = bloco_persona_pj(pool, CONTA, "Prime Eventos")
    assert "CENTROS DE CUSTO desta empresa (a ÁREA do negócio): INVESTIMENTO." in p
    # e mesmo com um centro chamado INVESTIMENTO, "foi investimento" é TIPO (325)
    assert "'foi investimento' -> tipo_despesa=investimento" in p
    assert "NUNCA ponha isso em centro_custo" in p
    assert "COMPROVANTE QUE QUITA CONTA" in p
    assert "dar_baixa_titulo o lançaria DUAS vezes" in p
