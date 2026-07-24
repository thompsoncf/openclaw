"""Holerite (recibo de pagamento) — monta o documento a partir da folha do mês.

Valida que holerite_funcionario espelha EXATAMENTE a folha gerencial:
  • vencimentos (salário base + adicionais) × descontos (VT, INSS, adiantamento);
  • totais e líquido batem com folha_do_mes;
  • rodapé: base de INSS/FGTS, FGTS do mês (8%), base e faixa de IRRF.

Cenário-espelho do holerite real usado de referência (RSAL / MONITOR DE CFTV):
  salário 1.881,00 + adicional 87,21 ; VT 6% ; adiantamento 168,60.
As funções puras (FGTS 8%, IRRF por faixa) também são testadas.
"""
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp

# endereço (038), bairro (049), folha/funcionarios (053), cidade/uf/fantasia
# (058), contato (059), vale-transporte (089), cbo (092). O join com `nichos` e
# as colunas nicho_id/cnae (que obter_dados_empresa lê) vêm da migração 031, que
# arrasta dependências pesadas — aqui basta o DDL mínimo (ver _garantir_nichos).
# 018/057/088: colunas de lancamentos (chave, natureza, forma_pagamento) que o
# evento "vale" grava no caixa. 038/058/059/089/092: dados da empresa + folha.
_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "038_endereco_conta.sql",
              "053_modulo_pj.sql", "057_natureza_lancamento.sql",
              "058_dados_empresa.sql", "059_contato_empresa.sql",
              "088_forma_pagamento_parcelas.sql",
              "089_funcionario_vale_transporte.sql", "092_funcionario_cbo.sql",
              "093_folha_beneficios_e_org.sql")

# Colunas que obter_dados_empresa lê e que, nas migrações reais, vêm junto de
# DDL de tabelas do marketplace (carrinhos/nichos). A tabela `nichos` é criada
# IDÊNTICA à da migração 031 — assim o banco de teste compartilhado entre
# módulos não conflita (o `create ... if not exists` de qualquer lado é no-op).
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


# ── parte pura: FGTS e IRRF ────────────────────────────────────────────────
def test_fgts_8_por_cento():
    assert emp.fgts_mes_centavos(196821) == 15746   # 8% de 1.968,21 (arred.)
    assert emp.fgts_mes_centavos(0) == 0


def test_irrf_isento_ate_faixa1():
    r = emp.irrf_info(180000)      # 1.800,00 → isento
    assert r["isento"] is True
    assert r["imposto_centavos"] == 0
    assert r["aliquota"] == 0.0


def test_irrf_faixa_com_imposto():
    r = emp.irrf_info(300000)      # 3.000,00 → 15% − 381,44
    assert r["aliquota"] == 0.15
    assert r["isento"] is False
    assert r["imposto_centavos"] == int(round(300000 * 0.15)) - 38144


# ── parte com banco: monta o holerite ──────────────────────────────────────
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
        c.execute(_NICHOS_MIN)      # nichos + nicho_id + cnae (mínimo)
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def empresa_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            """insert into contas (tipo, nome, razao_social, documento,
                                    endereco, bairro, cidade, uf)
               values ('pj','RSAL Óculos','RSAL COMERCIO DE OCULOS LTDA',
                       '56048627000177','Avenida Visconde de Souza Franco, 776',
                       'Umarizal','Belém','PA') returning id"""
        ).fetchone()[0]
        c.commit()
    return cid


def test_holerite_espelha_a_folha(pool, empresa_id):
    # funcionário com CBO + VT ligado, salário 1.881,00
    f = emp.criar_funcionario(pool, empresa_id, "LUCIARA DO SOCORRO LISBOA SARAIVA",
                              cargo="MONITOR DE CFTV", salario_centavos=188100,
                              vale_transporte=True, admitido_em=date(2025, 7, 21),
                              cbo="372115")
    fid = f["id"]
    hoje = date.today()
    # adicional (diferença de salário) e adiantamento (vale)
    emp.registrar_evento_folha(pool, empresa_id, fid, "extra", 8721,
                               competencia=date(hoje.year, hoje.month, 1))
    emp.registrar_evento_folha(pool, empresa_id, fid, "vale", 16860,
                               competencia=date(hoje.year, hoje.month, 1))

    h = emp.holerite_funcionario(pool, empresa_id, fid, hoje.year, hoje.month)
    assert h is not None

    # cabeçalho da empresa
    assert h["empresa"]["razao_social"] == "RSAL COMERCIO DE OCULOS LTDA"
    assert h["empresa"]["cidade"] == "Belém" and h["empresa"]["uf"] == "PA"

    # funcionário
    assert h["func"]["codigo"] == f"{fid:05d}"
    assert h["func"]["cargo"] == "MONITOR DE CFTV"
    assert h["func"]["cbo"] == "372115"
    assert h["func"]["admitido_em"] == date(2025, 7, 21)

    inss = emp.inss_desconto_centavos(188100 + 8721)
    vt = emp.vale_transporte_desconto_centavos(188100, True)   # 11286

    # vencimentos: salário base + adicional
    assert h["vencimentos"][0] == ("00001", "SALÁRIO BASE", "30/30", 188100)
    assert ("00050", "VENCIMENTOS/ADICIONAIS", "", 8721) in h["vencimentos"]
    # descontos: VT (6%), INSS e adiantamento
    desc_valores = {d[1]: d[3] for d in h["descontos"]}
    assert desc_valores["DESCONTO DE VALE TRANSPORTE"] == vt
    assert desc_valores["DESCONTO INSS"] == inss
    assert desc_valores["DESCONTO ADIANTAMENTO SALARIAL"] == 16860

    # totais espelham a folha
    assert h["total_vencimentos_centavos"] == 188100 + 8721      # 196821
    assert h["total_descontos_centavos"] == vt + inss + 16860
    assert h["liquido_centavos"] == 196821 - (vt + inss + 16860)

    # o líquido do holerite == o líquido da folha_do_mes (mesmo número pago)
    folha = emp.folha_do_mes(pool, empresa_id, hoje.year, hoje.month)
    item = next(i for i in folha["itens"] if i["id"] == fid)
    assert h["liquido_centavos"] == item["liquido_centavos"]

    # rodapé
    assert h["base_inss_centavos"] == 196821
    assert h["base_fgts_centavos"] == 196821
    assert h["fgts_centavos"] == emp.fgts_mes_centavos(196821)   # 15746
    assert h["base_irrf_centavos"] == 196821 - inss
    assert h["irrf_isento"] is True                              # base ~1.815 → isento


