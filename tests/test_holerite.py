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
              "093_folha_beneficios_e_org.sql", "094_funcionario_demissao.sql",
              "095_funcionario_cpf.sql",
              "150_funcionario_salario_vigencia.sql")

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
-- apagar_lancamento (usado ao remover um evento) limpa precos_observados (mig 017);
-- aqui basta um stub com item_id pra o DELETE rodar (não apaga nada).
create table if not exists precos_observados (id bigserial primary key, item_id bigint);
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

    # funcionário (código no padrão 6 dígitos)
    assert h["func"]["codigo"] == f"{fid:06d}"
    assert h["func"]["cargo"] == "MONITOR DE CFTV"
    assert h["func"]["cbo"] == "372115"
    assert h["func"]["admitido_em"] == date(2025, 7, 21)

    inss = emp.inss_desconto_centavos(188100 + 8721)
    vt = emp.vale_transporte_desconto_centavos(188100, True)   # 11286

    # proventos: Salário-Base + adicional (padrão do escritório)
    assert h["proventos"][0] == ("011", "Salário-Base", "30 dia(s)", 188100)
    assert ("012", "Adicionais/Vantagens", "", 8721) in h["proventos"]
    # descontos: INSS (310), VT (320, 6%) e adiantamento (961)
    desc_valores = {d[1]: d[3] for d in h["descontos"]}
    assert desc_valores["Vale-Transporte"] == vt
    assert desc_valores["INSS"] == inss
    assert desc_valores["Adiantamento Salarial"] == 16860
    # os códigos padrão aparecem
    cods = {d[0] for d in h["descontos"]}
    assert {"310", "320", "961"} <= cods

    # totais espelham a folha
    assert h["total_proventos_centavos"] == 188100 + 8721      # 196821
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
    # provento vira Pró-labore (962), não Salário-Base
    assert h["proventos"][0] == ("962", "Pró-labore", "30 dia(s)", 500000)
    # pró-labore não desconta INSS CLT → não há linha de INSS
    assert all(d[1] != "INSS" for d in h["descontos"])
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
    assert h["func"]["lotacao"] == "ADMIN"     # depto vira "Lotação" no recibo
    assert h["func"]["setor"] == "RH"
    assert h["func"]["secao"] == "002"


def test_lotacao_default_geral_no_holerite(pool, empresa_id):
    # sem departamento definido, o holerite mostra GERAL na Lotação
    f = emp.criar_funcionario(pool, empresa_id, "Fábio", salario_centavos=200000)
    hoje = date.today()
    h = emp.holerite_funcionario(pool, empresa_id, f["id"], hoje.year, hoje.month)
    assert h["func"]["lotacao"] == "GERAL"


def test_holerite_mostra_logo_da_marca(pool, empresa_id):
    """A logo da empresa entra no cabeçalho do recibo quando existe; sem logo, some."""
    from web import portal
    f = emp.criar_funcionario(pool, empresa_id, "Hilda", salario_centavos=200000)
    hoje = date.today()
    h = emp.holerite_funcionario(pool, empresa_id, f["id"], hoje.year, hoje.month)

    def render(logo):
        return portal._env.get_template("holerite").render(
            h=h, cnpj_fmt="56.048.627/0001-77", empresa_nome="RSAL",
            marca={"logo_url": logo, "nome": "RSAL", "cor": "#2f7d32", "iniciais": "R"})

    assert 'src="https://x/logo.jpg"' in render("https://x/logo.jpg")
    assert "<img" not in render(None)


def test_cpf_aparece_formatado_no_holerite(pool, empresa_id):
    f = emp.criar_funcionario(pool, empresa_id, "Gilda", salario_centavos=200000,
                              cpf="03592657313")
    hoje = date.today()
    h = emp.holerite_funcionario(pool, empresa_id, f["id"], hoje.year, hoje.month)
    assert h["func"]["cpf"] == "035.926.573-13"       # formatado
    assert h["competencia_extenso"].endswith(str(hoje.year))


def test_folha_mostra_fgts_por_funcionario_e_total(pool, empresa_id):
    # FGTS (8% da remuneração) aparece na folha por funcionário e no total —
    # é o depósito do empregador, não desconta do líquido.
    f = emp.criar_funcionario(pool, empresa_id, "Gina", salario_centavos=200000)
    hoje = date.today()
    folha = emp.folha_do_mes(pool, empresa_id, hoje.year, hoje.month)
    item = next(i for i in folha["itens"] if i["id"] == f["id"])
    assert item["fgts_centavos"] == emp.fgts_mes_centavos(200000)   # 8%
    assert item["liquido_centavos"] == 200000 - emp.inss_desconto_centavos(200000)
    assert folha["total_fgts_centavos"] >= item["fgts_centavos"]


