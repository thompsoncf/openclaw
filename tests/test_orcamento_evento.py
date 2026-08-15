"""Regressão do MODO EVENTO do orçamento (migração 147).

O módulo nasceu pra venda recorrente (setup + mensalidade). No nicho 'eventos'
o orçamento é outro bicho: data da festa, número de convidados, horário de
início/encerramento, quantidade × valor unitário e parcelas com vencimento.
Aqui testamos as três pontas disso:

  • agenda.janela_evento — a festa que "encerra às 24" acaba 00:00 do DIA
    SEGUINTE (virar a noite é a regra do ramo);
  • proposta — carrega/renderiza o orçamento de evento e reserva a data na
    agenda quando o cliente assina (idempotente);
  • vendas.fechar_orcamento — cada parcela vira um título a receber.

Banco de TESTE separado (ver tests/conftest.py).
"""
import json
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from psycopg.errors import UniqueViolation

from db.conexao import init_schema
from finance import agenda as ag, vendas
from web import proposta as prop
from web.painel_servicos import _com_retry_numero, _garantir_tabela

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


def _sql(nome: str) -> str:
    return (BASE / nome).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    with p.connection() as c:
        c.execute(_sql("053_modulo_pj.sql"))        # titulos
        c.execute(_sql("098_agenda.sql"))           # eventos_agenda
        c.execute(_sql("099_agenda_tipo.sql"))      # eventos_agenda.tipo
        c.execute(_sql("130_evento_desfecho.sql"))      # .desfecho
        c.execute(_sql("131_evento_link_online.sql"))   # .link_online
        # dados da empresa que o cabeçalho do orçamento usa. Vêm das migrações
        # 038/045/049/058/059, que arrastam junto loja/catálogo — aqui só as
        # colunas, que é o que o teste precisa.
        c.execute("""
            alter table contas add column if not exists endereco      text;
            alter table contas add column if not exists cep           text;
            alter table contas add column if not exists bairro        text;
            alter table contas add column if not exists cidade        text;
            alter table contas add column if not exists uf            varchar(2);
            alter table contas add column if not exists telefone      text;
            alter table contas add column if not exists email_empresa text;
            alter table contas add column if not exists logo_url      text;
            alter table contas add column if not exists nome_fantasia text;
            alter table contas add column if not exists cnae          text;
        """)
        c.execute("""create table if not exists nichos (
            id bigserial primary key, nome text, slug text unique, tipo text,
            ativo boolean not null default true)""")
        c.execute("alter table contas add column if not exists nicho_id bigint references nichos(id)")
        c.execute("""create table if not exists orcamentos (
            id bigserial primary key, cliente text, empresa text, segmento text,
            setup_centavos bigint default 0, mensal_centavos bigint default 0,
            primeiro_ano_centavos bigint default 0, n_modulos int default 0,
            criado_em timestamptz default now())""")
        _garantir_tabela(c)     # espelha 068/069/070/074/147 (inclusive modo/evento/parcelas)
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            """insert into contas (tipo, nome, razao_social, documento, endereco,
                                   bairro, cep, cidade, uf, telefone, email_empresa)
               values ('pj','Prime Eventos','M S de Sousa Junior Festas e Eventos LTDA',
                       '52.752.898/0001-58','Rua Deoclécio Brito, 3399','Planalto',
                       '64050-050','Teresina','PI','(86) 99409-5516',
                       'primeeventosthe@gmail.com')
               returning id""").fetchone()[0]
        c.execute("insert into membros (conta_id, nome, papel) values (%s,%s,'dono')",
                  (cid, "Manoel Soares de Sousa Junior"))
        c.commit()
    return cid


def _conta_do_nicho(pool, slug: str) -> int:
    with pool.connection() as c:
        c.execute("insert into nichos (nome, slug, tipo) values (%s,%s,'produto') "
                  "on conflict (slug) do nothing", (slug, slug))
        cid = c.execute(
            """insert into contas (tipo, nome, nicho_id)
               values ('pj', %s, (select id from nichos where slug=%s)) returning id""",
            (f"Conta {slug}", slug)).fetchone()[0]
        c.commit()
    return cid


EVENTO = {"data": "2025-11-18", "convidados": 50, "inicio": "19:00", "fim": "24:00",
          "tipo": "Aniversário", "contratos": ["Locação de espaço"],
          "local": "Espaço 01"}
ITENS = [{"nome": "Espaço 01 — completo", "desc": "O espaço inclui: mesas, 60 cadeiras…",
          "setup": 7200, "mensal": 0, "qtd": 1, "unitario": 7200,
          "categoria": "Locação de espaço", "foto_url": "https://ex.com/espaco.jpg"},
         {"nome": "Cadeira Modway Entreat", "desc": "Além das 60 inclusas.",
          "setup": 250, "mensal": 0, "qtd": 10, "unitario": 25,
          "categoria": "Locação de móveis e utensílios", "foto_url": ""}]
