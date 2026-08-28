"""A aba Agenda do app, RENDERIZADA — com as datas reais da Prime.

O motor está em tests/test_cockpit_agenda_completa.py. Aqui se prova o que o
vendedor vê no celular, porque uma agenda que o motor traz certo e a tela não mostra
não serve pra ninguém.

O caso que se monta abaixo é o real: 35 compromissos futuros, 6 pré-reservas, o
choque de 10/07/2027 entre o Allef (reservado) e a Márcia (pré-reserva), e as datas
espalhadas até outubro de 2027. Com o teto de 14 dias, essa tela mostrava 4 linhas.

Quatro coisas que só se verificam renderizando:
 1. o que está a meses de distância chega à tela — agrupado por mês, senão vira um
    rolo de 35 linhas no celular;
 2. as pílulas de ESTADO existem e levam a contagem dentro;
 3. trocar de estado não joga fora o Meus/Todos que a pessoa escolheu;
 4. o mês que tem choque avisa ANTES de ser aberto.
"""
import os
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import agenda as ag
from web import painel_cockpit as pc

CONTA, VEND, OUTRO = 1, 10, 11

_SQL = """
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  razao_social text, endereco text, bairro text, cidade text, uf text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true,
  cockpit_push_ativo boolean default true, cockpit_pausado boolean default false);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, whatsapp text, telefone text, status text default 'novo',
  estagio text default 'lead', orcamento_id bigint, ultimo_contato_em timestamptz,
  atualizado_em timestamptz default now());
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text, resultado text, descricao text,
  criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  status text default 'aberta', agente_ativo boolean default true,
  responsavel_membro_id bigint, ultima_msg_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  autor text default 'humano', membro_id bigint, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  lembrete_min int, tipo text default 'pessoal', link_online text, desfecho text,
  status text default 'ativo', criado_em timestamptz default now(), prospeccao_id bigint,
  ics_token text, pre_reserva_ate timestamptz, sinal_centavos int,
  tipo_evento text, convidados int, hora_sugerida boolean default false);
"""


@pytest.fixture()
def cli(monkeypatch):
    dbname = "zaq_ck_agenda_tela"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute(_SQL)
        c.execute("insert into contas (id, nome) values (%s,'Prime Eventos')", (CONTA,))
        c.execute("insert into membros (id, conta_id, nome) values (%s,%s,'Pedro Yan'), "
                  "(%s,%s,'Jacqueline')", (VEND, CONTA, OUTRO, CONTA))
        c.commit()

    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    monkeypatch.setattr(pc, "_sessao", lambda request: (CONTA, VEND))
    monkeypatch.setattr(pc, "_gerencia", lambda request: None)
    monkeypatch.setattr(pc, "_pend_vend", lambda *a, **k: 0)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pc.router)
    c = TestClient(app, follow_redirects=False)
    c.pool = pool
    yield c
    pool.close()


def _ev(cli, *, dias, hora=19, membro=VEND, titulo="Locação — Ana",
        status="ativo", prazo_dias=None, sugerida=False):
    quando = (datetime.now(ag.BRT) + timedelta(days=dias)).replace(
        hour=hora, minute=0, second=0, microsecond=0)
    ate = (datetime.now(ag.BRT) + timedelta(days=prazo_dias)) if prazo_dias else None
    with cli.pool.connection() as c:
        c.execute("""insert into eventos_agenda (conta_id, membro_id, titulo, inicio,
                       status, pre_reserva_ate, hora_sugerida)
                     values (%s,%s,%s,%s,%s,%s,%s)""",
                  (CONTA, membro, titulo, quando, status, ate, sugerida))
        c.commit()
    return quando


def _html(cli, **q):
    r = cli.get("/cockpit/agenda", params=q)
    assert r.status_code == 200, r.text[:400]
    return r.text


# ═══════════════ o que o teto escondia ═══════════════

def test_a_festa_de_daqui_a_um_ano_chega_a_tela(cli):
    """Com `dias=14` esta linha não existia."""
    quando = _ev(cli, dias=400, titulo="Locação — Vanessa Maria")
    html = _html(cli, t="todos")
    # ela chega DOBRADA no mês — 35 linhas soltas não cabem num celular. O nome do
    # mês e a contagem são o que a tela mostra; o título vem ao abrir.
    assert "Mais pra frente" in html, "faltou o agrupamento"
    assert quando.strftime("%Y-%m") in html, "a linha do mês precisa do mês"
    assert "Locação — Vanessa Maria" in _html(cli, t="todos", m=quando.strftime("%Y-%m"))


