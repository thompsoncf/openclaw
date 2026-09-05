"""O Raio-X do vendedor (finance/raio_x): sua semana, responda hoje, fechamentos,
a confiança do dado, e a mensagem de segunda no grupo.

O módulo SÓ LÊ o que já existe — os testes montam o schema mínimo das tabelas
que ele lê (mesmo padrão de tests/test_cockpit.py) e aplicam a 207 (as duas
tabelas dele: a escolha do grupo e a trava de envio). Tudo com `agora` fixo, pra
o teste não depender do dia da semana em que roda.

Banco dedicado e descartável.
"""
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from psycopg_pool import ConnectionPool

from finance import raio_x as rx
from finance import raio_x_perfil as rxp

EVENTOS = rxp.perfil("eventos")
RECORRENTE = rxp.perfil("consultoria")

BRT = ZoneInfo("America/Sao_Paulo")
MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"

# segunda-feira, 07/09/2026, 10h em Brasília: a semana corrente começou às 0h de
# hoje; a "passada" é 31/08 a 06/09.
SEGUNDA_10H = datetime(2026, 9, 7, 10, 0, tzinfo=BRT)

_SQL = """
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  criado_em timestamptz not null default now());
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, status text default 'novo', evento_em date, evento_tipo text,
  orcamento_id bigint, segmento text, criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  contato_ref text, contato_nome text, criado_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  autor text default 'humano', membro_id bigint, texto text default '', provider_sid text,
  criado_em timestamptz default now());
create table orcamentos (id bigserial primary key, cliente text, status text default 'rascunho',
  primeiro_ano_centavos bigint default 0, criado_em timestamptz default now(),
  aprovada_em timestamptz, sinal_pago_em timestamptz);
create table contratos (id bigserial primary key, conta_id bigint, orcamento_id bigint,
  status text default 'enviado', valor_centavos bigint, assinado_em timestamptz,
  enviado_em timestamptz, criado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  titulo text, inicio timestamptz, status text default 'ativo', desfecho text);
create table wa_qr_log (id bigserial primary key, conta_id bigint, nivel text default 'warn',
  msg text not null default '', dados jsonb, criado_em timestamptz not null default now());
create table wa_decifra_diario (dia date not null, conta_id bigint not null, from_me boolean not null,
  ocorrencias int not null default 0, ids_distintos int not null default 0,
  chegaram int, nunca_chegaram int, correlacionado_em timestamptz,
  apurado_em timestamptz not null default now(), primary key (dia, conta_id, from_me));
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_raio_x_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute((MIG / "207_raio_x.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


# ------------------------------------------------------------------ montadores

def _conta(c, nome="Prime Festas"):
    return c.execute("insert into contas (nome) values (%s) returning id", (nome,)).fetchone()[0]


def _vend(c, conta, nome="Jaqueline Silva", ativo=True):
    return c.execute("insert into membros (conta_id, nome, papel, ativo) values (%s,%s,'vendedor',%s) returning id",
                     (conta, nome, ativo)).fetchone()[0]


def _lead(c, conta, vend, contato="Ana", status="novo", criado=None, evento_em=None, evento_tipo=None, orc=None):
    return c.execute("""insert into prospeccao (conta_id, vendedor_id, contato, status, criado_em, evento_em, evento_tipo, orcamento_id)
                        values (%s,%s,%s,%s,coalesce(%s, now()),%s,%s,%s) returning id""",
                     (conta, vend, contato, status, criado, evento_em, evento_tipo, orc)).fetchone()[0]


def _conversa(c, conta, lead, criado, ref="5586999990001", nome=None):
    return c.execute("""insert into conversas (conta_id, prospeccao_id, contato_ref, contato_nome, criado_em)
                        values (%s,%s,%s,%s,%s) returning id""", (conta, lead, ref, nome, criado)).fetchone()[0]


def _msg(c, conv, direcao, em, texto="oi", autor=None, sid=None):
    autor = autor or ("lead" if direcao == "in" else "humano")
    return c.execute("""insert into mensagens (conversa_id, direcao, autor, texto, provider_sid, criado_em)
                        values (%s,%s,%s,%s,%s,%s) returning id""", (conv, direcao, autor, texto, sid, em)).fetchone()[0]


def _orc(c, cliente, status="enviado", total=500000, criado=None, aprovada=None, sinal=None):
    return c.execute("""insert into orcamentos (cliente, status, primeiro_ano_centavos, criado_em, aprovada_em, sinal_pago_em)
                        values (%s,%s,%s,coalesce(%s, now()),%s,%s) returning id""",
                     (cliente, status, total, criado, aprovada, sinal)).fetchone()[0]


def _contrato(c, conta, orc, status="assinado", valor=500000, assinado=None):
    return c.execute("""insert into contratos (conta_id, orcamento_id, status, valor_centavos, assinado_em)
                        values (%s,%s,%s,%s,%s) returning id""", (conta, orc, status, valor, assinado)).fetchone()[0]


def _t(dias_atras: float, base: datetime = SEGUNDA_10H) -> datetime:
    return base - timedelta(days=dias_atras)


# ------------------------------------------------------------------ a janela

def test_janela_semana_comeca_na_segunda_e_passada_e_a_semana_fechada():
    ini, fim, rot = rx.janela("semana", SEGUNDA_10H)
    assert ini == datetime(2026, 9, 7, 0, 0, tzinfo=BRT) and fim == SEGUNDA_10H
    ini, fim, rot = rx.janela("passada", SEGUNDA_10H)
    assert ini == datetime(2026, 8, 31, 0, 0, tzinfo=BRT) and fim == datetime(2026, 9, 7, 0, 0, tzinfo=BRT)
    assert rot == "31/08 a 06/09"
    ini, _, _ = rx.janela("mes", SEGUNDA_10H)
    assert ini == datetime(2026, 9, 1, 0, 0, tzinfo=BRT)
    ini, _, _ = rx.janela("tudo", SEGUNDA_10H)
    assert (SEGUNDA_10H - ini).days == 365


def test_janela_no_meio_da_semana_ainda_comeca_na_segunda():
    quinta = datetime(2026, 9, 10, 15, 30, tzinfo=BRT)
    ini, fim, rot = rx.janela("semana", quinta)
    assert ini == datetime(2026, 9, 7, 0, 0, tzinfo=BRT) and fim == quinta
    assert rot == "07/09 a 10/09"


def test_fmt_min():
    assert rx.fmt_min(None) == "—"
    assert rx.fmt_min(3) == "3 min"
    assert rx.fmt_min(150) == "2h30"
    assert rx.fmt_min(60 * 72) == "3 dias"


# ------------------------------------------------------------------ sua semana

def test_sua_semana_mede_primeira_resposta_propostas_toques_e_contratos(pool):
    """Um vendedor, uma semana fechada (31/08 a 06/09):
      - Ana: perguntou, respondida em 3 min          → no alvo
      - Bia: perguntou, respondida em 2h             → fora do alvo
      - Caio: respondemos 1× e paramos há 3 dias     → parou na 1ª tentativa
        (Ana e Bia também: uma resposta nossa, e mais nada há mais de 24h)
      - Dora: respondemos, o cliente não voltou, insistimos (2º out) → 1 toque
      - proposta enviada na semana + 1 rascunho de 5 dias
      - contrato da Eva assinado na semana (R$ 8.000); Fabi aprovou e não assinou."""
    ini, fim, _ = rx.janela("passada", SEGUNDA_10H)
    with pool.connection() as c:
        conta = _conta(c); v = _vend(c, conta)
        ana = _lead(c, conta, v, "Ana", criado=_t(5))
        cv = _conversa(c, conta, ana, _t(5)); _msg(c, cv, "in", _t(5)); _msg(c, cv, "out", _t(5) + timedelta(minutes=3))
        bia = _lead(c, conta, v, "Bia", criado=_t(4))
        cv = _conversa(c, conta, bia, _t(4)); _msg(c, cv, "in", _t(4)); _msg(c, cv, "out", _t(4) + timedelta(hours=2))
        caio = _lead(c, conta, v, "Caio", status="contatado", criado=_t(3.5))
        cv = _conversa(c, conta, caio, _t(3.5)); _msg(c, cv, "in", _t(3.5)); _msg(c, cv, "out", _t(3))
        dora = _lead(c, conta, v, "Dora", status="contatado", criado=_t(6))
        cv = _conversa(c, conta, dora, _t(6)); _msg(c, cv, "in", _t(6)); _msg(c, cv, "out", _t(5.9)); _msg(c, cv, "out", _t(4))
        o1 = _orc(c, "Ana", "enviado", 600000, criado=_t(4.5))
        c.execute("update prospeccao set orcamento_id=%s, status='proposta' where id=%s", (o1, ana))
        o2 = _orc(c, "Bia", "rascunho", 300000, criado=_t(5))
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (o2, bia))
        eva = _lead(c, conta, v, "Eva", status="ganho", criado=_t(20))
        o3 = _orc(c, "Eva Lima", "fechado", 800000, criado=_t(10), aprovada=_t(8))
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (o3, eva))
        _contrato(c, conta, o3, "assinado", 800000, assinado=_t(2))
        fabi = _lead(c, conta, v, "Fabi", status="proposta", criado=_t(15))
        o4 = _orc(c, "Fabi Costa", "enviado", 450000, criado=_t(12), aprovada=_t(9))
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (o4, fabi))
        c.commit()

    s = rx.sua_semana(pool, conta, v, ini, fim)
    assert s["leads"] == 4                      # Ana, Bia, Caio, Dora (Eva e Fabi são de antes)
    # Caio (12h) e Dora (2h24) também tiveram 1ª resposta: 4 medidas, 1 no alvo
    assert s["primeira_n"] == 4 and s["primeira_em_5"] == 1
    assert s["primeira_min"] == round((120 + 144) / 2)   # mediana de 3, 120, 144, 720
    assert s["primeira_min_anterior"] is None
    assert s["propostas_enviadas"] == 1 and s["rascunhos"] == 1 and s["rascunho_dias"] == 4
    assert s["toques"] == 1                     # o 2º out da Dora
    assert s["paradas_1a"] == 3                 # Ana, Bia e Caio
    assert [x["nome"] for x in s["contratos"]] == ["Eva Lima"] and s["contratos_valor"] == 800000
    assert [x["nome"] for x in s["sem_assinar"]] == ["Fabi Costa"]
    assert s["sem_assinar"][0]["dias"] == 8   # aprovou sábado 10h; a semana fecha segunda 0h
    # as cores: 1ª resposta 62 min é ruim; rascunho de 5 dias é ruim; 1 parada é
    # amarelo; contrato assinado é verde
    assert rx.cor("primeira", s) == "ruim"
    assert rx.cor("propostas", s) == "ruim"
    assert rx.cor("toques", s) == "amb"
    assert rx.cor("contratos", s) == "ok"


def test_sua_semana_vazia_nao_quebra_e_fica_amarela(pool):
    ini, fim, _ = rx.janela("passada", SEGUNDA_10H)
    with pool.connection() as c:
        conta = _conta(c); v = _vend(c, conta, "Novo"); c.commit()
    s = rx.sua_semana(pool, conta, v, ini, fim)
    assert s["leads"] == 0 and s["primeira_min"] is None and s["contratos"] == [] and s["sem_assinar"] == []
    assert rx.cor("primeira", s) == "amb" and rx.cor("propostas", s) == "ok" and rx.cor("toques", s) == "ok"
    assert rx.cor("contratos", s) == "amb"


def test_sua_semana_compara_com_a_semana_anterior(pool):
    """Semana passada 2 min, retrasada 40 min: o comparativo diz que melhorou."""
    ini, fim, _ = rx.janela("passada", SEGUNDA_10H)
    with pool.connection() as c:
        conta = _conta(c); v = _vend(c, conta)
        a = _lead(c, conta, v, "A", criado=_t(3))
        cv = _conversa(c, conta, a, _t(3)); _msg(c, cv, "in", _t(3)); _msg(c, cv, "out", _t(3) + timedelta(minutes=2))
        b = _lead(c, conta, v, "B", criado=_t(10))
        cv = _conversa(c, conta, b, _t(10)); _msg(c, cv, "in", _t(10)); _msg(c, cv, "out", _t(10) + timedelta(minutes=40))
        c.commit()
    s = rx.sua_semana(pool, conta, v, ini, fim)
    assert s["primeira_min"] == 2 and s["primeira_min_anterior"] == 40


def test_resposta_do_bot_nao_conta_como_primeira_resposta(pool):
    """A meta é do VENDEDOR: a IA respondendo em 0 min não é o vendedor no alvo."""
    ini, fim, _ = rx.janela("passada", SEGUNDA_10H)
    with pool.connection() as c:
        conta = _conta(c); v = _vend(c, conta)
        a = _lead(c, conta, v, "A", criado=_t(3))
        cv = _conversa(c, conta, a, _t(3)); _msg(c, cv, "in", _t(3))
        _msg(c, cv, "out", _t(3) + timedelta(seconds=5), autor="bot")
        _msg(c, cv, "out", _t(3) + timedelta(minutes=30), autor="humano")
        c.commit()
    s = rx.sua_semana(pool, conta, v, ini, fim)
    assert s["primeira_min"] == 30 and s["primeira_em_5"] == 0


# ------------------------------------------------------------------ responda hoje

def test_responda_hoje_quatro_faixas_na_ordem_e_um_lead_por_faixa(pool):
    """  Gil: perguntou "qual o valor?" há 2h                   → pergunta
         Hugo: disse "obrigada!" (despedida)                     → NÃO é pergunta
         Iva: festa em 20 dias, sem proposta                     → festa
         Jô: respondemos há 2 dias, 1 toque feito (venceu o 2º)  → toque
         Kim: respondemos há 1h, 1 toque (o 2º vence em 24h)     → ainda não
         Lia: visita marcada pra amanhã                          → visita
         Mel: lead ganho                                         → fora (não está em jogo)
       Gil também tem festa em 10 dias: entra UMA vez, na pergunta."""
    hoje = SEGUNDA_10H.date()
    with pool.connection() as c:
        conta = _conta(c); v = _vend(c, conta)
        gil = _lead(c, conta, v, "Gil", evento_em=hoje + timedelta(days=10), evento_tipo="15 anos")
        cv = _conversa(c, conta, gil, _t(1)); _msg(c, cv, "out", _t(1)); _msg(c, cv, "in", _t(2 / 24), "qual o valor?")
        hugo = _lead(c, conta, v, "Hugo", status="contatado")
        cv = _conversa(c, conta, hugo, _t(1)); _msg(c, cv, "out", _t(1)); _msg(c, cv, "in", _t(0.5), "Obrigada!!")
        iva = _lead(c, conta, v, "Iva", status="qualificado", evento_em=hoje + timedelta(days=20), evento_tipo="casamento")
        jo = _lead(c, conta, v, "Jô", status="contatado")
        cv = _conversa(c, conta, jo, _t(3)); _msg(c, cv, "in", _t(3)); _msg(c, cv, "out", _t(2))
        kim = _lead(c, conta, v, "Kim", status="contatado")
        cv = _conversa(c, conta, kim, _t(1)); _msg(c, cv, "in", _t(1)); _msg(c, cv, "out", _t(1 / 24))
        lia = _lead(c, conta, v, "Lia", status="qualificado")
        c.execute("insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio) values (%s,%s,'Visita Lia',%s)",
                  (conta, lia, datetime(2026, 9, 8, 15, 0, tzinfo=BRT)))
        mel = _lead(c, conta, v, "Mel", status="ganho")
        cv = _conversa(c, conta, mel, _t(1)); _msg(c, cv, "in", _t(0.1), "e aí, quando fechamos?")
        c.commit()

    h = rx.responda_hoje(pool, conta, v, SEGUNDA_10H, perfil=EVENTOS)
    assert [(i["faixa"], i["nome"]) for i in h["itens"]] == [
        ("pergunta", "Gil"), ("festa", "Iva"), ("toque", "Jô"), ("visita", "Lia")]
    assert h["n"] == 4 and h["por_faixa"] == {"pergunta": 1, "festa": 1, "proposta": 0, "toque": 1, "visita": 1}
    assert h["sem_urgencia"] == 2          # Hugo e Kim seguem na Fila, sem urgência
    por = {i["nome"]: i for i in h["itens"]}
    assert "qual o valor?" in por["Gil"]["detalhe"] and "2h" in por["Gil"]["detalhe"] and por["Gil"]["acao"] == "responder"
    assert por["Gil"]["href"] == f"/cockpit/lead/{gil}"
    assert "casamento" in por["Iva"]["detalhe"] and "em 20 dias" in por["Iva"]["detalhe"] and por["Iva"]["acao"] == "proposta"
    assert por["Jô"]["acao"] == "2º toque" and "1 toque(s)" in por["Jô"]["detalhe"]
    assert por["Lia"]["acao"] == "confirmar" and "15:00" in por["Lia"]["detalhe"]


def test_responda_hoje_perguntas_mais_antigas_primeiro_e_4o_toque_vira_porta_aberta(pool):
    with pool.connection() as c:
        conta = _conta(c); v = _vend(c, conta)
        a = _lead(c, conta, v, "Antiga"); cv = _conversa(c, conta, a, _t(3)); _msg(c, cv, "in", _t(2), "e o pacote?")
        b = _lead(c, conta, v, "Recente"); cv = _conversa(c, conta, b, _t(1)); _msg(c, cv, "in", _t(0.1), "tem data?")
        # 3 toques feitos, o último há 8 dias: o 4º é a "porta aberta"
        p = _lead(c, conta, v, "Parado", status="contatado")
        cv = _conversa(c, conta, p, _t(20)); _msg(c, cv, "in", _t(20))
        _msg(c, cv, "out", _t(19)); _msg(c, cv, "out", _t(15)); _msg(c, cv, "out", _t(8))
        # 4 toques feitos: já é parado de vez, sai da fila de hoje
        q = _lead(c, conta, v, "Esgotado", status="contatado")
        cv = _conversa(c, conta, q, _t(30)); _msg(c, cv, "in", _t(30))
        for d in (29, 25, 20, 10):
            _msg(c, cv, "out", _t(d))
        c.commit()
    h = rx.responda_hoje(pool, conta, v, SEGUNDA_10H, perfil=EVENTOS)
    nomes = [i["nome"] for i in h["itens"]]
    assert nomes == ["Antiga", "Recente", "Parado"]
    assert [i for i in h["itens"] if i["nome"] == "Parado"][0]["acao"] == "porta aberta"
    assert h["sem_urgencia"] == 1


def test_responda_hoje_visita_com_desfecho_ou_cancelada_nao_entra(pool):
    with pool.connection() as c:
        conta = _conta(c); v = _vend(c, conta)
        a = _lead(c, conta, v, "A")
        c.execute("insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio, desfecho) values (%s,%s,'x',%s,'realizado')",
                  (conta, a, datetime(2026, 9, 8, 9, 0, tzinfo=BRT)))
        c.execute("insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio, status) values (%s,%s,'x',%s,'cancelado')",
                  (conta, a, datetime(2026, 9, 8, 11, 0, tzinfo=BRT)))
        c.commit()
    assert rx.responda_hoje(pool, conta, v, SEGUNDA_10H, perfil=EVENTOS)["itens"] == []


# ------------------------------------------------------------------ fechamentos

def test_fechamentos_quatro_listas_e_o_valor_em_jogo(pool):
    with pool.connection() as c:
        conta = _conta(c); v = _vend(c, conta)
        # assinou há 3 dias, sinal pago
        a = _lead(c, conta, v, "Ana", status="ganho", evento_em=date(2026, 11, 20), evento_tipo="casamento")
        oa = _orc(c, "Ana Souza", "fechado", 1000000, criado=_t(10), aprovada=_t(6), sinal=_t(4))
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (oa, a)); _contrato(c, conta, oa, "assinado", 1000000, _t(3))
        # aprovou há 5 dias, contrato enviado sem assinatura
        b = _lead(c, conta, v, "Bia", status="proposta")
        ob = _orc(c, "Bia Reis", "enviado", 700000, criado=_t(9), aprovada=_t(5))
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (ob, b)); _contrato(c, conta, ob, "enviado", 700000)
        # proposta enviada há 4 dias, 2 toques depois da última do cliente
        d = _lead(c, conta, v, "Dan", status="proposta")
        od = _orc(c, "Dan Melo", "enviado", 300000, criado=_t(4))
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (od, d))
        cv = _conversa(c, conta, d, _t(5)); _msg(c, cv, "in", _t(5)); _msg(c, cv, "out", _t(4)); _msg(c, cv, "out", _t(2))
        # rascunho de 6 dias, festa em 12 dias: urgente
        e = _lead(c, conta, v, "Edu", status="qualificado", evento_em=SEGUNDA_10H.date() + timedelta(days=12), evento_tipo="aniversário")
        oe = _orc(c, "Edu Paz", "rascunho", 200000, criado=_t(6))
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (oe, e))
        # assinado há 40 dias: já saiu do "assinou" (só 30 dias)
        f = _lead(c, conta, v, "Fim", status="ganho")
        of = _orc(c, "Fim", "fechado", 100000, criado=_t(50), aprovada=_t(45))
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (of, f)); _contrato(c, conta, of, "assinado", 100000, _t(40))
        c.commit()
    f_ = rx.fechamentos(pool, conta, v, SEGUNDA_10H)
    assert [x["nome"] for x in f_["assinou"]] == ["Ana Souza"] and "sinal pago" in f_["assinou"][0]["detalhe"]
    assert f_["assinou"][0]["festa"] == "casamento 20/11"
    assert [x["nome"] for x in f_["falta_assinar"]] == ["Bia Reis"] and f_["falta_assinar"][0]["dias"] == 5
    assert f_["falta_assinar"][0]["acao"] == "reenviar o contrato"
    assert [x["nome"] for x in f_["esperando"]] == ["Dan Melo"]
    assert f_["esperando"][0]["toques"] == 2 and f_["esperando"][0]["acao"] == "3º toque"
    assert [x["nome"] for x in f_["rascunhos"]] == ["Edu Paz"] and f_["rascunhos"][0]["urgente"] is True
    assert "12 dias" in f_["rascunhos"][0]["detalhe"] and f_["rascunhos"][0]["acao"] == "enviar agora"
    assert f_["em_jogo_centavos"] == 700000 + 300000 + 200000


# ------------------------------------------------------------------ confiança do dado

def _log(c, conta, msg, dados, em):
    c.execute("insert into wa_qr_log (conta_id, msg, dados, criado_em) values (%s,%s,%s::jsonb,%s)",
              (conta, msg, dados, em))


def test_confianca_conta_dias_religadas_e_quem_pode_nao_ter_chegado(pool):
    """Duas falhas de decifragem de cliente nas últimas 48h: a do 0001 chegou
    depois (provider_sid bate) — não conta; a do 0002 nunca chegou — conta, pelo
    nome do lead. Eco (fromMe true), grupo e status são ruído e ficam fora."""
    import json
    ini, fim, _ = rx.janela("passada", SEGUNDA_10H)
    with pool.connection() as c:
        conta = _conta(c); v = _vend(c, conta)
        a = _lead(c, conta, v, "Rô Rodrigues"); cv = _conversa(c, conta, a, _t(5), ref="5586999990001")
        _msg(c, cv, "in", _t(5)); _msg(c, cv, "in", _t(1), sid="ID-CHEGOU")
        b = _lead(c, conta, v, "Amanda M"); _conversa(c, conta, b, _t(3), ref="5586999990002")
        _log(c, conta, "conexão fechou", "{}", _t(0.5)); _log(c, conta, "conexão fechou", "{}", _t(1))
        _log(c, conta, "conexão fechou", "{}", _t(5))          # fora das 48h
        def falha(id_, jid, from_me="false"):
            return json.dumps({"key": {"id": id_, "remoteJid": jid, "fromMe": from_me}})
        _log(c, conta, "failed to decrypt message", falha("ID-CHEGOU", "5586999990001@s.whatsapp.net"), _t(1))
        _log(c, conta, "failed to decrypt message", falha("ID-SUMIU", "5586999990002@s.whatsapp.net"), _t(0.3))
        _log(c, conta, "failed to decrypt message", falha("ID-SUMIU", "5586999990002@s.whatsapp.net"), _t(0.29))  # retry, mesmo id
        _log(c, conta, "failed to decrypt message", falha("ID-ECO", "5586999990002@s.whatsapp.net", "true"), _t(0.2))
        _log(c, conta, "failed to decrypt message", falha("ID-GRUPO", "1203630@g.us"), _t(0.2))
        _log(c, conta, "failed to decrypt message", falha("ID-ST", "status@broadcast"), _t(0.2))
        c.execute("insert into wa_decifra_diario (dia, conta_id, from_me, nunca_chegaram) values (%s,%s,false,3)",
                  (date(2026, 9, 2), conta))
        c.commit()
    cf = rx.confianca(pool, conta, ini, fim, SEGUNDA_10H)
    assert cf["dias_periodo"] == 8 and cf["dias_medidos"] == 2   # _t(5) e _t(1) caem na semana passada
    assert cf["religou"] == 2
    assert cf["nao_chegaram"] == [{"numero": "5586999990002", "nome": "Amanda M", "mensagens": 1}]
    assert cf["nao_chegaram_fechados"] == 3
    t = rx.texto_confianca(cf)
    assert "2 de 8 dias medidos" in t and "religou 2×" in t
    assert "4 mensagem(ns) pode(m) não ter chegado (Amanda M): confira no celular" in t


def test_confianca_sem_nada_diz_nenhuma_perdida(pool):
    ini, fim, _ = rx.janela("passada", SEGUNDA_10H)
    with pool.connection() as c:
        conta = _conta(c); c.commit()
    cf = rx.confianca(pool, conta, ini, fim, SEGUNDA_10H)
    assert cf["religou"] == 0 and cf["nao_chegaram"] == []
    assert rx.texto_confianca(cf).endswith("nenhuma mensagem perdida")


# ------------------------------------------------------------------ o grupo

def test_texto_grupo_uma_linha_por_vendedor_ativo_a_empresa_e_a_confianca(pool):
    ini, fim, rot = rx.janela("passada", SEGUNDA_10H)
    with pool.connection() as c:
        conta = _conta(c, "Prime Festas")
        c.execute("update contas set nome_fantasia='Prime' where id=%s", (conta,))
        j = _vend(c, conta, "Jaqueline Silva"); p = _vend(c, conta, "Pedro Yan")
        _vend(c, conta, "Saiu Ontem", ativo=False)
        c.execute("insert into membros (conta_id, nome, papel) values (%s,'Dono','dono')", (conta,))
        a = _lead(c, conta, j, "Ana", criado=_t(3))
        cv = _conversa(c, conta, a, _t(3)); _msg(c, cv, "in", _t(3)); _msg(c, cv, "out", _t(3) + timedelta(minutes=4))
        _msg(c, cv, "in", _t(0.1), "e o valor?")
        o = _orc(c, "Ana", "rascunho", 250000, criado=_t(2)); c.execute("update prospeccao set orcamento_id=%s where id=%s", (o, a))
        b = _lead(c, conta, p, "Bia", status="ganho", criado=_t(9))
        ob = _orc(c, "Bia Reis", "fechado", 900000, criado=_t(8), aprovada=_t(7))
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (ob, b)); _contrato(c, conta, ob, "assinado", 900000, _t(1))
        c.commit()
    t = rx.texto_grupo(pool, conta, ini, fim, SEGUNDA_10H)
    linhas = t.split("\n")
    assert linhas[0] == f"🔎 *Raio-X da semana · {rot}*"
    assert any(l.startswith("*Jaqueline* · 1 leads · 1ª resposta 4 min (1 em 5 min) · propostas 0 enviada(s), *1 rascunho* ⚠️ · responda hoje: *1*") for l in linhas)
    assert any(l.startswith("*Pedro* · 0 leads") and l.endswith("contrato: Bia 🎉") for l in linhas)
    assert "Saiu" not in t and "Dono" not in t
    emp = [l for l in linhas if l.startswith("*Prime na semana:*")][0]
    assert "1 leads" in emp and "1ª resposta 4 min (meta 5 min)" in emp and "1 em rascunho" in emp
    assert "1 contrato(s) (R$ 9.000)" in emp and "R$ 2.500 em propostas abertas" in emp
    assert linhas[-1].startswith("📡 ") and "nenhuma mensagem perdida" in linhas[-1]


def test_definir_recusa_jid_que_nao_e_grupo_e_config_le_o_que_gravou(pool):
    with pool.connection() as c:
        conta = _conta(c); c.commit()
    assert rx.config(pool, conta) is None
    assert rx.definir(pool, conta, "5586999990000@s.whatsapp.net", "x", True) == {"ok": False, "erro": "grupo_invalido"}
    assert rx.config(pool, conta) is None
    assert rx.definir(pool, conta, "120363012345@g.us", "Vendas Prime", True) == {"ok": True}
    cfg = rx.config(pool, conta)
    assert cfg["grupo_jid"] == "120363012345@g.us" and cfg["grupo_nome"] == "Vendas Prime" and cfg["ativo"] is True
    assert cfg["ultimo"] is None
    # desligar mantém o grupo escolhido
    assert rx.definir(pool, conta, "120363012345@g.us", "Vendas Prime", False) == {"ok": True}
    assert rx.config(pool, conta)["ativo"] is False
    # sem grupo (limpou) também vale: fica "salvo, sem onde chegar"
    assert rx.definir(pool, conta, "", "", True) == {"ok": True}
    assert rx.config(pool, conta)["grupo_jid"] is None


def _fake_envio(ok=True, erro=None):
    chamadas = []

    def enviar(pool, conta_id, jid, texto):
        chamadas.append((conta_id, jid, texto))
        return {"ok": ok} if ok else {"ok": False, "erro": erro or "desconectado"}
    enviar.chamadas = chamadas
    return enviar


def test_rodar_so_na_segunda_a_partir_das_8h_e_uma_vez_por_semana(pool):
    with pool.connection() as c:
        conta = _conta(c, "Prime"); _vend(c, conta, "Jaqueline"); c.commit()
    rx.definir(pool, conta, "120363099@g.us", "Vendas", True)
    env = _fake_envio()
    # terça: nada. Segunda 7h59: nada.
    assert rx.rodar(pool, datetime(2026, 9, 8, 10, 0, tzinfo=BRT), env) == 0
    assert rx.rodar(pool, datetime(2026, 9, 7, 7, 59, tzinfo=BRT), env) == 0
    assert env.chamadas == []
    # segunda 8h: manda a semana passada, uma vez
    assert rx.rodar(pool, datetime(2026, 9, 7, 8, 0, tzinfo=BRT), env) == 1
    assert len(env.chamadas) == 1
    conta_id, jid, texto = env.chamadas[0]
    assert conta_id == conta and jid == "120363099@g.us"
    assert texto.startswith("🔎 *Raio-X da semana · 31/08 a 06/09*") and "*Jaqueline*" in texto
    # o segundo worker, 2 min depois: a linha já existe, não manda de novo
    assert rx.rodar(pool, datetime(2026, 9, 7, 8, 2, tzinfo=BRT), env) == 0
    assert rx.rodar(pool, datetime(2026, 9, 7, 18, 0, tzinfo=BRT), env) == 0
    assert len(env.chamadas) == 1
    cfg = rx.config(pool, conta)
    assert cfg["ultimo"]["semana"] == date(2026, 9, 7) and cfg["ultimo"]["enviado_em"] is not None
    assert cfg["ultimo"]["erro"] is None
    with pool.connection() as c:
        assert c.execute("select texto from raio_x_envios where conta_id=%s", (conta,)).fetchone()[0] == texto
    # a segunda seguinte é outra semana: manda de novo
    assert rx.rodar(pool, datetime(2026, 9, 14, 8, 30, tzinfo=BRT), env) == 1
    assert env.chamadas[1][2].startswith("🔎 *Raio-X da semana · 07/09 a 13/09*")


def test_rodar_ignora_conta_desligada_ou_sem_grupo(pool):
    with pool.connection() as c:
        c1 = _conta(c, "Desligada"); c2 = _conta(c, "Sem grupo"); c3 = _conta(c, "Sem config"); c.commit()
    rx.definir(pool, c1, "120363001@g.us", "x", False)
    rx.definir(pool, c2, "", "", True)
    env = _fake_envio()
    assert rx.rodar(pool, datetime(2026, 9, 7, 9, 0, tzinfo=BRT), env) == 0 or all(
        ch[0] not in (c1, c2, c3) for ch in env.chamadas)
    assert all(ch[0] not in (c1, c2, c3) for ch in env.chamadas)


def test_rodar_falha_guarda_o_erro_e_tenta_de_novo_so_depois_de_10_min(pool):
    with pool.connection() as c:
        conta = _conta(c, "Falha"); _vend(c, conta, "V"); c.commit()
    rx.definir(pool, conta, "120363777@g.us", "x", True)
    ruim = _fake_envio(ok=False, erro="desconectado")
    seg = datetime(2026, 9, 21, 8, 0, tzinfo=BRT)
    assert rx.rodar(pool, seg, ruim) == 0
    assert [ch[0] for ch in ruim.chamadas].count(conta) == 1
    cfg = rx.config(pool, conta)
    assert cfg["ultimo"]["enviado_em"] is None and cfg["ultimo"]["erro"] == "desconectado"
    # 2 min depois: cedo demais, não tenta
    assert rx.rodar(pool, seg + timedelta(minutes=2), ruim) == 0
    assert [ch[0] for ch in ruim.chamadas].count(conta) == 1
    # a folga de 10 min passou (o relógio da trava é o do banco: recua na mão)
    with pool.connection() as c:
        c.execute("update raio_x_envios set atualizado_em = now() - interval '11 minutes' where conta_id=%s", (conta,)); c.commit()
    bom = _fake_envio()
    assert rx.rodar(pool, seg + timedelta(minutes=12), bom) == 1
    assert [ch[0] for ch in bom.chamadas] == [conta]
    with pool.connection() as c:
        tent, env_em, erro = c.execute("select tentativas, enviado_em, erro from raio_x_envios where conta_id=%s", (conta,)).fetchone()
    assert tent == 2 and env_em is not None and erro is None


def test_rodar_desiste_depois_de_5_tentativas(pool):
    with pool.connection() as c:
        conta = _conta(c, "Teimosa"); _vend(c, conta, "V"); c.commit()
    rx.definir(pool, conta, "120363778@g.us", "x", True)
    ruim = _fake_envio(ok=False)
    seg = datetime(2026, 9, 28, 8, 0, tzinfo=BRT)
    for _ in range(6):
        rx.rodar(pool, seg, ruim)
        with pool.connection() as c:
            c.execute("update raio_x_envios set atualizado_em = now() - interval '11 minutes' where conta_id=%s", (conta,)); c.commit()
    assert [ch[0] for ch in ruim.chamadas].count(conta) == 5


def test_rodar_quando_texto_estoura_grava_o_erro_e_nao_derruba_o_ticker(pool, monkeypatch):
    with pool.connection() as c:
        conta = _conta(c, "Quebra"); _vend(c, conta, "V"); c.commit()
    rx.definir(pool, conta, "120363779@g.us", "x", True)

    def estoura(*a, **k):
        raise RuntimeError("tabela sumiu")
    monkeypatch.setattr(rx, "texto_grupo", estoura)
    env = _fake_envio()
    assert rx.rodar(pool, datetime(2026, 10, 5, 8, 0, tzinfo=BRT), env) == 0
    assert env.chamadas == []
    assert rx.config(pool, conta)["ultimo"]["erro"] == "RuntimeError: tabela sumiu"


def test_enviar_agora_manda_a_semana_corrente_sem_passar_pela_trava(pool):
    with pool.connection() as c:
        conta = _conta(c, "Agora"); _vend(c, conta, "V"); c.commit()
    env = _fake_envio()
    assert rx.enviar_agora(pool, conta, SEGUNDA_10H, env) == {"ok": False, "erro": "sem_grupo"}
    rx.definir(pool, conta, "120363555@g.us", "x", False)      # desligado ainda deixa testar
    r = rx.enviar_agora(pool, conta, SEGUNDA_10H, env)
    assert r["ok"] is True and r["texto"].startswith("🔎 *Raio-X da semana · 07/09 a 07/09 (até agora)*")
    assert env.chamadas[0][1] == "120363555@g.us"
    # não deixa rastro na trava de segunda
    with pool.connection() as c:
        assert c.execute("select count(*) from raio_x_envios where conta_id=%s", (conta,)).fetchone()[0] == 0


def test_envio_padrao_so_pelo_whatsapp_proprio(pool, monkeypatch):
    """Twilio e Cloud API não falam com grupo: o envio padrão recusa antes de
    tentar. No QR, vai pelo `whatsapp_out.enviar` normal, com o jid do grupo."""
    from finance import whatsapp_out as wo
    monkeypatch.setattr(wo, "provedor_da_conta", lambda c, conta_id: "twilio")
    assert rx._enviar_padrao(pool, 1, "120363@g.us", "x") == {"ok": False, "erro": "so_numero_proprio"}
    capt = {}
    monkeypatch.setattr(wo, "provedor_da_conta", lambda c, conta_id: "qr")
    monkeypatch.setattr(wo, "enviar", lambda c, conta_id, numero, texto, chip_id=None: capt.update(numero=numero, texto=texto) or {"ok": True})
    assert rx._enviar_padrao(pool, 1, "120363@g.us", "oi") == {"ok": True}
    assert capt == {"numero": "120363@g.us", "texto": "oi"}


def test_migracao_207_e_idempotente(pool):
    with pool.connection() as c:
        c.execute((MIG / "207_raio_x.sql").read_text(encoding="utf-8"))
        c.commit()


def test_responda_hoje_no_recorrente_troca_festa_por_proposta_parada_e_visita_por_reuniao(pool):
    """Perfil recorrente (a ZAQ): a festa perto não existe, a proposta enviada
    sem resposta há 3 dias entra como faixa própria, e o compromisso de amanhã
    se chama reunião. O mesmo cenário no perfil eventos tem a festa e a visita."""
    hoje = SEGUNDA_10H.date()
    with pool.connection() as c:
        conta = _conta(c, "ZAQ"); v = _vend(c, conta, "Vend")
        # festa em 20 dias sem proposta: só entra no perfil eventos
        festa = _lead(c, conta, v, "Festa", status="qualificado", evento_em=hoje + timedelta(days=20), evento_tipo="casamento")
        # proposta enviada há 9 dias, nossa última mensagem há 4 dias, sem resposta
        cli = _lead(c, conta, v, "Clínica Sorriso", status="proposta")
        o = _orc(c, "Clínica Sorriso", "enviado", 89000, criado=_t(9))
        c.execute("update prospeccao set orcamento_id=%s where id=%s", (o, cli))
        cv = _conversa(c, conta, cli, _t(10)); _msg(c, cv, "in", _t(10)); _msg(c, cv, "out", _t(9)); _msg(c, cv, "out", _t(4))
        # reunião amanhã
        est = _lead(c, conta, v, "Estética Vila", status="qualificado")
        c.execute("insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio) values (%s,%s,'Reunião',%s)",
                  (conta, est, datetime(2026, 9, 8, 10, 0, tzinfo=BRT)))
        c.commit()
    h = rx.responda_hoje(pool, conta, v, SEGUNDA_10H, perfil=RECORRENTE)
    assert [(i["faixa"], i["nome"]) for i in h["itens"]] == [("proposta", "Clínica Sorriso"), ("visita", "Estética Vila")]
    por = {i["nome"]: i for i in h["itens"]}
    assert por["Clínica Sorriso"]["detalhe"] == "proposta de R$ 890 enviada há 9 dia(s) · sem resposta"
    assert por["Clínica Sorriso"]["acao"] == "3º toque"          # 2 nossas depois da última dele
    assert por["Estética Vila"]["detalhe"].startswith("reunião amanhã 10:00")
    assert h["sem_urgencia"] == 1                                # a festa fica na Fila, sem urgência
    assert "festa" not in {i["faixa"] for i in h["itens"]}
    # o mesmo cenário em eventos: a festa entra, a visita se chama visita
    h2 = rx.responda_hoje(pool, conta, v, SEGUNDA_10H, perfil=EVENTOS)
    assert [(i["faixa"], i["nome"]) for i in h2["itens"]] == [("festa", "Festa"), ("proposta", "Clínica Sorriso"), ("visita", "Estética Vila")]
    assert h2["itens"][-1]["detalhe"].startswith("visita amanhã")


def test_sua_semana_conta_o_segmento_em_familias(pool):
    ini, fim, _ = rx.janela("passada", SEGUNDA_10H)
    with pool.connection() as c:
        conta = _conta(c, "Seg"); v = _vend(c, conta, "V")
        for seg in ("Loja", "Comércio varejista de calçados", "Atividade odontológica", "", None):
            lid = _lead(c, conta, v, "x", criado=_t(3))
            c.execute("update prospeccao set segmento=%s where id=%s", (seg, lid))
        c.commit()
    s = rx.sua_semana(pool, conta, v, ini, fim)
    assert s["leads"] == 5 and s["leads_sem_segmento"] == 2
    assert s["segmentos"] == [{"rotulo": "Loja / comércio", "n": 2}, {"rotulo": "Clínica / saúde", "n": 1}]
