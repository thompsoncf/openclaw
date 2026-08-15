"""Salário com VIGÊNCIA (migração 150): cada valor vale a partir de uma data, e a
folha de cada mês usa o que valia NAQUELA competência.

O teste que justifica a feature inteira é o `test_holerite_antigo_nao_muda...`:
antes disso, dar um aumento sobrescrevia `funcionarios.salario_centavos`, e como o
holerite é montado a partir da folha (holerite_funcionario -> folha_do_mes),
reimprimir o recibo de um mês passado saía com o salário NOVO — um valor que a
pessoa não recebeu, num documento que ela guarda, sem aviso nenhum. Era por isso
que a tela simplesmente não deixava editar salário.
"""
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp

_MIGRACOES = ("053_modulo_pj.sql", "038_endereco_conta.sql",
              "058_dados_empresa.sql", "059_contato_empresa.sql",
              "089_funcionario_vale_transporte.sql",
              "092_funcionario_cbo.sql", "093_folha_beneficios_e_org.sql",
              "094_funcionario_demissao.sql", "095_funcionario_cpf.sql",
              "150_funcionario_salario_vigencia.sql")

# obter_dados_empresa (que o holerite chama) faz join com `nichos`. Mesmo stub
# mínimo de tests/test_holerite.py — o DDL é idêntico ao da migração 031, então
# `if not exists` de qualquer um dos dois é no-op no banco compartilhado.
_NICHOS_MIN = """
create table if not exists nichos (
    id        bigserial primary key,
    nome      text not null,
    slug      text not null unique,
    tipo      text not null default 'produto'
              check (tipo in ('produto','servico')),
    ativo     boolean not null default true,
    criado_em timestamptz not null default now()
);
alter table contas add column if not exists nicho_id bigint references nichos(id);
alter table contas add column if not exists cnae text;
alter table contas add column if not exists bairro text;
"""


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    with p.connection() as c:
        c.execute(_NICHOS_MIN)
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj','Empresa Vigência') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def _salario_na_folha(pool, conta_id, func_id, ano, mes):
    folha = emp.folha_do_mes(pool, conta_id, ano, mes)
    item = next((i for i in folha["itens"] if i["id"] == func_id), None)
    return item and item["salario_centavos"]


# ── o coração: aumento vale só daqui pra frente ────────────────────────────
def test_aumento_vale_do_mes_da_vigencia_em_diante(pool, conta_id):
    f = emp.criar_funcionario(pool, conta_id, "Maria", salario_centavos=250000,
                              admitido_em=date(2024, 3, 11))
    emp.definir_salario(pool, conta_id, f["id"], 275000, date(2026, 9, 1))

    assert _salario_na_folha(pool, conta_id, f["id"], 2026, 7) == 250000
    assert _salario_na_folha(pool, conta_id, f["id"], 2026, 8) == 250000
    assert _salario_na_folha(pool, conta_id, f["id"], 2026, 9) == 275000
    assert _salario_na_folha(pool, conta_id, f["id"], 2026, 10) == 275000


def test_holerite_antigo_nao_muda_depois_do_aumento(pool, conta_id):
    """A razão de existir da migração 150. O holerite de julho, reimpresso DEPOIS
    do aumento de setembro, tem que continuar mostrando o salário de julho."""
    f = emp.criar_funcionario(pool, conta_id, "Joana", salario_centavos=200000,
                              admitido_em=date(2024, 1, 5))
    antes = emp.holerite_funcionario(pool, conta_id, f["id"], 2026, 7)
    emp.definir_salario(pool, conta_id, f["id"], 300000, date(2026, 9, 1))
    depois = emp.holerite_funcionario(pool, conta_id, f["id"], 2026, 7)

    assert antes["liquido_centavos"] == depois["liquido_centavos"]
    assert any(v[3] == 200000 for v in depois["proventos"]), depois["proventos"]
    # e o de setembro reflete o novo
    set_ = emp.holerite_funcionario(pool, conta_id, f["id"], 2026, 9)
    assert any(v[3] == 300000 for v in set_["proventos"]), set_["proventos"]


def test_varios_aumentos_cada_mes_pega_o_seu(pool, conta_id):
    f = emp.criar_funcionario(pool, conta_id, "Ana", salario_centavos=100000,
                              admitido_em=date(2025, 1, 1))
    emp.definir_salario(pool, conta_id, f["id"], 120000, date(2025, 6, 1))
    emp.definir_salario(pool, conta_id, f["id"], 150000, date(2026, 1, 1))

    assert _salario_na_folha(pool, conta_id, f["id"], 2025, 3) == 100000
    assert _salario_na_folha(pool, conta_id, f["id"], 2025, 7) == 120000
    assert _salario_na_folha(pool, conta_id, f["id"], 2026, 5) == 150000


def test_regravar_a_mesma_data_substitui_em_vez_de_duplicar(pool, conta_id):
    f = emp.criar_funcionario(pool, conta_id, "Bia", salario_centavos=100000,
                              admitido_em=date(2025, 1, 1))
    emp.definir_salario(pool, conta_id, f["id"], 130000, date(2026, 3, 1))
    emp.definir_salario(pool, conta_id, f["id"], 140000, date(2026, 3, 1))

    hist = emp.historico_salarios(pool, conta_id, f["id"])
    assert len([h for h in hist if h["vigencia_de"] == date(2026, 3, 1)]) == 1
    assert _salario_na_folha(pool, conta_id, f["id"], 2026, 4) == 140000