def test_a_pre_reserva_distante_chega_a_tela(cli):
    """As 6 da Prime estavam todas fora da janela. É a informação que evita
    prometer a mesma data duas vezes — e quem promete é quem está na rua."""
    _ev(cli, dias=270, status="pre_reservado", titulo="Casamento — Denise")
    html = _html(cli, t="todos", m=(datetime.now(ag.BRT) + timedelta(days=270)).strftime("%Y-%m"))
    assert "Casamento — Denise" in html
    assert "pré-reserva" in html


def test_o_que_esta_perto_vem_aberto_e_o_resto_dobrado(cli):
    """A rota da semana não pode estar dobrada dentro de um mês."""
    _ev(cli, dias=5, titulo="Locação — Perto")
    _ev(cli, dias=200, titulo="Locação — Longe")
    html = _html(cli, t="todos")
    assert "Próximas semanas" in html
    assert "Locação — Perto" in html, "o que é perto tem que estar aberto"
    assert "Locação — Longe" not in html, "o que é longe tem que estar dobrado no mês"


def test_o_mes_abre_quando_pedido(cli):
    quando = _ev(cli, dias=200, titulo="Locação — Longe")
    assert "Locação — Longe" not in _html(cli, t="todos")
    assert "Locação — Longe" in _html(cli, t="todos", m=quando.strftime("%Y-%m"))


# ═══════════════ as pílulas ═══════════════

def test_as_pilulas_de_estado_existem_com_a_contagem(cli):
    """Sem o número ninguém toca em "Pré-reserva" pra descobrir que tem seis."""
    _ev(cli, dias=5)
    _ev(cli, dias=6)
    _ev(cli, dias=7, status="pre_reservado")
    html = _html(cli, t="todos")
    assert "Tudo <span class=c>3</span>" in html
    assert "Reservado <span class=c>2</span>" in html
    assert "Pré-reserva <span class=c>1</span>" in html


def test_a_pilula_de_pre_reserva_filtra(cli):
    _ev(cli, dias=5, titulo="Locação — Reservada")
    _ev(cli, dias=6, status="pre_reservado", titulo="Casamento — Segurada")
    html = _html(cli, t="todos", e="pre")
    assert "Casamento — Segurada" in html
    assert "Locação — Reservada" not in html


def test_trocar_de_estado_nao_joga_fora_o_meus(cli):
    """Quem está em "Meus" e toca em "Pré-reserva" quer MINHAS pré-reservas — não
    as do time inteiro."""
    _ev(cli, dias=5, status="pre_reservado")
    html = _html(cli, t="meus")
    assert "t=meus&amp;e=pre" in html or "t=meus&e=pre" in html, \
        "o link da pílula de estado perdeu o Meus"


def test_abrir_um_mes_nao_joga_fora_os_filtros(cli):
    quando = _ev(cli, dias=200, status="pre_reservado")
    html = _html(cli, t="meus", e="pre")
    alvo = quando.strftime("%Y-%m")
    assert f"t=meus&amp;e=pre&amp;m={alvo}" in html or f"t=meus&e=pre&m={alvo}" in html


def test_o_cabecalho_conta_reservadas_e_pre_reservas(cli):
    _ev(cli, dias=5)
    _ev(cli, dias=6, status="pre_reservado")
    assert "1 reservadas · 1 pré-reservas" in _html(cli, t="todos")


# ═══════════════ as palavras ═══════════════

def test_a_tag_fala_a_lingua_do_dono(cli):
    _ev(cli, dias=5, titulo="Festa firme")
    _ev(cli, dias=6, status="pre_reservado", titulo="Festa segurando")
    html = _html(cli, t="todos")
    assert ">reservado<" in html
    assert ">pré-reserva<" in html
    assert ">compromisso<" not in html, "a palavra antiga voltou"
    assert ">segurada<" not in html, "a palavra antiga voltou"


# ═══════════════ o choque ═══════════════

def test_o_mes_com_choque_avisa_antes_de_abrir(cli):
    """O vendedor descobre no mês, e não na hora de prometer pro cliente."""
    _ev(cli, dias=300, hora=17, titulo="Locação — Allef")
    _ev(cli, dias=300, hora=20, titulo="Locação — Márcia", status="pre_reservado")
    html = _html(cli, t="todos")
    assert "choque de data" in html