PARCELAS = [{"venc": "2025-11-13", "valor_centavos": 181000, "forma": "Pix",
             "obs": "Sinal — confirma a reserva da data"},
            {"venc": "2025-12-13", "valor_centavos": 600000, "forma": "Cartão de crédito",
             "obs": "12 parcelas de R$ 500,00"}]


def _semear(pool, conta_id, *, status="enviado", evento=EVENTO, parcelas=PARCELAS,
            numero=60, total_centavos=745000):
    """Cria um orçamento de evento e devolve (id, token). O token é sorteado: o
    banco de teste é compartilhado entre módulos e não é truncado entre runs."""
    token = "EV" + secrets.token_hex(6)
    with pool.connection() as c:
        oid = c.execute(
            """insert into orcamentos (conta_id, empresa, cliente, escopo, itens,
                   setup_centavos, primeiro_ano_centavos, status, token, modo,
                   evento, parcelas, numero, endereco, cep, cidade, uf, cnpj,
                   email, telefone, criado_por)
               values (%s,'Maria Teste','Maria Teste','Reserva com o sinal.',%s::jsonb,
                       %s,%s,%s,%s,'evento',%s::jsonb,%s::jsonb,%s,
                       'Rua das Flores, 120','64049-000','Teresina','PI',
                       '000.000.000-00','maria@teste.com','(86) 99999-0000',
                       (select min(id)::text from membros where conta_id=%s))
               returning id""",
            (conta_id, json.dumps(ITENS), total_centavos, total_centavos, status, token,
             json.dumps(evento) if evento is not None else None,
             json.dumps(parcelas), numero, conta_id)).fetchone()[0]
        c.commit()
    return oid, token


# --------------------------------------------------------------- janela_evento
def test_festa_que_encerra_as_24_termina_no_dia_seguinte():
    ini, fim = ag.janela_evento("2025-11-18", "19:00", "24:00")
    assert (ini.day, ini.hour) == (18, 19)
    assert (fim.day, fim.hour) == (19, 0)      # 00:00 do dia seguinte, não 24h do mesmo
    assert fim > ini


def test_festa_que_vira_a_noite_tambem_rola_o_dia():
    _, fim = ag.janela_evento("2025-11-18", "20:00", "02:00")
    assert (fim.day, fim.hour) == (19, 2)


def test_festa_que_acaba_no_mesmo_dia_nao_rola():
    ini, fim = ag.janela_evento("18/11/2025", "14:00", "23:00")
    assert fim.day == ini.day == 18 and fim.hour == 23


def test_horario_tolerante_e_faltando():
    ini, fim = ag.janela_evento("2025-11-18", "19", "19h30")
    assert (ini.hour, ini.minute) == (19, 0)
    assert (fim.day, fim.hour, fim.minute) == (18, 19, 30)
    assert ag.janela_evento("2025-11-18", "", "24:00") == (None, None)   # sem início, não marca
    assert ag.janela_evento("", "19:00", "24:00") == (None, None)        # sem data, não marca
    ini2, fim2 = ag.janela_evento("2025-11-18", "19:00", "")             # sem fim: só o início
    assert ini2 is not None and fim2 is None


# --------------------------------------------------------------- proposta
def test_carregar_traz_evento_parcelas_e_emitente(pool, conta_id):
    _, tok = _semear(pool, conta_id)
    d = prop._carregar(tok, pool=pool)
    assert d["modo"] == "evento"
    assert d["evento"]["convidados"] == 50 and d["evento"]["fim"] == "24:00"
    assert d["numero"] == 60 and d["doc_num"] == "Nº 60"
    assert d["total"] == "R$ 7.450,00"                       # com centavos, como orçamento
    assert d["validade"] == date(2025, 11, 18)               # vale até o dia da festa
    assert d["emitente"]["doc"] == "52.752.898/0001-58"
    assert d["emitente"]["cidade"] == "Teresina"
    assert d["cliente"]["endereco"] == "Rua das Flores, 120"
    assert [p["valor_centavos"] for p in d["parcelas"]] == [181000, 600000]


