"""A folha em DOIS dias: adiantamento e saldo como contas a pagar (02/10/2026).

O pedido do dono: "colocar por exemplo 5 e 20 como saldo salário e adiantamento
salário". As decisões dele no mesmo dia, que este arquivo trava:

1. o adiantamento é escolhido POR FUNCIONÁRIO — percentual do salário ou valor
   fixo, num dia do mês;
2. o saldo cai no 5º DIA ÚTIL do mês seguinte (o sábado conta; domingo e feriado
   nacional, não).

E o que não pode acontecer de jeito nenhum: salário pago duas vezes. Por isso os
testes de banco olham o CAIXA (quantos lançamentos) e a FOLHA (o `a_pagar`) a
cada passo, não só as contas a pagar.

Duas partes: a regra pura (calendário, valores, `plano`) roda sem banco; o resto
usa um banco PRÓPRIO, com o relógio de Brasília parado em 02/10/2026 — a regra
depende de "hoje" (conta vencida não nasce), e o teste não pode depender do dia
em que o CI roda.
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from finance import folha_titulos as ft

HOJE_FIXO = date(2026, 10, 2)          # sexta-feira
OUT = date(2026, 10, 1)                # a competência de outubro


# ═══════════════════════════════════════════════════════════ a regra pura
@pytest.mark.parametrize("ano,mes,esperado", [
    (2026, 10, date(2026, 10, 6)),   # qui 1, sex 2, SÁB 3, (dom), seg 5, ter 6
    (2026, 11, date(2026, 11, 7)),   # dom 1 e Finados 2 não contam; o 5º é um sábado
    (2026, 12, date(2026, 12, 5)),
    (2027, 1, date(2027, 1, 7)),     # 1º de janeiro não conta
    (2027, 4, date(2027, 4, 6)),     # qui 1, sex 2, sáb 3, (dom), seg 5, ter 6
    (2026, 4, date(2026, 4, 7)),     # Sexta da Paixão (03/04/2026) não conta
])
def test_o_quinto_dia_util(ano, mes, esperado):
    assert ft.quinto_dia_util(ano, mes) == esperado


def test_o_sabado_conta_e_o_domingo_nao():
    assert ft.dia_util(date(2026, 10, 3))          # sábado
    assert not ft.dia_util(date(2026, 10, 4))      # domingo
    assert not ft.dia_util(date(2026, 10, 12))     # Aparecida
    assert not ft.dia_util(date(2026, 11, 20))     # Consciência Negra (desde 2024)


def test_a_pascoa_anda_e_a_sexta_santa_anda_junto():
    assert ft.pascoa(2026) == date(2026, 4, 5)
    assert ft.pascoa(2027) == date(2027, 3, 28)
    assert date(2027, 3, 26) in ft.feriados_nacionais(2027)
    assert date(2023, 11, 20) not in ft.feriados_nacionais(2023)


def _cfg(**kw):
    base = {"adiantamento_dia": 20, "adiantamento_pct": Decimal("40"),
            "adiantamento_centavos": None, "saldo_regra": "quinto_util",
            "dia_pagamento": 5}
    base.update(kw)
    return base


def test_os_vencimentos_de_uma_competencia():
    assert ft.vencimento_adiantamento(_cfg(), OUT) == date(2026, 10, 20)
    assert ft.vencimento_saldo(_cfg(), OUT) == date(2026, 11, 7)
    assert ft.vencimento_saldo(_cfg(saldo_regra="dia"), OUT) == date(2026, 11, 5)
    assert ft.vencimento_saldo(_cfg(saldo_regra="dia"), date(2026, 12, 1)) == date(2027, 1, 5)
    assert ft.vencimento_adiantamento(_cfg(adiantamento_dia=None), OUT) is None


def test_o_adiantamento_e_percentual_ou_valor_fixo():
    assert ft.valor_adiantamento(_cfg(), 300000) == 120000
    assert ft.valor_adiantamento(_cfg(adiantamento_pct=Decimal("33.33")), 100001) == 33330
    assert ft.valor_adiantamento(_cfg(adiantamento_centavos=50000), 300000) == 50000
    assert ft.valor_adiantamento(_cfg(adiantamento_dia=None), 300000) == 0


def test_a_regra_em_texto():
    assert ft.regra_em_texto(_cfg()) == {"adiantamento": "todo dia 20 · 40% do salário",
                                         "saldo": "5º dia útil do mês seguinte"}
    assert ft.regra_em_texto(_cfg(adiantamento_pct=Decimal("40.50")))["adiantamento"] \
        == "todo dia 20 · 40,5% do salário"
    assert ft.regra_em_texto(_cfg(adiantamento_dia=None, saldo_regra="dia")) == {
        "adiantamento": "", "saldo": "dia 5 do mês seguinte"}
    assert ft.regra_em_texto(_cfg(adiantamento_centavos=125050))["adiantamento"] \
        == "todo dia 20 · R$ 1.250,50 fixo"


def _plano(restante, tits=None, *, cfg=None, elegivel=True, recalcular=(),
           hoje=HOJE_FIXO, salario=300000):
    return ft.plano(restante, salario, cfg or _cfg(), OUT, tits or {},
                    elegivel=elegivel, recalcular=recalcular, hoje=hoje)


def _vivo(tid, valor, calc=None, venc=None, status="aberto"):
    return {"vivo": {"id": tid, "status": status, "valor": valor,
                     "calculado": valor if calc is None else calc,
                     "vencimento": venc, "aprovacao": "aguardando"},
            "cancelado": False}


def test_plano_cria_as_duas_partes():
    assert _plano(250000) == [("criar", "adiantamento", 120000, date(2026, 10, 20)),
                              ("criar", "saldo", 130000, date(2026, 11, 7))]


def test_plano_nao_cria_parte_que_ja_venceu():
    """Ligou no dia 25: o adiantamento do dia 20 provavelmente já foi pago por
    fora. Nasce só o saldo — com o líquido inteiro que a folha diz que falta."""
    assert _plano(250000, hoje=date(2026, 10, 25)) == [
        ("criar", "saldo", 250000, date(2026, 11, 7))]


def test_plano_nao_paga_mais_do_que_a_folha_deve():
    assert _plano(100000) == [("criar", "adiantamento", 100000, date(2026, 10, 20))]


def test_plano_quem_nao_gera_mais_so_passa_pela_trava():
    """Desligou as contas: nada nasce, nada sobe — mas a folha quitada ainda
    cancela o que ficou aberto (senão o salário sairia duas vezes)."""
    tits = {"saldo": _vivo(7, 130000, venc=date(2026, 11, 7))}
    assert _plano(250000, tits, elegivel=False) == []
    assert _plano(0, tits, elegivel=False) == [("cancelar", 7)]


def test_plano_respeita_o_valor_mudado_a_mao():
    """O adiantamento menor deste mês: o dono mudou 1.200 pra 500 na conta."""
    tits = {"adiantamento": _vivo(1, 50000, calc=120000, venc=date(2026, 10, 20)),
            "saldo": _vivo(2, 130000, venc=date(2026, 11, 7))}
    # o saldo sobe pra cobrir o que o adiantamento deixou de adiantar
    assert _plano(250000, tits) == [("atualizar", 2, 200000, 200000, None)]
    # mudar a configuração passa por cima da mão dele (e o saldo, que já era o
    # da conta cheia, fica como está)
    assert _plano(250000, tits, recalcular=True) == [("atualizar", 1, 120000, 120000, None)]


def test_plano_cancelada_nao_volta_so_com_a_configuracao_nova():
    tits = {"adiantamento": {"vivo": None, "cancelado": True}}
    assert _plano(250000, tits) == [("criar", "saldo", 250000, date(2026, 11, 7))]
    assert _plano(250000, tits, recalcular=True)[0] == (
        "criar", "adiantamento", 120000, date(2026, 10, 20))


def test_plano_recalcula_so_a_parte_que_mudou():
    """Trocou só a regra do saldo: o adiantamento cancelado continua cancelado, e
    o adiantamento mexido à mão continua como ele deixou."""
    cancelado = {"adiantamento": {"vivo": None, "cancelado": True}}
    assert _plano(250000, cancelado, recalcular={"saldo"}) == [
        ("criar", "saldo", 250000, date(2026, 11, 7))]
    mexido = {"adiantamento": _vivo(1, 50000, calc=120000, venc=date(2026, 10, 20)),
              "saldo": _vivo(2, 200000, venc=date(2026, 11, 7))}
    assert _plano(250000, mexido, recalcular={"saldo"}) == []
    assert _plano(250000, mexido, recalcular={"adiantamento"}) == [
        ("atualizar", 1, 120000, 120000, None), ("atualizar", 2, 130000, 130000, None)]


def test_plano_a_data_segue_a_regra():
    """Mudou o dia do pagamento: a conta aberta anda junto (nenhuma tela muda o
    vencimento de um título — não há mão do dono a respeitar ali)."""
    tits = {"saldo": _vivo(2, 250000, venc=date(2026, 11, 5))}
    assert _plano(250000, tits, cfg=_cfg(adiantamento_dia=None)) == [
        ("atualizar", 2, 250000, 250000, date(2026, 11, 7))]


def test_plano_tirou_o_adiantamento_da_configuracao():
    tits = {"adiantamento": _vivo(1, 120000, venc=date(2026, 10, 20)),
            "saldo": _vivo(2, 130000, venc=date(2026, 11, 7))}
    assert _plano(250000, tits, cfg=_cfg(adiantamento_dia=None), recalcular=True) == [
        ("cancelar", 1), ("atualizar", 2, 250000, 250000, None)]


def test_plano_e_idempotente():
    tits = {"adiantamento": _vivo(1, 120000, venc=date(2026, 10, 20)),
            "saldo": _vivo(2, 130000, venc=date(2026, 11, 7))}
    assert _plano(250000, tits) == []


def test_plano_adiantamento_pago_sai_da_conta_do_saldo():
    """Pago, o adiantamento já está no `restante` (é um 'vale' da folha)."""
    tits = {"adiantamento": _vivo(1, 120000, venc=date(2026, 10, 20), status="pago"),
            "saldo": _vivo(2, 130000, venc=date(2026, 11, 7))}
    assert _plano(130000, tits) == []


# ═══════════════════════════════════════════════════════════════ o banco
_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "038_endereco_conta.sql",
              "053_modulo_pj.sql", "057_natureza_lancamento.sql",
              "058_dados_empresa.sql", "059_contato_empresa.sql",
              "064_clientes_lojista.sql", "066_pessoas_identidade.sql",
              "067_titulos_cliente.sql", "089_funcionario_vale_transporte.sql",
              "092_funcionario_cbo.sql",
              "093_folha_beneficios_e_org.sql", "094_funcionario_demissao.sql",
              "095_funcionario_cpf.sql", "131_pessoa_cnpj.sql",
              "132_plano_contas_centros_custo.sql", "150_funcionario_salario_vigencia.sql",
              "195_titulo_aprovacao.sql", "196_titulo_recorrencia.sql",
              "197_titulo_acrescimo.sql", "317_titulo_classificacao.sql",
              "325_tipo_despesa.sql", "482_folha_adiantamento_e_saldo.sql",
              "484_titulo_vencimento_e_referencia.sql")

CONTA = 701

#: o mínimo que o holerite (nicho e endereço da empresa) e o "✕ remover" (a
#: faxina de `precos_observados` ao apagar um lançamento) pedem — igual ao
#: tests/test_holerite.py
_ESQUEMA_MIN = """
create table if not exists nichos (
    id bigserial primary key, nome text not null, slug text not null unique,
    tipo text not null default 'produto', ativo boolean not null default true,
    criado_em timestamptz not null default now());