def test_a_linha_do_evento_avisa_do_choque(cli):
    quando = _ev(cli, dias=300, hora=17, titulo="Locação — Allef")
    _ev(cli, dias=300, hora=20, titulo="Locação — Márcia", status="pre_reservado")
    html = _html(cli, t="todos", m=quando.strftime("%Y-%m"))
    assert "outra festa nesta data" in html


def test_mes_tranquilo_mostra_as_bolinhas(cli):
    _ev(cli, dias=200, titulo="Uma só")
    html = _html(cli, t="todos")
    assert "class=pts" in html
    assert "choque de data" not in html


# ═══════════════ o resto ═══════════════

def test_a_hora_chutada_vem_sublinhada(cli):
    _ev(cli, dias=5, sugerida=True)
    html = _html(cli, t="todos")
    assert "class='h sug'" in html
    assert "horário a conferir" in html


def test_o_prazo_do_sinal_aparece_no_card(cli):
    _ev(cli, dias=20, status="pre_reservado", prazo_dias=4.2)
    assert "sinal vence em 4d" in _html(cli, t="todos")


def test_agenda_vazia_diz_o_que_fazer(cli):
    html = _html(cli, t="todos")
    assert "Nada na agenda" in html
    assert "toque no +" in html


def test_filtro_vazio_nao_finge_que_a_agenda_acabou(cli):
    """Com uma reservada e nenhuma pré-reserva, a pílula "Pré-reserva" tem que dizer
    que NÃO HÁ DATA SEGURADA — e não "marque uma visita", que sugere agenda vazia."""
    _ev(cli, dias=5)
    html = _html(cli, t="todos", e="pre")
    assert "Nenhuma data segurada" in html
    assert "toque no +" not in html


def test_nenhum_mes_aparece_em_dois_lugares(cli):
    """O corte cai no FIM do mês, não no trigésimo dia.

    Cortando no dia 30, quatro festas de setembro apareciam abertas em cima e uma
    linha "setembro — 2" logo abaixo — que se lê como "setembro tem duas". Foi um
    defeito que só apareceu ao renderizar a agenda real da Prime."""
    perto = _ev(cli, dias=3, titulo="Festa do começo do mês")
    # um evento no MESMO mês, mas depois do trigésimo dia
    fim_do_mes = perto.replace(day=28) + timedelta(days=32)
    with cli.pool.connection() as c:
        c.execute("insert into eventos_agenda (conta_id, membro_id, titulo, inicio) "
                  "values (%s,%s,'Festa do fim do mês',%s)",
                  (CONTA, VEND, perto + timedelta(days=29)))
        c.commit()
    html = _html(cli, t="todos")
    assert "Festa do começo do mês" in html
    assert "Festa do fim do mês" in html, \
        "a festa do mesmo mês foi parar numa linha dobrada"
    assert html.count(perto.strftime("%Y-%m")) == 0, \
        "o mês que já está aberto em cima não pode ter linha dobrada embaixo"
    assert fim_do_mes


# ═══════════════ remarcar: só em visita ═══════════════

def _visita(cli, *, dias=5, titulo="Visita — Camila"):
    """Uma visita de verdade: evento ativo COM lead pendurado."""
    quando = (datetime.now(ag.BRT) + timedelta(days=dias)).replace(
        hour=15, minute=0, second=0, microsecond=0)
    with cli.pool.connection() as c:
        lead = c.execute("insert into prospeccao (conta_id, vendedor_id, empresa, whatsapp) "
                         "values (%s,%s,'Camila','5586999990000') returning id",
                         (CONTA, VEND)).fetchone()[0]
        eid = c.execute(
            """insert into eventos_agenda (conta_id, membro_id, titulo, inicio, fim,
                 prospeccao_id, ics_token) values (%s,%s,%s,%s,%s,%s,'tk') returning id""",
            (CONTA, VEND, titulo, quando, quando + timedelta(hours=1), lead)).fetchone()[0]
        c.commit()
    return eid


def test_a_visita_ganha_o_botao_de_remarcar(cli):
    eid = _visita(cli)
    html = _html(cli, t="todos")
    assert f"/agenda/{eid}/remarcar" in html, "a visita não tem como ser remarcada"


def test_a_festa_reservada_nao_ganha_o_botao(cli):
    """Mudar a data de uma festa mexe em contrato e em sinal — fica no painel."""
    _ev(cli, dias=5, titulo="Locação — Jonas")
    assert "/remarcar" not in _html(cli, t="todos")


