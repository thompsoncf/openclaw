"""O lembrete de segunda das obras, a margem da obra e a faixa do Financeiro
(finance/obras_lembrete.py, finance/obras.py `margem`, finance/obra_venda.py
`topo_financeiro`; migração 365). Desenho: docs/mockups/nicho_construcao.html,
seção 06.

Os testes que mais importam:

`test_uma_vez_por_semana_por_pessoa` — o ticker roda a cada 2 minutos a segunda
inteira; o lembrete que se repete vira spam e o dono desliga.

`test_fora_da_janela_vai_por_template_numa_linha_so` — na segunda de manhã a
janela de 24 h do WhatsApp quase sempre está fechada (o dono da PX2 tinha falado
5 dias antes); texto livre ali some sem erro nenhum.

`test_margem_usa_o_previsto_enquanto_a_obra_nao_termina` — no meio da obra o
gasto ainda vai crescer; mostrar a margem só com o gasto de hoje promete lucro
que não vai existir.
"""
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import obra_reforma as orf
from finance import obra_venda as ov
from finance import obras as ob
from finance import obras_lembrete as obl

_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "058_dados_empresa.sql",
              "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql", "067_titulos_cliente.sql",
              "131_pessoa_cnpj.sql", "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql", "182_clientes_papel.sql",
              "186_plano_aporte_socios.sql", "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql", "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql", "325_tipo_despesa.sql",
              "336_plano_fardamentos.sql", "349_plano_obras.sql", "351_obras.sql",
              "353_obra_venda_documentos.sql", "355_reforma_orcamento.sql",
              "365_obras_lembrete_segunda.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
HOJE = date.today()
# uma segunda qualquer, às 9h de Brasília
SEGUNDA = datetime(2026, 9, 28, 9, 0)


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obras_lembrete_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((_BASE / m).read_text(encoding="utf-8"))
            c.commit()
    with p.connection() as c:
        # o que o lembrete lê dos membros, sem puxar as migrações da equipe inteira;
        # e a memória da conversa, que em produção nasceu sem DDL no repositório
        c.execute("""alter table membros drop constraint if exists membros_papel_check;
                     alter table membros add column if not exists email text;
                     alter table membros add column if not exists whatsapp text;
                     create table if not exists memoria_conversa (
                        conversa_id text primary key, mensagens jsonb,
                        atualizado_em timestamptz not null default now())""")
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta(pool):
    with pool.connection() as c:
        cid = c.execute("""insert into contas (tipo, nome, nome_fantasia)
                           values ('pj', 'Pablo', 'PX2 Empreendimentos') returning id"""
                        ).fetchone()[0]
        c.commit()
    return cid


@pytest.fixture()
def canais(monkeypatch):
    """Grava o que sairia por cada canal, sem rede."""
    saiu = []
    from finance import email_sender, notificar, whatsapp_twilio as wt
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+10000000000")
    monkeypatch.delenv("TWILIO_TMPL_OBRAS_SID", raising=False)
    monkeypatch.setattr(wt, "enviar_texto",
                        lambda rem, num, corpo: saiu.append(("whatsapp", num, corpo)) or {"ok": True})
    monkeypatch.setattr(wt, "enviar_template",
                        lambda rem, num, sid, v: saiu.append(("template", num, v)) or {"ok": True})
    monkeypatch.setattr(notificar, "enviar_para_dono", lambda pool, cid, txt: False)
    monkeypatch.setattr(email_sender, "enviar_aviso",
                        lambda dest, tit, msg, nome=None, **kw: saiu.append(("email", dest, msg)) or True)
    return saiu


def _membro(pool, conta, papel="dono", whatsapp="99988887777", email="pablo@px2.test",
            nome="Pablo Thyago"):
    with pool.connection() as c:
        mid = c.execute("""insert into membros (conta_id, nome, papel, whatsapp, email)
                           values (%s,%s,%s,%s,%s) returning id""",
                        (conta, nome, papel, whatsapp, email)).fetchone()[0]
        c.commit()
    return mid


def _falou_agora(pool, membro_id, horas_atras=1):
    with pool.connection() as c:
        c.execute("""insert into memoria_conversa (conversa_id, mensagens, atualizado_em)
                     values (%s, '[]', now() - make_interval(hours => %s))
                     on conflict (conversa_id) do update set atualizado_em = excluded.atualizado_em""",
                  (f"wa:{membro_id}", horas_atras))
        c.commit()


def _casa_pronta(pool, conta, nome="Casa 2", **kw):
    o = ob.criar_obra(pool, conta, nome, "casa", **kw)
    for e in o["etapas"]:
        ob.marcar_etapa(pool, conta, o["id"], e["id"])
    return ob.obter_obra(pool, conta, o["id"])