# ── corrigir erro de digitação ≠ aumento ───────────────────────────────────
def test_corrigir_reescreve_a_vigencia_atual_sem_criar_linha(pool, conta_id):
    """Corrigir um valor digitado errado NÃO é um aumento — não pode virar linha
    nova no histórico, senão a linha do tempo conta uma história que não houve."""
    f = emp.criar_funcionario(pool, conta_id, "Carla", salario_centavos=250000,
                              admitido_em=date(2025, 2, 1))
    emp.definir_salario(pool, conta_id, f["id"], 300000, date(2026, 5, 1))
    antes = len(emp.historico_salarios(pool, conta_id, f["id"]))

    emp.corrigir_salario_atual(pool, conta_id, f["id"], 310000)

    hist = emp.historico_salarios(pool, conta_id, f["id"])
    assert len(hist) == antes, "corrigir não pode criar vigência nova"
    assert hist[0]["salario_centavos"] == 310000
    assert hist[0]["vigencia_de"] == date(2026, 5, 1)
    # e o mês ANTERIOR à vigência corrigida continua no valor antigo
    assert _salario_na_folha(pool, conta_id, f["id"], 2026, 4) == 250000
    assert _salario_na_folha(pool, conta_id, f["id"], 2026, 6) == 310000


def test_criar_funcionario_ja_nasce_com_vigencia(pool, conta_id):
    """Sem a vigência inicial, o primeiro aumento criaria a ÚNICA linha da linha do
    tempo — e a folha de um mês anterior a ele cairia na reserva
    (funcionarios.salario_centavos, já atualizado), reimprimindo holerite antigo
    com o salário de hoje."""
    f = emp.criar_funcionario(pool, conta_id, "Duda", salario_centavos=180000,
                              admitido_em=date(2025, 4, 10))
    hist = emp.historico_salarios(pool, conta_id, f["id"])
    assert len(hist) == 1
    assert hist[0]["salario_centavos"] == 180000
    assert hist[0]["vigencia_de"] == date(2025, 4, 10)


# ── redes de segurança ─────────────────────────────────────────────────────
def test_sem_vigencia_nenhuma_cai_no_campo_antigo(pool, conta_id):
    """Cinto de segurança: se o backfill da 150 não tiver pegado alguém, ele
    aparece com o salário corrente — não com zero, que seria bem pior."""
    f = emp.criar_funcionario(pool, conta_id, "Elis", salario_centavos=220000,
                              admitido_em=date(2025, 1, 1))
    with pool.connection() as c:
        c.execute("delete from funcionario_salarios where funcionario_id=%s", (f["id"],))
        c.commit()
    assert _salario_na_folha(pool, conta_id, f["id"], 2026, 8) == 220000


def test_backfill_preserva_quem_ja_existia(pool, conta_id):
    """Simula o funcionário anterior à migração: linha na tabela sem nenhuma
    vigência. Rodar o backfill da 150 de novo tem que criar a linha inicial."""
    with pool.connection() as c:
        fid = c.execute(
            """insert into funcionarios (conta_id, nome, salario_centavos, admitido_em)
               values (%s,'Antigo',195000,%s) returning id""",
            (conta_id, date(2023, 8, 1))).fetchone()[0]
        c.commit()
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with pool.connection() as c:
        c.execute((base / "150_funcionario_salario_vigencia.sql").read_text(encoding="utf-8"))
        c.commit()

    hist = emp.historico_salarios(pool, conta_id, fid)
    assert hist and hist[0]["salario_centavos"] == 195000
    assert hist[0]["vigencia_de"] == date(2023, 8, 1)
    assert _salario_na_folha(pool, conta_id, fid, 2024, 5) == 195000


def test_vigencia_de_uma_conta_nao_vaza_pra_outra(pool):
    """A resolução por competência faz UMA consulta pra conta inteira — então o
    isolamento por conta_id tem que estar nela, não só no laço."""
    with pool.connection() as c:
        a = c.execute("insert into contas (tipo,nome) values ('pj','A') returning id").fetchone()[0]
        b = c.execute("insert into contas (tipo,nome) values ('pj','B') returning id").fetchone()[0]
        c.commit()
    fa = emp.criar_funcionario(pool, a, "DaA", salario_centavos=100000,
                               admitido_em=date(2025, 1, 1))
    emp.criar_funcionario(pool, b, "DaB", salario_centavos=900000,
                          admitido_em=date(2025, 1, 1))
    emp.definir_salario(pool, a, fa["id"], 110000, date(2026, 2, 1))

    folha_b = emp.folha_do_mes(pool, b, 2026, 8)
    assert [i["nome"] for i in folha_b["itens"]] == ["DaB"]
    assert folha_b["itens"][0]["salario_centavos"] == 900000
    assert _salario_na_folha(pool, a, fa["id"], 2026, 8) == 110000