def test_a_pre_reserva_nao_ganha_o_botao(cli):
    _ev(cli, dias=6, status="pre_reservado", titulo="Casamento — Denise")
    assert "/remarcar" not in _html(cli, t="todos")


def test_a_tela_de_remarcar_mostra_de_onde_pra_onde(cli):
    """Sem o "hoje está marcada pra", a pessoa escolhe a data nova sem lembrar da
    velha — e é a comparação que faz ela conferir."""
    eid = _visita(cli)
    r = cli.get(f"/cockpit/agenda/{eid}/remarcar")
    assert r.status_code == 200
    assert "Hoje está marcada pra" in r.text
    assert "Visita — Camila" in r.text
    assert "Avisar Camila no WhatsApp" in r.text


def test_remarcar_uma_festa_pela_barra_de_endereco_nao_abre(cli):
    """O id vem da URL: o portão tem que estar no servidor, não só no botão."""
    _ev(cli, dias=5, titulo="Locação — Jonas")
    with cli.pool.connection() as c:
        eid = c.execute("select id from eventos_agenda where titulo='Locação — Jonas'"
                        ).fetchone()[0]
    r = cli.get(f"/cockpit/agenda/{eid}/remarcar")
    assert r.status_code == 303, "abriu a tela de remarcar pra uma festa"


def test_lead_sem_whatsapp_nao_oferece_aviso(cli):
    """Caixinha marcada que não manda nada é pior que caixinha nenhuma: o vendedor
    sai achando que o cliente foi avisado."""
    quando = (datetime.now(ag.BRT) + timedelta(days=5)).replace(hour=15, minute=0,
                                                                second=0, microsecond=0)
    with cli.pool.connection() as c:
        lead = c.execute("insert into prospeccao (conta_id, vendedor_id, empresa, whatsapp) "
                         "values (%s,%s,'Sem Zap','') returning id", (CONTA, VEND)).fetchone()[0]
        eid = c.execute("""insert into eventos_agenda (conta_id, membro_id, titulo, inicio,
                             prospeccao_id) values (%s,%s,'Visita — Sem Zap',%s,%s)
                           returning id""", (CONTA, VEND, quando, lead)).fetchone()[0]
        c.commit()
    r = cli.get(f"/cockpit/agenda/{eid}/remarcar")
    assert "name=avisar" not in r.text
    assert "o aviso você dá na mão" in r.text


# ═══════════════ o cartão "Precisa de resposta" ═══════════════
#
# A visita PASSADA e sem desfecho sai da lista normal e sobe pro bloco vermelho do
# topo. Isso custava caro sem ninguém notar: junto com a linha comum iam embora os
# atalhos dela — Remarcar, Lead, Mapa, Calendário. O dono abriu a Agenda no celular e
# perguntou onde estava o botão de remarcar; estava atrás de "Não apareceu", que é
# resposta que ele ainda não quis dar.

def _visita_pendente(cli, *, dias=-3, titulo="Visita — Beatriz", com_lead=True):
    """Visita cuja hora JÁ PASSOU e que ninguém marcou como realizada ou não."""
    quando = (datetime.now(ag.BRT) + timedelta(days=dias)).replace(
        hour=15, minute=0, second=0, microsecond=0)
    with cli.pool.connection() as c:
        lead = None
        if com_lead:
            lead = c.execute(
                "insert into prospeccao (conta_id, vendedor_id, empresa, whatsapp) "
                "values (%s,%s,'Beatriz','5586999991111') returning id",
                (CONTA, VEND)).fetchone()[0]
        eid = c.execute(
            """insert into eventos_agenda (conta_id, membro_id, titulo, inicio, fim,
                 prospeccao_id, ics_token) values (%s,%s,%s,%s,%s,%s,'tk2') returning id""",
            (CONTA, VEND, titulo, quando, quando + timedelta(hours=1), lead)).fetchone()[0]
        c.commit()
    return eid, lead


def test_o_cartao_de_pendencia_tem_remarcar_sem_precisar_responder(cli):
    """A queixa do dono, virada teste: o botão tem que estar à vista ao abrir a tela,
    não só depois de marcar "não apareceu"."""
    eid, _ = _visita_pendente(cli)
    html = _html(cli, t="todos")
    assert "Precisa de resposta" in html, "a visita passada não subiu pro bloco"
    assert f"{pc._BASE}/agenda/{eid}/remarcar" in html, \
        "o cartão de pendência não oferece Remarcar — é a queixa que gerou isto"