def _gasto(pool, conta, obra, valor):
    with pool.connection() as c:
        c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos, categoria, descricao,
                                              data, natureza, centro_custo_id)
                     values (%s,'despesa',%s,'Insumos','nota',%s,'empresa',%s)""",
                  (conta, valor, HOJE, obra["centro_custo_id"]))
        c.commit()


# ── o que entra na mensagem ───────────────────────────────────────────────
def test_casa_pronta_diz_o_que_trava_e_quanto_esta_parado(pool, conta):
    casa = _casa_pronta(pool, conta)
    _gasto(pool, conta, casa, 7_930_000)
    ob.criar_obra(pool, conta, "Casa 3", "casa")          # em obra, sem prazo vencendo
    p = obl.pendencias(pool, conta)
    assert p["itens"] == ["Casa 2: o que trava é habite-se; "
                          f"Pronta há 0 dia(s) esperando habite-se."]
    assert p["parado"] == 7_930_000
    txt = obl.texto("PABLO THYAGO", p)
    assert txt.startswith("Bom dia, Pablo!") and "R$ 79.300,00" in txt
    assert "saiu o habite-se da casa 2" in txt


def test_casa_em_obra_entra_pelo_prazo_do_cno(pool, conta):
    ob.criar_obra(pool, conta, "Casa 4", "casa", inicio_em=HOJE - timedelta(days=40))
    itens = obl.pendencias(pool, conta)["itens"]
    assert len(itens) == 1 and itens[0].startswith("Casa 4: CNO atrasado")


def test_casa_com_papel_em_dia_e_credito_na_conta_nao_entra(pool, conta):
    casa = _casa_pronta(pool, conta)
    for t in ("habite_se", "cnd_obra", "averbacao"):
        ov.marcar_documento(pool, conta, casa["id"], t)
    ov.salvar_venda(pool, conta, casa["id"], comprador="J. Silva",
                    valor_venda_centavos=15_000_000, financiamento_centavos=14_000_000)
    for passo in ("aprovado", "avaliacao", "assinatura", "registro", "creditado"):
        ov.andar_venda(pool, conta, casa["id"], passo)
    assert obl.pendencias(pool, conta)["itens"] == []


def test_reforma_com_parcela_liberada_entra_pra_cobrar(pool, conta):
    o = ob.criar_obra(pool, conta, "Reforma Dona Márcia", "reforma")
    itens = [{"servico": "Pintura", "tipo": "mao_de_obra", "unidade": "m2",
              "quantidade": 40, "valor_unit_centavos": 2_000}]
    orc = orf.salvar_rascunho(pool, conta, o["id"], itens=itens, modelo_pagamento="etapas")
    env = orf.enviar(pool, conta, orc["id"])
    assert orf.aceitar(pool, env["token"], "Márcia", "", "")
    completa = ob.obter_obra(pool, conta, o["id"])
    for e in completa["etapas"]:
        ob.marcar_etapa(pool, conta, o["id"], e["id"])
    linhas = obl.pendencias(pool, conta)["itens"]
    assert linhas and all(l.startswith("Reforma Dona Márcia: a parcela") for l in linhas)
    assert "já pode ser cobrada" in linhas[0]


def test_linha_do_template_nao_quebra_linha_e_tem_tamanho():
    p = {"itens": [f"Casa {n}: o que trava é habite-se." for n in range(60)], "parado": 100}
    linha = obl.linha_do_template(p)
    assert "\n" not in linha and "  " not in linha
    assert len(linha) <= obl._MAX_VARIAVEL and linha.endswith("…")


# ── quando e por onde sai ─────────────────────────────────────────────────
def test_so_na_segunda_de_dia(pool, conta, canais):
    _casa_pronta(pool, conta)
    _membro(pool, conta)
    assert obl.rodar(pool, SEGUNDA.replace(hour=7)) == 0
    assert obl.rodar(pool, SEGUNDA + timedelta(days=1)) == 0
    assert obl.rodar(pool, SEGUNDA.replace(hour=20)) == 0
    assert not canais


def test_uma_vez_por_semana_por_pessoa(pool, conta, canais):
    _casa_pronta(pool, conta)
    _membro(pool, conta, email="semana@px2.test", whatsapp="")
    obl.rodar(pool, SEGUNDA)
    obl.rodar(pool, SEGUNDA + timedelta(minutes=2))
    obl.rodar(pool, SEGUNDA.replace(hour=15))
    assert len([s for s in canais if s[1] == "semana@px2.test"]) == 1
    obl.rodar(pool, SEGUNDA + timedelta(days=7))
    assert len([s for s in canais if s[1] == "semana@px2.test"]) == 2


def test_dentro_da_janela_vai_por_whatsapp_com_o_texto_inteiro(pool, conta, canais):
    _casa_pronta(pool, conta)
    mid = _membro(pool, conta, whatsapp="98911112222", email="")
    _falou_agora(pool, mid, horas_atras=3)
    obl.rodar(pool, SEGUNDA)
    [(canal, num, corpo)] = [s for s in canais if s[1] == "98911112222"]
    assert canal == "whatsapp" and "Casa 2: o que trava é habite-se" in corpo
    with pool.connection() as c:
        assert c.execute("select canal from obras_lembretes where membro_id=%s",
                         (mid,)).fetchone()[0] == "whatsapp"


def test_fora_da_janela_vai_por_template_numa_linha_so(pool, conta, canais, monkeypatch):
    monkeypatch.setenv("TWILIO_TMPL_OBRAS_SID", "HXteste")
    _casa_pronta(pool, conta)
    mid = _membro(pool, conta, whatsapp="98933334444", email="")
    _falou_agora(pool, mid, horas_atras=5 * 24)
    obl.rodar(pool, SEGUNDA)
    [(canal, _, var)] = [s for s in canais if s[1] == "98933334444"]
    assert canal == "template"
    assert var["1"] == "PX2 Empreendimentos"
    assert var["2"].startswith("Casa 2: o que trava é habite-se") and "\n" not in var["2"]


def test_fora_da_janela_sem_template_cai_no_email(pool, conta, canais):
    _casa_pronta(pool, conta)
    mid = _membro(pool, conta, whatsapp="98955556666", email="socio@px2.test", papel="gestor")
    obl.rodar(pool, SEGUNDA)
    assert [s[0] for s in canais if s[1] in ("98955556666", "socio@px2.test")] == ["email"]
    with pool.connection() as c:
        assert c.execute("select canal from obras_lembretes where membro_id=%s",
                         (mid,)).fetchone()[0] == "email"


def test_sem_pendencia_nao_manda_nem_trava_a_semana(pool, conta, canais):
    ob.criar_obra(pool, conta, "Casa 9", "casa")
    mid = _membro(pool, conta, email="nada@px2.test")
    obl.rodar(pool, SEGUNDA)
    assert not [s for s in canais if s[1] == "nada@px2.test"]
    with pool.connection() as c:
        assert c.execute("select count(*) from obras_lembretes where membro_id=%s",
                         (mid,)).fetchone()[0] == 0


def test_vendedor_nao_recebe(pool, conta, canais):
    _casa_pronta(pool, conta)
    _membro(pool, conta, papel="vendedor", email="vendedor@px2.test", whatsapp="")
    obl.rodar(pool, SEGUNDA)
    assert not [s for s in canais if s[1] == "vendedor@px2.test"]


# ── a margem ──────────────────────────────────────────────────────────────
def test_margem_usa_o_previsto_enquanto_a_obra_nao_termina(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 5", "casa", custo_previsto_centavos=8_200_000,
                      valor_centavos=15_000_000)
    _gasto(pool, conta, o, 3_000_000)
    m = ob.margem(ob.obter_obra(pool, conta, o["id"]))
    assert m == {"preco": 15_000_000, "custo": 8_200_000, "valor": 6_800_000,
                 "pct": 45, "base": "previsto"}


def test_margem_da_casa_pronta_usa_o_gasto_e_o_preco_da_venda(pool, conta):
    casa = _casa_pronta(pool, conta, "Casa 6", custo_previsto_centavos=8_200_000,
                        valor_centavos=14_000_000)
    _gasto(pool, conta, casa, 10_880_000)
    ov.salvar_venda(pool, conta, casa["id"], comprador="J.", valor_venda_centavos=15_000_000)
    o = ob.obter_obra(pool, conta, casa["id"])
    m = ob.margem(o, ov.venda(pool, conta, casa["id"]))
    assert (m["preco"], m["custo"], m["valor"], m["pct"], m["base"]) == \
        (15_000_000, 10_880_000, 4_120_000, 27, "gasto")
    assert "Margem R$ 41.200,00 (27% de R$ 150.000,00, com o gasto até agora)." in \
        ob.resumo_da_obra(o, venda=ov.venda(pool, conta, casa["id"]))


def test_margem_negativa_e_sem_preco(pool, conta):
    o = ob.criar_obra(pool, conta, "Reforma cara", "reforma", valor_centavos=1_000_000)
    _gasto(pool, conta, o, 1_500_000)
    o = ob.obter_obra(pool, conta, o["id"])
    assert ob.margem(o)["valor"] == -500_000
    sem_preco = ob.obter_obra(pool, conta, ob.criar_obra(pool, conta, "Casa 7", "casa")["id"])
    assert ob.margem(sem_preco) is None


# ── a faixa do Financeiro ─────────────────────────────────────────────────
def test_faixa_do_financeiro(pool, conta):
    assert ov.topo_financeiro(pool, conta) is None
    casa = _casa_pronta(pool, conta)
    _gasto(pool, conta, casa, 7_930_000)
    with pool.connection() as c:
        c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos, categoria, descricao,
                                              data, natureza)
                     values (%s,'despesa',150000,'Insumos','areia',%s,'empresa')""", (conta, HOJE))
        c.commit()
    t = ov.topo_financeiro(pool, conta)
    assert t == {"parado": 7_930_000, "travadas": ["Casa 2 (habite-se)"],
                 "sem_n": 1, "sem_total": 150_000}


def test_a_tela_do_financeiro_compila():
    """A faixa entrou no template `dash`, que é a tela de toda conta: um erro de
    sintaxe ali derruba o Financeiro de todo mundo, não só o da construção."""
    from web import portal
    portal._env.get_template("dash")