def test_prolabore_nao_gera_fgts_na_folha(pool, empresa_id):
    f = emp.criar_funcionario(pool, empresa_id, "Sócio Léo", salario_centavos=500000,
                              pro_labore=True)
    hoje = date.today()
    item = next(i for i in emp.folha_do_mes(pool, empresa_id, hoje.year, hoje.month)["itens"]
                if i["id"] == f["id"])
    assert item["fgts_centavos"] == 0


def test_remover_evento_reverte_o_caixa(pool, empresa_id):
    # adiantamento vira despesa no caixa; remover deve apagar essa despesa
    from finance.livro_caixa import LivroCaixa
    f = emp.criar_funcionario(pool, empresa_id, "Gil", salario_centavos=300000)
    hoje = date.today()
    comp = date(hoje.year, hoje.month, 1)
    emp.registrar_evento_folha(pool, empresa_id, f["id"], "vale", 50000, competencia=comp)

    evs = emp.eventos_folha_do_mes(pool, empresa_id, hoje.year, hoje.month)[f["id"]]
    assert len(evs) == 1 and evs[0]["tipo"] == "vale" and evs[0]["valor_centavos"] == 50000
    ev_id = evs[0]["id"]

    saldo_antes = LivroCaixa(pool, empresa_id).saldo_centavos()
    assert emp.remover_evento_folha(pool, empresa_id, ev_id) is True
    # o adiantamento sumiu da folha
    assert emp.eventos_folha_do_mes(pool, empresa_id, hoje.year, hoje.month).get(f["id"], []) == []
    # e a despesa de -500 foi revertida (saldo sobe 500)
    assert LivroCaixa(pool, empresa_id).saldo_centavos() == saldo_antes + 50000
    # remover de novo: já não existe
    assert emp.remover_evento_folha(pool, empresa_id, ev_id) is False


def test_remover_evento_so_da_propria_conta(pool, empresa_id):
    f = emp.criar_funcionario(pool, empresa_id, "Hugo", salario_centavos=200000)
    hoje = date.today()
    emp.registrar_evento_folha(pool, empresa_id, f["id"], "beneficio", 20000,
                               competencia=date(hoje.year, hoje.month, 1))
    ev_id = emp.eventos_folha_do_mes(pool, empresa_id, hoje.year, hoje.month)[f["id"]][0]["id"]
    # outra conta não remove
    assert emp.remover_evento_folha(pool, empresa_id + 99999, ev_id) is False
    # segue existindo
    assert len(emp.eventos_folha_do_mes(pool, empresa_id, hoje.year, hoje.month)[f["id"]]) == 1


def test_demissao_tira_da_folha_no_mes_seguinte(pool, empresa_id):
    from datetime import date as _d
    f = emp.criar_funcionario(pool, empresa_id, "Ivo", salario_centavos=200000)
    # demitido em 30/06/2026
    assert emp.atualizar_funcionario(pool, empresa_id, f["id"],
                                     demitido_em=_d(2026, 6, 30)) is True
    # no mês da demissão (junho) ele AINDA aparece
    fol_jun = emp.folha_do_mes(pool, empresa_id, 2026, 6)
    assert any(i["id"] == f["id"] for i in fol_jun["itens"])
    # no mês seguinte (julho) ele SAI da folha
    fol_jul = emp.folha_do_mes(pool, empresa_id, 2026, 7)
    assert all(i["id"] != f["id"] for i in fol_jul["itens"])
    # e o holerite de julho não é gerado pra ele
    assert emp.holerite_funcionario(pool, empresa_id, f["id"], 2026, 7) is None
    # mas o de junho (mês da demissão) ainda sai
    assert emp.holerite_funcionario(pool, empresa_id, f["id"], 2026, 6) is not None


def test_editar_admissao_e_limpar_demissao(pool, empresa_id):
    from datetime import date as _d
    f = emp.criar_funcionario(pool, empresa_id, "Jonas", salario_centavos=200000)
    emp.atualizar_funcionario(pool, empresa_id, f["id"],
                              admitido_em=_d(2024, 1, 15), demitido_em=_d(2026, 5, 1))
    achado = next(x for x in emp.listar_funcionarios(pool, empresa_id) if x["id"] == f["id"])
    assert achado["admitido_em"] == _d(2024, 1, 15)
    assert achado["demitido_em"] == _d(2026, 5, 1)
    # limpar a demissão (None) — funcionário volta pra folha
    emp.atualizar_funcionario(pool, empresa_id, f["id"], demitido_em=None)
    achado = next(x for x in emp.listar_funcionarios(pool, empresa_id) if x["id"] == f["id"])
    assert achado["demitido_em"] is None


def test_holerite_funcionario_de_outra_conta_eh_none(pool, empresa_id):
    f = emp.criar_funcionario(pool, empresa_id, "Beltrano", salario_centavos=200000)
    hoje = date.today()
    # conta diferente não enxerga o funcionário
    assert emp.holerite_funcionario(pool, empresa_id + 99999, f["id"],
                                    hoje.year, hoje.month) is None