def test_carregar_sem_evento_segue_recorrente(pool, conta_id):
    tok = "RC" + secrets.token_hex(6)
    with pool.connection() as c:
        c.execute(
            """insert into orcamentos (conta_id, empresa, cliente, itens, setup_centavos,
                   mensal_centavos, primeiro_ano_centavos, status, token)
               values (%s,'Clínica','Ana','[]'::jsonb,920000,210000,3440000,'enviado',%s)""",
            (conta_id, tok))
        c.commit()
    d = prop._carregar(tok, pool=pool)
    assert d["modo"] == "recorrente"
    assert d["validade"] == d["criado"] + timedelta(days=15)   # os 15 dias de sempre
    assert d["doc_num"].startswith("PR-")


def test_pagina_do_evento_mostra_qtd_valor_unitario_e_parcelas(pool, conta_id, monkeypatch):
    _, tok = _semear(pool, conta_id)
    # a rota chama _carregar() com o pool de produção; aqui ela usa o de teste.
    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    html = prop.proposta_publica(None, tok).body.decode()
    assert "Orçamento de evento" in html and "Nº 60" in html
    assert "50" in html and "19:00" in html and "24:00" in html      # bloco do evento
    assert "Aniversário" in html and "Locação de espaço" in html
    # linha de item: número puro (o R$ fica nos totais e nas parcelas)
    assert ">25,00<" in html and ">250,00<" in html                  # 10 × 25 = 250
    assert "https://ex.com/espaco.jpg" in html                       # foto do item
    assert html.count("Locação de espaço") >= 2   # categoria do item + subtotal por categoria
    assert "Casamento" in html and "Corporativo" in html   # as opções todas, como no papel
    assert "Vendedor: Manoel" in html
    assert "R$ 7.450,00" in html                                     # total do evento
    assert "Plano de pagamento" in html and "R$ 1.810,00" in html
    assert "52.752.898/0001-58" in html                              # emitente no cabeçalho
    assert "Mensalidade" not in html and "1º ano" not in html
    # bloco de aprovação: caixinha de aceite marcada pelo cliente + o botão que
    # diz o que acontece ao aprovar.
    assert "✓ Aprovar e reservar a data" in html
    assert 'type="checkbox" name="aceite"' in html
    assert "Li e concordo com os termos e valores deste orçamento" in html
    assert "nome, CPF, data/hora e IP" in html


def test_subtotal_por_categoria(pool, conta_id):
    from web.proposta import _subtotais
    assert _subtotais(ITENS) == [{"nome": "Locação de espaço", "valor": "R$ 7.200,00"},
                                 {"nome": "Locação de móveis e utensílios", "valor": "R$ 250,00"}]
    # uma categoria só repetiria o total, e item sem categoria mentiria a soma:
    # nos dois casos o bloco não aparece.
    assert _subtotais([ITENS[0]]) == []
    assert _subtotais([ITENS[0], {"setup": 100}]) == []


# ------------------------------------------------- o modo vem do nicho, sempre
def test_modo_por_nicho():
    assert vendas.modo_por_nicho("eventos") == "evento"
    assert vendas.modo_por_nicho("tecnologia") == "recorrente"
    assert vendas.modo_por_nicho(None) == "recorrente"


def test_modo_do_orcamento_pela_conta(pool):
    """Orçamento nasce em quatro portas (painel, cockpit, prospecção, agente) e
    todas perguntam à conta — senão a empresa de eventos manda folha de
    mensalidade dependendo de onde o vendedor clicou."""
    assert vendas.modo_do_orcamento(pool, _conta_do_nicho(pool, "eventos")) == "evento"
    assert vendas.modo_do_orcamento(pool, _conta_do_nicho(pool, "tecnologia")) == "recorrente"
    assert vendas.modo_do_orcamento(pool, 10 ** 9) == "recorrente"   # conta inexistente


def test_numero_repete_quando_dois_salvam_junto():
    """A série por conta é garantida pelo índice único: quem perde a corrida
    refaz o cálculo em vez de estourar na cara do vendedor."""
    class FakeCursor:
        def __init__(self): self.rollbacks = 0
        def rollback(self): self.rollbacks += 1

    c = FakeCursor()
    tentativas = []

    def uma_colisao():
        tentativas.append(1)
        if len(tentativas) == 1:
            raise UniqueViolation("colidiu")
        return ("ok",)

    assert _com_retry_numero(c, uma_colisao) == ("ok",)
    assert c.rollbacks == 1

    def sempre_colide():
        raise UniqueViolation("colidiu")

    assert _com_retry_numero(c, sempre_colide) is None    # desiste e devolve vazio


def test_folha_sem_dados_do_evento_nao_imprime_bloco_vazio(pool, conta_id, monkeypatch):
    """Conta de eventos que gerou a proposta pelo cockpit não tem data nem
    convidados: a folha vai direto pros itens, sem quatro travessões."""
    _, tok = _semear(pool, conta_id, evento={})
    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    html = prop.proposta_publica(None, tok).body.decode()
    assert "O evento" not in html and "Convidados" not in html
    assert "Itens do orçamento" in html and "R$ 7.450,00" in html   # o resto sai igual


