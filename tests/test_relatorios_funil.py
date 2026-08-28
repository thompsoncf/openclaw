"""Relatórios › Funil: os quatro indicadores do consultor, e a cobertura de cada um.

Em 26/08/2026 o dono trouxe a orientação de um consultor pedindo taxa de conversão
em cada degrau — qualificado → visita agendada → realizada → sinal pago. Medindo
contra o banco apareceu o problema real: das 8 visitas que já tinham acontecido na
conta 34, **5 estavam sem resposta**. "2 de 3 apareceram" pareceria 67% de
comparecimento; a verdade é que ninguém sabe de 5.

Daí as duas regras que este arquivo guarda, e que são fáceis de escrever errado:

1. **Desfecho ausente NUNCA é "não apareceu".** Uma é o cliente que faltou, a outra
   é o vendedor que não respondeu. Confundi-las inventa um no-show que não houve —
   justamente o número que a gestão vai usar pra decidir.
2. **Toda taxa sai com a cobertura.** Abaixo de metade da amostra ela é marcada
   como pouco confiável, em vez de sair limpa e virar decisão em cima de anedota.
"""
import os
import re
from datetime import datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

import web.painel_relatorios as rel
from finance import vendas

_BASE_SQL = """
create table contas (id bigserial primary key, nome text, chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table orcamentos (id bigserial primary key, conta_id bigint, numero int,
  sinal_pago_em timestamptz);
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  whatsapp text, telefone text, criado_em timestamptz not null default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  criado_em timestamptz not null default now());
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  autor text, criado_em timestamptz not null default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, status text default 'ativo', desfecho text,
  tipo_evento text, prospeccao_id bigint, criado_em timestamptz not null default now(),
  -- as colunas que a agenda do Cockpit lê junto (finance/cockpit.agenda_da_conta)
  local text, ics_token text, pre_reserva_ate timestamptz,
  hora_sugerida boolean default false, convidados int, sinal_centavos int);
"""

#: base de tempo fixa — "já passou" tem que ser decidido pelo dado, não pelo relógio
#: de quem roda o teste.
AGORA = datetime.now(timezone.utc)
ONTEM = AGORA - timedelta(days=1)
AMANHA = AGORA + timedelta(days=1)


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_relatorios_funil_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


@pytest.fixture
def cen(pool):
    with pool.connection() as c:
        c.execute("truncate contas, membros, orcamentos, prospeccao, conversas, "
                  "mensagens, eventos_agenda restart identity")
        conta = c.execute("insert into contas (nome) values ('Prime') "
                          "returning id").fetchone()[0]
        pedro = c.execute("insert into membros (conta_id, nome) values (%s,'Pedro') "
                          "returning id", (conta,)).fetchone()[0]
        c.commit()
    return {"conta": conta, "pedro": pedro}


def _lead(pool, conta, nome, *, chegou=None):
    """Lead com conversa — é assim que ele conta no topo do funil."""
    with pool.connection() as c:
        pid = c.execute("insert into prospeccao (conta_id, empresa) values (%s,%s) "
                        "returning id", (conta, nome)).fetchone()[0]
        cid = c.execute("insert into conversas (conta_id, prospeccao_id) values (%s,%s) "
                        "returning id", (conta, pid)).fetchone()[0]
        if chegou is not None:
            c.execute("insert into mensagens (conversa_id, direcao, autor, criado_em) "
                      "values (%s,'in','lead',%s)", (cid, chegou))
        c.commit()
    return pid