def test_o_cartao_de_pendencia_leva_ao_lead(cli):
    """Sem isto o vendedor não tem por onde descobrir o que houve antes de responder."""
    _eid, lead = _visita_pendente(cli)
    assert f"{pc._BASE}/lead/{lead}" in _html(cli, t="todos")


def test_a_resposta_vem_antes_do_apoio(cli):
    """A hierarquia é o ponto. O cartão existe pra arrancar a RESPOSTA — se o apoio
    subir pra cima, ele vira a saída mais fácil, e comparecimento é o número que o
    relatório do funil usa."""
    eid, _ = _visita_pendente(cli)
    html = _html(cli, t="todos")
    resposta = max(html.index("data-d='realizado'"), html.index("data-d='nao_realizado'"))
    apoio = min(html.index(f"{pc._BASE}/agenda/{eid}/remarcar"),
                html.index("Abrir o lead"))
    assert resposta < apoio, "o apoio subiu na frente da resposta"


def test_o_apoio_nao_tem_o_peso_da_resposta(cli):
    """`.pb2` é a classe sem preenchimento. Se alguém trocar por `.pb`, os quatro
    botões ficam iguais e a pergunta some no meio deles."""
    _visita_pendente(cli)
    html = _html(cli, t="todos")
    assert "class=pb2" in html, "o apoio perdeu a classe discreta"
    # a folha do Cockpit é servida como ARQUIVO (/cockpit/app.css), não inline —
    # procurar o seletor no HTML da página não acha nada e o teste passa à toa
    assert ".pb2{" in pc._CSS_TEXTO, "falta a folha de estilo do apoio"
    assert "background:transparent" in pc._CSS_TEXTO.split(".pb2{", 1)[1][:200], \
        "o apoio ganhou preenchimento e passou a competir com a resposta"


def test_visita_pendente_sem_lead_nao_ganha_link_quebrado(cli):
    """Visita sem cliente ligado existe. Link que não abre é pior que link nenhum."""
    _visita_pendente(cli, com_lead=False)
    html = _html(cli, t="todos")
    assert "Precisa de resposta" in html
    assert "Abrir o lead" not in html
    assert f"{pc._BASE}/lead/None" not in html


def test_festa_passada_nao_vira_pendencia_nem_ganha_remarcar(cli):
    """Só visita pede desfecho, e só visita se remarca pelo celular — mudar a data de
    uma festa mexe em contrato e em sinal."""
    _ev(cli, dias=-4, titulo="Locação — Jonas")
    html = _html(cli, t="todos")
    assert "Precisa de resposta" not in html
    assert "/remarcar" not in html


def test_sem_pendencia_o_bloco_nao_existe(cli):
    """Bloco vermelho fixo no topo, sempre, vira moldura — ninguém mais lê."""
    _visita(cli, dias=5)          # visita FUTURA: ainda não deve nada
    html = _html(cli, t="todos")
    assert "Precisa de resposta" not in html


def test_visita_pendente_sem_lead_nao_oferece_remarcar(cli):
    """O caso que faz a guarda de `tipo_ev` valer a pena — e ele é alcançável.

    Escrevi antes um teste com pré-reserva passada achando que era esse o caminho;
    ela nem chega ao bloco, porque a cláusula que traz visita passada de volta exige
    `status='ativo'`. O caminho real é outro: `tipo_ev` vem de `prospeccao_id`
    (`"visita" if r[5] else "reservado"`, finance/cockpit.py), enquanto
    `precisa_resposta` só olha o TÍTULO. Então uma visita ativa, passada e SEM lead
    pede resposta com `tipo_ev == "reservado"`.

    Sem a guarda, ela ofereceria remarcar — e o cartão comum não oferece, pela mesma
    regra. Duas telas dizendo coisas diferentes sobre o mesmo compromisso é pior que
    as duas dizendo não."""
    eid, lead = _visita_pendente(cli, com_lead=False)
    assert lead is None
    html = _html(cli, t="todos")
    assert "Precisa de resposta" in html, "a visita passada devia pedir resposta"
    assert f"/agenda/{eid}/remarcar" not in html, (
        "visita sem lead ganhou Remarcar no cartão de pendência, mas não ganha no "
        "cartão comum — as duas telas têm que dizer a mesma coisa")