alter table contas add column if not exists nicho_id bigint references nichos(id);
alter table contas add column if not exists cnae text;
alter table contas add column if not exists bairro text;
create table if not exists precos_observados (id bigserial primary key, item_id bigint);
"""


@pytest.fixture(scope="module")
def pool():
    from psycopg_pool import ConnectionPool

    from db.conexao import init_schema
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True)
    dbname = "zaq_folha_dois_dias_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
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
    _eq.garantir_tabela(p)      # membros.email (listar_titulos lê)
    with p.connection() as c:
        c.execute(_ESQUEMA_MIN)
        c.execute("insert into contas (id, nome, tipo) values (%s,'Folha Teste','pj') "
                  "on conflict (id) do nothing", (CONTA,))
        c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _relogio_parado(monkeypatch):
    """Brasília parada em 02/10/2026: a regra não deixa nascer conta vencida."""
    from finance import relogio
    monkeypatch.setattr(relogio, "hoje", lambda: HOJE_FIXO)


@pytest.fixture()
def limpo(pool):
    with pool.connection() as c:
        for t in ("folha_eventos", "titulos", "lancamentos", "funcionario_salarios",
                  "funcionarios"):
            c.execute(f"delete from {t} where conta_id=%s", (CONTA,))
        c.commit()
    yield


def _func(pool, nome="Ana Teste", salario=300000, **cfg):
    from finance import empresa as emp
    fid = emp.criar_funcionario(pool, CONTA, nome, salario_centavos=salario,
                                admitido_em=date(2026, 1, 1))["id"]
    if cfg:
        r = ft.configurar(pool, CONTA, fid, **cfg)
        assert r["ok"], r
    return fid


_ADI_SALDO = {"adiantamento_dia": 20, "adiantamento_pct": "40",
              "saldo_regra": "quinto_util", "gerar_titulos": True}


def _titulos(pool, fid=None, status=None):
    sql = """select id, folha_parte, status, valor_centavos, vencimento, aprovacao,
                    folha_competencia, descricao, contraparte, categoria
               from titulos where conta_id=%s and folha_parte is not null"""
    args = [CONTA]
    if fid:
        sql += " and folha_funcionario_id=%s"
        args.append(fid)
    if status:
        sql += " and status=%s"
        args.append(status)
    with pool.connection() as c:
        rows = c.execute(sql + " order by id", args).fetchall()
    return [dict(zip(("id", "parte", "status", "valor", "venc", "aprovacao", "comp",
                      "descricao", "contraparte", "categoria"), r)) for r in rows]


def _abertos(pool, fid=None) -> dict:
    return {t["parte"]: t for t in _titulos(pool, fid, "aberto")}


def _a_pagar(pool, fid, comp=OUT) -> int:
    from finance import empresa as emp
    folha = emp.folha_do_mes(pool, CONTA, comp.year, comp.month)
    return next(i["a_pagar_centavos"] for i in folha["itens"] if i["id"] == fid)


def _n_lanc(pool) -> int:
    with pool.connection() as c:
        return c.execute("select count(*) from lancamentos where conta_id=%s",
                         (CONTA,)).fetchone()[0]


def test_ligar_cria_as_duas_contas_aguardando(pool, limpo):
    fid = _func(pool, **_ADI_SALDO)
    ab = _abertos(pool, fid)
    assert set(ab) == {"adiantamento", "saldo"}
    assert ab["adiantamento"]["valor"] == 120000
    assert ab["adiantamento"]["venc"] == date(2026, 10, 20)
    assert ab["saldo"]["valor"] == _a_pagar(pool, fid) - 120000
    assert ab["saldo"]["venc"] == date(2026, 11, 7)
    for t in ab.values():
        assert t["aprovacao"] == "aguardando", "toda conta a pagar nasce aguardando"
        assert t["comp"] == OUT and t["contraparte"] == "Ana Teste"
        assert t["categoria"] == "Pessoal"
    assert ab["adiantamento"]["descricao"] == "Adiantamento salarial 10/2026"
    assert ab["saldo"]["descricao"] == "Salário 10/2026 (saldo)"
    # o mês passado NÃO ganha conta: ligar hoje começa hoje
    assert all(t["comp"] == OUT for t in _titulos(pool, fid))


def test_sincronizar_de_novo_nao_muda_nada(pool, limpo):
    fid = _func(pool, **_ADI_SALDO)
    antes = _titulos(pool, fid)
    assert ft.sincronizar(pool, CONTA) == {"criados": 0, "atualizados": 0, "cancelados": 0}
    assert ft.sincronizar_todas(pool)["criados"] == 0
    assert _titulos(pool, fid) == antes


def test_o_saldo_acompanha_o_extra_e_o_aumento(pool, limpo):
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    emp.registrar_evento_folha(pool, CONTA, fid, "extra", 20000, competencia=OUT)
    ab = _abertos(pool, fid)
    assert ab["saldo"]["valor"] == _a_pagar(pool, fid) - 120000
    # aumento a partir de outubro: o adiantamento é 40% do salário NOVO
    emp.definir_salario(pool, CONTA, fid, 400000, date(2026, 10, 1))
    ab = _abertos(pool, fid)
    assert ab["adiantamento"]["valor"] == 160000
    assert ab["saldo"]["valor"] == _a_pagar(pool, fid) - 160000


def test_baixa_do_adiantamento_entra_na_folha_sem_dobrar_o_caixa(pool, limpo):
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    devido = _a_pagar(pool, fid)
    ab = _abertos(pool, fid)
    r = emp.dar_baixa_titulo(pool, CONTA, ab["adiantamento"]["id"],
                             data_pagto=date(2026, 10, 20))
    assert r["ok"]
    assert _n_lanc(pool) == 1, "UM lançamento: o da baixa (a folha não lança de novo)"
    with pool.connection() as c:
        ev = c.execute("""select tipo, valor_centavos, competencia, lancamento_id
                            from folha_eventos where conta_id=%s""", (CONTA,)).fetchall()
    assert ev == [("vale", 120000, OUT, r["lancamento_id"])]
    # a folha agora deve o saldo, e a conta do saldo não mudou
    assert _a_pagar(pool, fid) == devido - 120000 == _abertos(pool, fid)["saldo"]["valor"]
    # e o holerite mostra o 961
    h = emp.holerite_funcionario(pool, CONTA, fid, 2026, 10)
    assert ("961", "Adiantamento Salarial", "", 120000) in h["descontos"]


def test_baixa_do_saldo_quita_a_folha(pool, limpo):
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    ab = _abertos(pool, fid)
    emp.dar_baixa_titulo(pool, CONTA, ab["adiantamento"]["id"])
    emp.dar_baixa_titulo(pool, CONTA, ab["saldo"]["id"])
    assert _a_pagar(pool, fid) == 0
    folha = emp.folha_do_mes(pool, CONTA, 2026, 10)
    assert next(i for i in folha["itens"] if i["id"] == fid)["quitado"]
    assert _n_lanc(pool) == 2
    # pagar a folha depois não paga nada de novo
    assert emp.pagar_folha(pool, CONTA, 2026, 10)["total_centavos"] == 0


def test_pagar_folha_cancela_as_contas_que_sobraram(pool, limpo):
    """Pagou tudo pelo "pagar ✓": as contas abertas do mês saem canceladas — senão
    o salário sairia de novo na baixa delas."""
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    devido = _a_pagar(pool, fid)
    r = emp.pagar_folha(pool, CONTA, 2026, 10, funcionario_id=fid)
    assert r["total_centavos"] == devido
    assert _abertos(pool, fid) == {}
    assert {t["status"] for t in _titulos(pool, fid)} == {"cancelado"}
    # e o cron do dia seguinte não as traz de volta
    ft.sincronizar_todas(pool)
    assert _abertos(pool, fid) == {}


def test_cancelada_pelo_dono_nao_volta(pool, limpo):
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    ab = _abertos(pool, fid)
    assert emp.cancelar_titulo(pool, CONTA, ab["adiantamento"]["id"])
    # sem adiantamento este mês, o saldo vira o líquido inteiro
    assert _abertos(pool, fid)["saldo"]["valor"] == _a_pagar(pool, fid)
    ft.sincronizar_todas(pool)
    assert "adiantamento" not in _abertos(pool, fid)
    # mudar a configuração é decisão nova: aí a parte nasce de novo
    assert ft.configurar(pool, CONTA, fid, **{**_ADI_SALDO, "adiantamento_pct": "30"})["ok"]
    assert _abertos(pool, fid)["adiantamento"]["valor"] == 90000


def test_salvar_o_formulario_nao_ressuscita_o_que_foi_cancelado(pool, limpo):
    """O formulário manda tudo junto. Trocar só o dia do saldo não pode trazer de
    volta o adiantamento que o dono cancelou este mês."""
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    emp.cancelar_titulo(pool, CONTA, _abertos(pool, fid)["adiantamento"]["id"])
    r = ft.configurar(pool, CONTA, fid, **{**_ADI_SALDO, "saldo_regra": "dia",
                                           "dia_pagamento": 10})
    assert r["ok"] and r["criados"] == 0
    ab = _abertos(pool, fid)
    assert "adiantamento" not in ab
    assert ab["saldo"]["venc"] == date(2026, 11, 10), "a data do saldo andou"


def test_mudar_a_regra_nao_reabre_o_mes_passado(pool, limpo, monkeypatch):
    """O saldo de outubro (vence 07/11) que o dono cancelou por ter pago por fora
    não volta porque ele mudou a regra em novembro."""
    from finance import empresa as emp
    from finance import relogio
    fid = _func(pool, **_ADI_SALDO)
    emp.cancelar_titulo(pool, CONTA, _abertos(pool, fid)["saldo"]["id"])
    monkeypatch.setattr(relogio, "hoje", lambda: date(2026, 11, 3))
    assert ft.configurar(pool, CONTA, fid, **{**_ADI_SALDO, "saldo_regra": "dia",
                                              "dia_pagamento": 9})["ok"]
    out = [t for t in _titulos(pool, fid) if t["comp"] == OUT and t["parte"] == "saldo"]
    assert [t["status"] for t in out] == ["cancelado"]


def test_demitido_nao_ganha_conta_nova(pool, limpo, monkeypatch):
    from finance import empresa as emp
    from finance import relogio
    fid = _func(pool, **_ADI_SALDO)
    emp.atualizar_funcionario(pool, CONTA, fid, demitido_em=date(2026, 10, 10))
    assert set(_abertos(pool, fid)) == {"adiantamento", "saldo"}, \
        "o que existe fica: a rescisão o dono acerta à mão"
    monkeypatch.setattr(relogio, "hoje", lambda: date(2026, 11, 1))
    assert ft.sincronizar_todas(pool)["criados"] == 0


def test_a_tela_nao_preve_o_que_o_dono_cancelou_e_mostra_quando_pagou(pool, limpo):
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    ab = _abertos(pool, fid)
    emp.cancelar_titulo(pool, CONTA, ab["adiantamento"]["id"])
    emp.dar_baixa_titulo(pool, CONTA, ab["saldo"]["id"], data_pagto=date(2026, 10, 2))
    folha = emp.folha_do_mes(pool, CONTA, 2026, 10)
    ag = ft.agenda(pool, CONTA, folha["itens"])[fid]
    assert "adiantamento" not in ag["partes"], "cancelado não volta como previsto"
    assert ag["partes"]["saldo"]["situacao"] == "pago"
    assert ag["partes"]["saldo"]["pago_em"] == date(2026, 10, 2)


def test_a_data_mudada_a_mao_fica_e_a_referencia_e_a_competencia(pool, limpo):
    """O dono mudou o vencimento do saldo (484): mudar a regra depois não traz a
    data de volta. E a referência das contas da folha é a competência — o
    adiantamento do dia 20/10 é do salário de outubro, não de setembro."""
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    sid = _abertos(pool, fid)["saldo"]["id"]
    assert emp.editar_titulo(pool, CONTA, sid, vencimento=date(2026, 11, 6))
    assert ft.configurar(pool, CONTA, fid, **{**_ADI_SALDO, "saldo_regra": "dia",
                                              "dia_pagamento": 10})["ok"]
    assert _abertos(pool, fid)["saldo"]["venc"] == date(2026, 11, 6)
    lista = emp.listar_titulos(pool, CONTA, status="aberto", tipo="pagar")
    assert {t["referencia"] for t in lista} == {OUT}
    assert all(t["referencia_anotada"] for t in lista)


def test_apagar_conta_da_folha_vira_cancelar(pool, limpo):
    """Apagada, a sincronização a faria nascer de novo no dia seguinte."""
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    tid = _abertos(pool, fid)["adiantamento"]["id"]
    assert emp.apagar_titulo(pool, CONTA, tid)
    assert [t["status"] for t in _titulos(pool, fid) if t["id"] == tid] == ["cancelado"]
    ft.sincronizar_todas(pool)
    assert "adiantamento" not in _abertos(pool, fid)


def test_o_valor_mudado_a_mao_fica(pool, limpo):
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    ab = _abertos(pool, fid)
    devido = _a_pagar(pool, fid)
    assert emp.editar_titulo(pool, CONTA, ab["adiantamento"]["id"], valor_centavos=50000)
    ft.sincronizar_todas(pool)
    ab = _abertos(pool, fid)
    assert ab["adiantamento"]["valor"] == 50000, "o cron não desfaz a mão do dono"
    assert ab["saldo"]["valor"] == devido - 50000, "e o saldo cobre a diferença"


def test_valor_fixo_e_saldo_no_dia_de_sempre(pool, limpo):
    fid = _func(pool, adiantamento_dia=15, adiantamento_centavos=80000,
                saldo_regra="dia", dia_pagamento=10, gerar_titulos=True)
    ab = _abertos(pool, fid)
    assert (ab["adiantamento"]["valor"], ab["adiantamento"]["venc"]) == (80000, date(2026, 10, 15))
    assert ab["saldo"]["venc"] == date(2026, 11, 10)


def test_configuracao_invalida_nao_grava(pool, limpo):
    fid = _func(pool)
    for kw in ({"adiantamento_dia": 20}, {"adiantamento_dia": 20, "adiantamento_pct": "120"},
               {"adiantamento_dia": 31, "adiantamento_pct": "40"}):
        assert not ft.configurar(pool, CONTA, fid, **kw)["ok"]
    assert ft.configurar(pool, CONTA, 999999, adiantamento_dia=None)["erro"] == \
        "Funcionário não encontrado."
    assert _titulos(pool, fid) == []


def test_sem_contas_a_pagar_so_a_previsao(pool, limpo):
    """A regra serve sozinha: a linha da folha mostra as datas, sem conta nenhuma."""
    from finance import empresa as emp
    fid = _func(pool, adiantamento_dia=20, adiantamento_pct="40", saldo_regra="quinto_util")
    assert _titulos(pool, fid) == []
    folha = emp.folha_do_mes(pool, CONTA, 2026, 10)
    ag = ft.agenda(pool, CONTA, folha["itens"])[fid]
    assert ag["configurado"] and not ag["gera_titulos"]
    assert ag["partes"]["adiantamento"] == {"vencimento": date(2026, 10, 20),
                                            "valor": 120000, "situacao": "previsto"}
    assert ag["partes"]["saldo"]["valor"] == _a_pagar(pool, fid) - 120000
    assert ag["partes"]["saldo"]["situacao"] == "previsto"


def test_desligar_nao_apaga_mas_a_trava_continua(pool, limpo):
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    assert ft.configurar(pool, CONTA, fid, **{**_ADI_SALDO, "gerar_titulos": False})["ok"]
    assert set(_abertos(pool, fid)) == {"adiantamento", "saldo"}, "o que existe fica"
    emp.pagar_folha(pool, CONTA, 2026, 10, funcionario_id=fid)
    assert _abertos(pool, fid) == {}, "a folha quitada ainda cancela"


def test_remover_o_adiantamento_pago_pela_conta_e_recusado(pool, limpo):
    """O ✕ remover da folha apagaria o lançamento da baixa e deixaria a conta
    "paga" sem dinheiro atrás. Quem desfaz é a conta a pagar."""
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    emp.dar_baixa_titulo(pool, CONTA, _abertos(pool, fid)["adiantamento"]["id"])
    evs = emp.eventos_folha_do_mes(pool, CONTA, 2026, 10)[fid]
    assert [e["do_titulo"] for e in evs] == [True]
    assert emp.remover_evento_folha(pool, CONTA, evs[0]["id"]) is False
    assert _n_lanc(pool) == 1
    # o "+ adiantar" de sempre continua removível — e o saldo acompanha
    emp.registrar_evento_folha(pool, CONTA, fid, "vale", 10000, competencia=OUT)
    saldo = _abertos(pool, fid)["saldo"]["valor"]
    manual = [e for e in emp.eventos_folha_do_mes(pool, CONTA, 2026, 10)[fid]
              if not e["do_titulo"]]
    assert emp.remover_evento_folha(pool, CONTA, manual[0]["id"]) is True
    assert _abertos(pool, fid)["saldo"]["valor"] == saldo + 10000


def test_conciliar_e_desfazer_mexem_na_folha(pool, limpo):
    """Pago pelo extrato (conciliação): o evento nasce amarrado ao lançamento do
    extrato; desfazer a conciliação tira o evento e deixa o extrato onde estava."""
    from finance import empresa as emp
    from finance.livro_caixa import LivroCaixa
    from finance.models import Lancamento, Tipo
    fid = _func(pool, **_ADI_SALDO)
    tid = _abertos(pool, fid)["adiantamento"]["id"]
    lanc = LivroCaixa(pool, CONTA).adicionar(
        Lancamento(tipo=Tipo.DESPESA, valor_centavos=120000, categoria="Pessoal",
                   descricao="PIX ENVIADO Ana Teste", data=date(2026, 10, 20),
                   origem="manual", natureza="empresa"), forcar=True)
    r = emp.conciliar_titulo(pool, CONTA, tid, lanc.id)
    assert r["ok"], r
    with pool.connection() as c:
        ev = c.execute("select tipo, lancamento_id from folha_eventos where conta_id=%s",
                       (CONTA,)).fetchall()
    assert ev == [("vale", lanc.id)]
    assert _n_lanc(pool) == 1
    assert emp.desfazer_conciliacao(pool, CONTA, tid)["ok"]
    with pool.connection() as c:
        assert c.execute("select count(*) from folha_eventos where conta_id=%s",
                         (CONTA,)).fetchone()[0] == 0
    assert _n_lanc(pool) == 1, "o lançamento do extrato continua onde estava"
    assert "adiantamento" in _abertos(pool, fid)


def test_excluir_o_cadastro_cancela_as_contas_dele(pool, limpo):
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    assert emp.excluir_funcionario(pool, CONTA, fid)["excluido"]
    with pool.connection() as c:
        rows = c.execute("""select status, folha_funcionario_id from titulos
                             where conta_id=%s""", (CONTA,)).fetchall()
    assert rows and all(r == ("cancelado", None) for r in rows)


def test_o_cron_cria_a_competencia_nova_no_dia_primeiro(pool, limpo, monkeypatch):
    from finance import relogio
    fid = _func(pool, **_ADI_SALDO)
    monkeypatch.setattr(relogio, "hoje", lambda: date(2026, 11, 1))
    r = ft.sincronizar_todas(pool)
    assert r["criados"] == 2
    nov = {t["parte"]: t for t in _titulos(pool, fid, "aberto") if t["comp"] == date(2026, 11, 1)}
    assert nov["adiantamento"]["venc"] == date(2026, 11, 20)
    assert nov["saldo"]["venc"] == date(2026, 12, 5)
    # outubro segue lá, intocado (o saldo dele vence em 07/11)
    assert {t["parte"] for t in _titulos(pool, fid, "aberto") if t["comp"] == OUT} == {
        "adiantamento", "saldo"}


def test_a_folha_mostra_o_saldo_do_mes_passado_enquanto_aberto(pool, limpo, monkeypatch):
    from finance import empresa as emp
    from finance import relogio
    fid = _func(pool, **_ADI_SALDO)
    monkeypatch.setattr(relogio, "hoje", lambda: date(2026, 11, 3))
    ft.sincronizar_todas(pool)
    folha = emp.folha_do_mes(pool, CONTA, 2026, 11)
    ag = ft.agenda(pool, CONTA, folha["itens"])[fid]
    assert ag["saldo_anterior"]["mes"] == "10/2026"
    assert ag["saldo_anterior"]["vencimento"] == date(2026, 11, 7)


def test_a_conta_a_pagar_diz_que_e_da_folha(pool, limpo):
    from finance import empresa as emp
    fid = _func(pool, **_ADI_SALDO)
    lista = emp.listar_titulos(pool, CONTA, status="aberto", tipo="pagar")
    assert sorted(t["folha_parte"] for t in lista) == ["adiantamento", "saldo"]
    assert {t["tipo_despesa"] for t in lista} == {"fixa"}
    assert fid


# ═════════════════════════════════════════════════════════════════ a tela
def _tela_folha(itens):
    """Renderiza só o pedaço da folha do template `empresa` — o bloco inteiro
    pediria o contexto da aba toda."""
    import re

    from web.portal import _env
    fonte = _env.loader.get_source(_env, "empresa")[0]
    ini = fonte.index('{% for f in folha.itens %}<details class="folha-lin">')
    fim = fonte.index("</details>{% endfor %}", ini) + len("</details>{% endfor %}")
    trecho = fonte[ini:fim]
    tpl = _env.from_string(trecho)
    html = tpl.render(folha={"itens": itens}, hist_salarios={}, hoje_iso="2026-10-02")
    return re.sub(r"\s+", " ", html)


def _item(**kw):
    base = {"id": 5, "nome": "Ana Teste", "cargo": "Vendedora", "pro_labore": False,
            "demitido_em": None, "quitado": False, "a_pagar_centavos": 250000,
            "salario_centavos": 300000, "extras_centavos": 0, "inss_centavos": 0,
            "vt_centavos": 0, "vales_centavos": 0, "descontos_centavos": 0,
            "pago_centavos": 0, "beneficios_centavos": 0, "fgts_centavos": 0,
            "custo_real_centavos": 0, "vale_transporte": False, "departamento": "",
            "setor": "", "secao": "", "cbo": "", "cpf": "", "admitido_em": None,
            "dia_pagamento": 5, "eventos": [], "agenda": None}
    base.update(kw)
    return base


def test_tela_quem_nao_configurou_ve_a_folha_como_sempre():
    html = _tela_folha([_item()])
    assert "folha-ag" not in html
    assert "📅 Datas de pagamento</summary>" in html
    assert 'action="/painel/empresa/funcionario/5/pagamento"' in html


def test_tela_mostra_as_duas_datas_e_o_formulario_preenchido():
    cfg = {"adiantamento_dia": 20, "adiantamento_pct": Decimal("40.00"),
           "adiantamento_centavos": None, "saldo_regra": "quinto_util",
           "titulos_folha_desde": OUT, "dia_pagamento": 5}
    ag = {"configurado": True, "regra": ft.regra_em_texto(cfg), "gera_titulos": True,
          "cfg": cfg, "pct_txt": "40", "saldo_anterior": None,
          "partes": {"adiantamento": {"vencimento": date(2026, 10, 20), "valor": 120000,
                                      "situacao": "aberto", "aprovacao": "aguardando",
                                      "mexido": False},
                     "saldo": {"vencimento": date(2026, 11, 7), "valor": 130000,
                               "situacao": "previsto"}}}
    html = _tela_folha([_item(agenda=ag)])
    assert "📅 Adiantamento</span><span>vence 20/10</span><b>R$ 1.200,00</b>" in html
    assert "conta a pagar · aguardando liberação" in html
    assert "📅 Saldo</span><span>vence 07/11</span><b>R$ 1.300,00</b>" in html
    assert "saldo no 5º dia útil do mês seguinte" in html
    assert 'name="adiant_dia" min="1" max="28" value="20"' in html
    assert 'name="adiant_pct" inputmode="decimal" value="40"' in html
    assert 'value="quinto_util" checked' in html
    assert 'name="gerar" value="1" checked' in html
    assert "já está nas contas a pagar" in html, "o + adiantar avisa que não é ele"