def _visita(pool, conta, *, titulo="Visita — Fulano", quando=None, desfecho=None,
            status="ativo", lead=None, membro=None, tipo_evento=None, criada=None):
    with pool.connection() as c:
        vid = c.execute(
            """insert into eventos_agenda (conta_id, membro_id, titulo, inicio, status,
                 desfecho, tipo_evento, prospeccao_id, criado_em)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta, membro, titulo, quando or ONTEM, status, desfecho, tipo_evento,
             lead, criada or (quando or ONTEM))).fetchone()[0]
        c.commit()
    return vid


def _rel(pool, conta, *, status="", vendedor="", q=""):
    return rel._dados_funil(pool, conta, "todos", status, vendedor, q)


def _metrica(d, prefixo):
    return next(v for k, v in d["metricas"] if k.startswith(prefixo))


# ------------------------------------------- silêncio não é "não apareceu"

def test_visita_passada_sem_desfecho_diz_que_ninguem_respondeu(pool, cen):
    _visita(pool, cen["conta"], quando=ONTEM, desfecho=None)
    l = _rel(pool, cen["conta"])["linhas"][0]
    assert l["desfecho"] == "ninguém respondeu" and l["desfecho_cor"] == "erro"


def test_sem_resposta_nunca_entra_como_nao_apareceu_na_taxa(pool, cen):
    """O erro que este arquivo existe pra impedir: 1 apareceu e 3 em silêncio não é
    25% de comparecimento — é 100% das respondidas, com 3 sem resposta."""
    _visita(pool, cen["conta"], titulo="Visita — A", quando=ONTEM, desfecho="realizado")
    for n in "BCD":
        _visita(pool, cen["conta"], titulo=f"Visita — {n}", quando=ONTEM, desfecho=None)
    m = _metrica(_rel(pool, cen["conta"]), "Compareceram")
    assert m.startswith("100%"), m
    assert "1 de 4 respondidas — faltam 3" in m


def test_nao_apareceu_de_verdade_conta_como_ausencia(pool, cen):
    _visita(pool, cen["conta"], titulo="Visita — A", quando=ONTEM, desfecho="realizado")
    _visita(pool, cen["conta"], titulo="Visita — B", quando=ONTEM, desfecho="nao_realizado")
    m = _metrica(_rel(pool, cen["conta"]), "Compareceram")
    assert m.startswith("50%"), m
    assert "todas as 2 respondidas" in m


# ----------------------------------------------- o que conta como visita

def test_visita_cancelada_nao_conta_como_agendada(pool, cen):
    _visita(pool, cen["conta"], titulo="Visita — Viva", quando=ONTEM)
    _visita(pool, cen["conta"], titulo="Visita — Morta", quando=ONTEM, status="cancelado")
    assert [l["lead"] for l in _rel(pool, cen["conta"])["linhas"]] == ["Viva"]


def test_a_festa_do_cliente_nao_e_visita(pool, cen):
    """`tipo_evento` preenchido é a FESTA (Casamento, Locação). Perguntar "o cliente
    apareceu?" pro próprio casamento dele não faz sentido — e contaria como visita
    não realizada, inflando o no-show."""
    _visita(pool, cen["conta"], titulo="Visita — Real", quando=ONTEM)
    _visita(pool, cen["conta"], titulo="Visita — Casamento", quando=ONTEM,
            tipo_evento="Casamento")
    assert [l["lead"] for l in _rel(pool, cen["conta"])["linhas"]] == ["Real"]


def test_compromisso_que_nao_e_visita_fica_de_fora(pool, cen):
    _visita(pool, cen["conta"], titulo="Visita — Sim", quando=ONTEM)
    _visita(pool, cen["conta"], titulo="Reunião interna", quando=ONTEM)
    assert [l["lead"] for l in _rel(pool, cen["conta"])["linhas"]] == ["Sim"]


def test_visita_futura_nao_entra_no_comparecimento(pool, cen):
    """Senão o no-show inclui quem ainda nem chegou a data — e o número que o dono
    vai cobrar do time nasce errado."""
    _visita(pool, cen["conta"], titulo="Visita — Hoje", quando=ONTEM, desfecho="realizado")
    _visita(pool, cen["conta"], titulo="Visita — Depois", quando=AMANHA)
    d = _rel(pool, cen["conta"])
    assert _metrica(d, "Compareceram").startswith("100%")
    assert "todas as 1 respondidas" in _metrica(d, "Compareceram")
    futura = next(l for l in d["linhas"] if l["lead"] == "Depois")
    assert futura["desfecho"] == "ainda vai acontecer"


# -------------------------------------------------- a visita sem lead

def test_visita_sem_lead_conta_como_agendada_mas_nao_tem_espera(pool, cen):
    """São 8 das 14 na conta 34. Somem do numerador de qualquer taxa que dependa do
    lead, e a tela diz isso na coluna em vez de esconder."""
    _visita(pool, cen["conta"], titulo="VISITA TÉCNICA - PEDRO", quando=ONTEM)
    d = _rel(pool, cen["conta"])
    assert len(d["linhas"]) == 1
    assert d["linhas"][0]["esperou"] == "sem lead"


def test_espera_ate_agendar_sai_do_lead_ligado(pool, cen):
    chegou = ONTEM - timedelta(hours=3)
    pid = _lead(pool, cen["conta"], "Juliana", chegou=chegou)
    _visita(pool, cen["conta"], titulo="Visita — Juliana", quando=AMANHA,
            lead=pid, criada=chegou + timedelta(hours=2))
    l = _rel(pool, cen["conta"])["linhas"][0]
    assert l["lead"] == "Juliana" and l["esperou"] == "2h00"


# ------------------------------------------------------ o aviso e as taxas

def test_o_aviso_conta_as_visitas_sem_resposta(pool, cen):
    for n in "AB":
        _visita(pool, cen["conta"], titulo=f"Visita — {n}", quando=ONTEM, desfecho=None)
    _visita(pool, cen["conta"], titulo="Visita — C", quando=ONTEM, desfecho="realizado")
    aviso = _rel(pool, cen["conta"])["aviso_config"]
    assert "2 das 3 visitas" in aviso and "sem resposta" in aviso


def test_sem_buraco_nenhum_o_aviso_some(pool, cen):
    _visita(pool, cen["conta"], titulo="Visita — A", quando=ONTEM, desfecho="realizado")
    assert _rel(pool, cen["conta"])["aviso_config"] == ""


def test_taxa_de_amostra_pequena_sai_marcada_como_pouco_confiavel():
    """A regra, sem banco: 3 respondidas de 8 que aconteceram é cobertura de 37%."""
    t = vendas.taxa_com_cobertura(2, 3, base=8)
    assert t["texto"] == "67%" and t["confiavel"] is False and t["tom"] == "ambar"


def test_taxa_com_cobertura_cheia_sai_limpa():
    t = vendas.taxa_com_cobertura(7, 8, base=8)
    assert t["confiavel"] is True and t["tom"] == "ok"


def test_sinal_pago_zerado_nao_vira_taxa_falsa(pool, cen):
    with pool.connection() as c:
        c.execute("insert into orcamentos (conta_id, numero) values (%s,1)", (cen["conta"],))
        c.commit()
    assert _metrica(_rel(pool, cen["conta"]), "Viraram sinal").startswith("0%")


# ------------------------------------------------------------ forma e escopo

def test_nao_tem_coluna_de_valor_nem_linha_de_total(pool, cen):
    _visita(pool, cen["conta"], quando=ONTEM)
    d = _rel(pool, cen["conta"])
    assert not any(c["brl"] for c in d["colunas"])
    assert d["col_total"] is None


def test_uma_unica_coluna_elastica(pool, cen):
    """Regra que a main trouxe no #571 — sem ela a tabela rola pro lado."""
    _visita(pool, cen["conta"], quando=ONTEM)
    assert sum(1 for c in _rel(pool, cen["conta"])["colunas"] if c["flex"]) == 1


def test_filtro_sem_resposta(pool, cen):
    _visita(pool, cen["conta"], titulo="Visita — Muda", quando=ONTEM, desfecho=None)
    _visita(pool, cen["conta"], titulo="Visita — Falou", quando=ONTEM, desfecho="realizado")
    d = _rel(pool, cen["conta"], status="sem_resposta")
    assert [l["lead"] for l in d["linhas"]] == ["Muda"]


def test_filtro_por_vendedor(pool, cen):
    _visita(pool, cen["conta"], titulo="Visita — DoPedro", quando=ONTEM, membro=cen["pedro"])
    _visita(pool, cen["conta"], titulo="Visita — DeNinguem", quando=ONTEM)
    d = _rel(pool, cen["conta"], vendedor=str(cen["pedro"]))
    assert [l["lead"] for l in d["linhas"]] == ["DoPedro"]


def test_outra_conta_nao_vaza(pool, cen):
    with pool.connection() as c:
        outra = c.execute("insert into contas (nome) values ('Doce Mell') "
                          "returning id").fetchone()[0]
        c.commit()
    _visita(pool, cen["conta"], titulo="Visita — Minha", quando=ONTEM)
    _visita(pool, outra, titulo="Visita — Alheia", quando=ONTEM)
    assert [l["lead"] for l in _rel(pool, cen["conta"])["linhas"]] == ["Minha"]


def test_aba_registrada():
    assert rel.TIPOS["funil"]["label"] == "Funil"


# ------------------------------------------- o Cockpit traz a visita de volta

def test_cockpit_traz_a_visita_passada_sem_resposta(pool, cen):
    """A causa raiz: a consulta cortava em `inicio >= hoje` e a visita sumia da tela
    do vendedor no dia seguinte. Não havia onde dizer se o cliente apareceu."""
    from finance import cockpit as ck
    _visita(pool, cen["conta"], titulo="Visita — Beatriz", quando=ONTEM, desfecho=None)
    itens = ck.agenda_da_conta(pool, cen["conta"])
    pend = [i for i in itens if i["precisa_resposta"]]
    assert [i["titulo"] for i in pend] == ["Visita — Beatriz"]


def test_cockpit_esconde_a_visita_ja_respondida(pool, cen):
    from finance import cockpit as ck
    _visita(pool, cen["conta"], titulo="Visita — Respondida", quando=ONTEM,
            desfecho="realizado")
    itens = ck.agenda_da_conta(pool, cen["conta"])
    assert not [i for i in itens if i["precisa_resposta"]]
    assert "Visita — Respondida" not in [i["titulo"] for i in itens], \
        "visita já respondida não pode voltar pra agenda do vendedor"


def test_cockpit_nao_pergunta_pela_festa_do_cliente(pool, cen):
    from finance import cockpit as ck
    _visita(pool, cen["conta"], titulo="Visita — Casamento", quando=ONTEM,
            tipo_evento="Casamento")
    itens = ck.agenda_da_conta(pool, cen["conta"])
    assert not [i for i in itens if i["precisa_resposta"]]


def test_cockpit_nao_pergunta_por_visita_futura(pool, cen):
    from finance import cockpit as ck
    _visita(pool, cen["conta"], titulo="Visita — Amanhã", quando=AMANHA)
    itens = ck.agenda_da_conta(pool, cen["conta"])
    assert [i["titulo"] for i in itens] == ["Visita — Amanhã"]
    assert not [i for i in itens if i["precisa_resposta"]]


def test_cockpit_continua_trazendo_o_futuro_junto(pool, cen):
    """A mudança não pode ter trocado uma lista pela outra: o vendedor precisa das
    duas — o que ele deve responder e o que ele tem pela frente."""
    from finance import cockpit as ck
    _visita(pool, cen["conta"], titulo="Visita — Ontem", quando=ONTEM)
    _visita(pool, cen["conta"], titulo="Visita — Amanhã", quando=AMANHA)
    titulos = [i["titulo"] for i in ck.agenda_da_conta(pool, cen["conta"])]
    assert set(titulos) == {"Visita — Ontem", "Visita — Amanhã"}


def test_cockpit_com_janela_de_dias_nao_quebra_os_argumentos(pool, cen):
    """`dias` acrescenta um marcador no meio do SQL — a ordem dos argumentos é o
    tipo de coisa que quebra calada e só aparece em produção."""
    from finance import cockpit as ck
    _visita(pool, cen["conta"], titulo="Visita — Ontem", quando=ONTEM)
    _visita(pool, cen["conta"], titulo="Visita — Amanhã", quando=AMANHA)
    titulos = [i["titulo"] for i in ck.agenda_da_conta(pool, cen["conta"], dias=7)]
    assert set(titulos) == {"Visita — Ontem", "Visita — Amanhã"}


# ------------------------------------------------------ a redação, sem banco

def test_desfecho_ausente_nao_conta_no_comparecimento():
    assert vendas.desfecho_da_visita(None, True)["conta_no_comparecimento"] is False
    assert vendas.desfecho_da_visita("realizado", True)["conta_no_comparecimento"] is True
    assert vendas.desfecho_da_visita("nao_realizado", True)["conta_no_comparecimento"] is True


def test_texto_da_cobertura_fala_em_gente_nao_em_porcentagem():
    assert vendas.texto_da_cobertura(3, 8) == "3 de 8 responderam — faltam 5"
    assert vendas.texto_da_cobertura(8, 8) == "todas as 8 responderam"
    assert vendas.texto_da_cobertura(0, 0) == "sem nenhuma visita no período"