def test_holerite_pro_labore_sem_fgts_nem_inss(pool, empresa_id):
    f = emp.criar_funcionario(pool, empresa_id, "Sócia Ana", salario_centavos=500000,
                              pro_labore=True)
    hoje = date.today()
    h = emp.holerite_funcionario(pool, empresa_id, f["id"], hoje.year, hoje.month)
    assert h["fgts_centavos"] == 0                 # pró-labore não gera FGTS aqui
    # pró-labore não desconta INSS CLT → não há linha de INSS
    assert all(d[1] != "DESCONTO INSS" for d in h["descontos"])
    assert h["liquido_centavos"] == 500000


def test_beneficio_nao_desconta_do_liquido(pool, empresa_id):
    # benefício (VR/VA) é custo do empregador — entra no custo, NÃO no líquido
    f = emp.criar_funcionario(pool, empresa_id, "Carla", salario_centavos=200000)
    hoje = date.today()
    comp = date(hoje.year, hoje.month, 1)
    emp.registrar_evento_folha(pool, empresa_id, f["id"], "beneficio", 30000,
                               competencia=comp)
    folha = emp.folha_do_mes(pool, empresa_id, hoje.year, hoje.month)
    item = next(i for i in folha["itens"] if i["id"] == f["id"])
    inss = emp.inss_desconto_centavos(200000)   # Carla é CLT: INSS incide
    liquido_sem_benef = 200000 - inss
    assert item["beneficios_centavos"] == 30000
    # o benefício NÃO mexe no líquido (só o INSS desconta aqui)
    assert item["liquido_centavos"] == liquido_sem_benef
    # mas entra no custo do empregador (total da folha = custo real + benefício)
    assert folha["custo_real_total_centavos"] == emp.custo_real_centavos(200000, False) + 30000

    h = emp.holerite_funcionario(pool, empresa_id, f["id"], hoje.year, hoje.month)
    assert h["beneficios_centavos"] == 30000
    assert h["liquido_centavos"] == liquido_sem_benef
    # e não vira linha de desconto
    assert all("BENEF" not in d[1].upper() for d in h["descontos"])


def test_adiantamento_desconta_beneficio_nao(pool, empresa_id):
    # o mesmo funcionário com adiantamento (desconta) E benefício (não desconta)
    f = emp.criar_funcionario(pool, empresa_id, "Diego", salario_centavos=300000)
    hoje = date.today()
    comp = date(hoje.year, hoje.month, 1)
    emp.registrar_evento_folha(pool, empresa_id, f["id"], "vale", 50000, competencia=comp)
    emp.registrar_evento_folha(pool, empresa_id, f["id"], "beneficio", 20000, competencia=comp)
    folha = emp.folha_do_mes(pool, empresa_id, hoje.year, hoje.month)
    item = next(i for i in folha["itens"] if i["id"] == f["id"])
    inss = emp.inss_desconto_centavos(300000)
    # adiantamento (50k) desconta; benefício (20k) não
    assert item["liquido_centavos"] == 300000 - 50000 - inss
    assert item["beneficios_centavos"] == 20000


def test_org_aparece_no_holerite(pool, empresa_id):
    f = emp.criar_funcionario(pool, empresa_id, "Elaine", salario_centavos=200000)
    # depto/setor/seção editados depois (fluxo do painel)
    emp.atualizar_funcionario(pool, empresa_id, f["id"], departamento="ADMIN",
                              setor="RH", secao="002")
    hoje = date.today()
    h = emp.holerite_funcionario(pool, empresa_id, f["id"], hoje.year, hoje.month)
    assert h["func"]["departamento"] == "ADMIN"
    assert h["func"]["setor"] == "RH"
    assert h["func"]["secao"] == "002"


def test_departamento_default_geral_no_holerite(pool, empresa_id):
    # sem departamento definido, o holerite mostra GERAL
    f = emp.criar_funcionario(pool, empresa_id, "Fábio", salario_centavos=200000)
    hoje = date.today()
    h = emp.holerite_funcionario(pool, empresa_id, f["id"], hoje.year, hoje.month)
    assert h["func"]["departamento"] == "GERAL"


def test_holerite_funcionario_de_outra_conta_eh_none(pool, empresa_id):
    f = emp.criar_funcionario(pool, empresa_id, "Beltrano", salario_centavos=200000)
    hoje = date.today()
    # conta diferente não enxerga o funcionário
    assert emp.holerite_funcionario(pool, empresa_id + 99999, f["id"],
                                    hoje.year, hoje.month) is None