# --------------------------------------------------------------- reserva na agenda
def test_assinar_reserva_a_data_na_agenda(pool, conta_id):
    _, tok = _semear(pool, conta_id)
    assert prop.registrar_assinatura(pool, tok, "Maria Teste", "", "1.2.3.4")
    d = prop._carregar(tok, pool=pool)
    ev_id = prop._reservar_na_agenda(d, pool=pool)
    assert ev_id
    with pool.connection() as c:
        titulo, inicio, fim, local, desc = c.execute(
            "select titulo, inicio, fim, local, descricao from eventos_agenda where id=%s",
            (ev_id,)).fetchone()
        gravado = c.execute("select evento_agenda_id from orcamentos where token=%s",
                            (tok,)).fetchone()[0]
    assert titulo == "Aniversário — Maria Teste"
    assert (inicio.astimezone(ag.BRT).day, inicio.astimezone(ag.BRT).hour) == (18, 19)
    assert (fim.astimezone(ag.BRT).day, fim.astimezone(ag.BRT).hour) == (19, 0)
    assert local == "Espaço 01" and "50 convidados" in desc
    assert gravado == ev_id


def test_reserva_nao_duplica(pool, conta_id):
    _, tok = _semear(pool, conta_id)
    d = prop._carregar(tok, pool=pool)
    primeiro = prop._reservar_na_agenda(d, pool=pool)
    d2 = prop._carregar(tok, pool=pool)          # já tem evento_agenda_id
    assert prop._reservar_na_agenda(d2, pool=pool) is None
    with pool.connection() as c:
        n = c.execute("select count(*) from eventos_agenda where id=%s", (primeiro,)).fetchone()[0]
    assert n == 1


def test_evento_sem_data_nao_marca_nada(pool, conta_id):
    _, tok = _semear(pool, conta_id, evento={"convidados": 30, "tipo": "Casamento"})
    d = prop._carregar(tok, pool=pool)
    assert prop._reservar_na_agenda(d, pool=pool) is None


def test_recorrente_nao_vai_pra_agenda(pool, conta_id):
    tok = "RC" + secrets.token_hex(6)
    with pool.connection() as c:
        c.execute(
            """insert into orcamentos (conta_id, empresa, itens, setup_centavos, status, token)
               values (%s,'Clínica','[]'::jsonb,920000,'enviado',%s)""", (conta_id, tok))
        c.commit()
    d = prop._carregar(tok, pool=pool)
    assert prop._reservar_na_agenda(d, pool=pool) is None


# --------------------------------------------------------------- fechar contrato
def test_fechar_evento_gera_um_titulo_por_parcela(pool, conta_id):
    oid, _tok = _semear(pool, conta_id)
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    assert r["ok"] and r["modo"] == "evento" and len(r["titulos"]) == 2
    with pool.connection() as c:
        linhas = c.execute(
            """select descricao, valor_centavos, vencimento, recorrente, tipo
                 from titulos where id = any(%s) order by vencimento""",
            (r["titulos"],)).fetchall()
    assert [l[1] for l in linhas] == [181000, 600000]
    assert [l[2] for l in linhas] == [date(2025, 11, 13), date(2025, 12, 13)]
    assert all(l[3] is False and l[4] == "receber" for l in linhas)   # evento não é recorrente
    assert "Sinal" in linhas[0][0] and "Maria Teste" in linhas[0][0]


def test_fechar_evento_sem_parcelas_gera_titulo_do_total(pool, conta_id):
    oid, _tok = _semear(pool, conta_id, parcelas=[])
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    assert r["ok"] and len(r["titulos"]) == 1
    with pool.connection() as c:
        valor, recorrente = c.execute(
            "select valor_centavos, recorrente from titulos where id=%s",
            (r["titulos"][0],)).fetchone()
    assert valor == 745000 and recorrente is False


def test_fechar_evento_duas_vezes_nao_duplica_titulos(pool, conta_id):
    oid, _tok = _semear(pool, conta_id)
    assert vendas.fechar_orcamento(pool, conta_id, oid)["ok"]
    segundo = vendas.fechar_orcamento(pool, conta_id, oid)
    assert not segundo["ok"] and "fechado" in segundo["erro"]
    with pool.connection() as c:
        n = c.execute("select count(*) from titulos where conta_id=%s and descricao like 'Evento%%'",
                      (conta_id,)).fetchone()[0]
    assert n == 2      # os 2 da primeira vez; a segunda não somou nada
