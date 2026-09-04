"""O funil separado por mês do EVENTO (migração 197 + finance/evento_lead.py).

POR QUE. Prime Eventos, 04/09/2026: 224 dos 274 leads numa coluna só, sem eixo de
tempo. No nicho de eventos o mês que importa é o da festa. Este teste trava o que a
tela faz com isso:

  * o trilho de meses em cima do quadro, e o filtro `?mes=` em todas as colunas
    (com o "9 de 224" pra ninguém achar que os outros sumiram);
  * dentro da coluna, separadores por mês do evento e por mês de entrada;
  * a dobra dos parados há 15+ dias, fechada, no pé — sem mover ninguém no banco;
  * a linha do evento no card, e o "perguntar" só onde há conversa (ou número);
  * numa conta que não vende data, nada disso aparece — funil de sempre.
"""
import os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool
from starlette.datastructures import QueryParams

from web import painel_prospeccao as pp

CONTA = 11

_SQL = """
create table contas (id bigserial primary key, chip_de bigint, nome text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text not null, segmento text, cidade text, uf text, telefone text, whatsapp text,
  email text, instagram text, temperatura text default 'frio',
  valor_estimado_centavos bigint default 0, proximo_contato_em date,
  enriquecido_em timestamptz, estagio text default 'lead', status text default 'novo',
  evento_em date, evento_tipo text, evento_convidados int, ultimo_contato_em timestamptz,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false,
  criado_em timestamptz default now(), constraint uq_funil_etapa unique (conta_id, chave));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true);
create table conversas (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint references prospeccao(id), canal text, chip_id bigint,
  ultima_msg_em timestamptz, criado_em timestamptz default now(), visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  texto text, criado_em timestamptz default now());
create table campanhas (id bigserial primary key, conta_id bigint, nome text);
create table campanha_alvos (id bigserial primary key, campanha_id bigint,
  prospeccao_id bigint references prospeccao(id), ultima_msg_em timestamptz);
create table canais_config (id bigserial primary key, conta_id bigint, canal text, rotulo text);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_por_mes_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def vende_data(monkeypatch):
    """A conta é de eventos. Sem isto (o padrão) o funil é o de sempre."""
    import finance.vendas as v
    monkeypatch.setattr(v, "vende_data", lambda pool, conta_id: True)


def _lead(pool, empresa, *, status="contatado", evento_em=None, tipo=None, conv=None,
          whatsapp="", criado_em=None):
    with pool.connection() as c:
        lid = c.execute(
            """insert into prospeccao (conta_id, empresa, status, evento_em, evento_tipo,
                                       evento_convidados, whatsapp, criado_em)
               values (%s,%s,%s,%s,%s,%s,%s,coalesce(%s, now())) returning id""",
            (CONTA, empresa, status, evento_em, tipo, conv, whatsapp or None, criado_em)).fetchone()[0]
        c.commit()
    return lid


def _conversa(pool, lead_id, msg_ha_dias=None):
    with pool.connection() as c:
        cid = c.execute(
            "insert into conversas (conta_id, prospeccao_id, canal) values (%s,%s,'whatsapp') returning id",
            (CONTA, lead_id)).fetchone()[0]
        if msg_ha_dias is not None:
            c.execute("insert into mensagens (conversa_id, direcao, texto, criado_em) "
                      "values (%s,'in','oi',now() - %s * interval '1 day')", (cid, msg_ha_dias))
        c.commit()
    return cid


def _html(monkeypatch, pool, vendedor="", mes="") -> str:
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": 1, "gerencia": True, "pode_atribuir": True}, None))
    req = SimpleNamespace(session={}, query_params=QueryParams(""))
    r = pp.prospeccao_kanban(req, vendedor=vendedor, mes=mes)
    assert r.status_code == 200
    return bytes(r.body).decode("utf-8")


def _coluna(html, chave):
    return html.split(f'data-status="{chave}"')[1].split('<div class="kbcol"')[0]


def _card(html, lid):
    """Só o HTML de UM card (do data-id dele até o card seguinte ou o fim da coluna)."""
    ini = html.index(f'data-id="{lid}"')
    fins = [html.find(m, ini + 1) for m in ('data-id="', "</details>", "</div>\n      </div>")]
    fim = min(x for x in fins if x > 0)
    return html[ini:fim]


# ------------------------------------------------------------------ a linha do evento
def test_o_card_mostra_tipo_data_e_convidados(monkeypatch, pool, vende_data):
    _lead(pool, "Fernanda Lima", evento_em=date(2027, 11, 28), tipo="Aniversário", conv=120)
    html = _html(monkeypatch, pool)
    assert "🎂 <b>Aniversário</b> · <span class=\"d\">28 nov 27</span> · 120 conv." in html


def test_sem_data_o_card_diz_e_o_perguntar_abre_a_conversa_com_o_texto_pronto(monkeypatch, pool, vende_data):
    lid = _lead(pool, "Priscila N.", whatsapp="86999990000")
    cid = _conversa(pool, lid, msg_ha_dias=0)
    html = _html(monkeypatch, pool)
    assert f'kbPerguntarData(event,{cid},this)">perguntar</button>' in html
    # a pergunta é a do agente, e vai pra caixa do balão — não dispara sozinha
    assert 'var KB_PERGUNTA_DATA="Pra qual data' in html
    assert "function kbPerguntarData(ev,convId,btn){_cpPrefill=KB_PERGUNTA_DATA;kbAbrirChat" in html


def test_sem_conversa_mas_com_numero_o_perguntar_cai_no_wa_me(monkeypatch, pool, vende_data):
    lid = _lead(pool, "Rodrigo A.", whatsapp="86988880000")
    card = _card(_html(monkeypatch, pool), lid)
    assert "wa.me/5586988880000?text=Pra%20qual%20data" in card
    assert "kbPerguntarData" not in card


def test_sem_numero_nenhum_nao_ha_o_que_perguntar(monkeypatch, pool, vende_data):
    lid = _lead(pool, "Sem Fone")
    card = _card(_html(monkeypatch, pool), lid)
    assert "📅 sem data" in card and "perguntar" not in card


def test_conta_que_nao_vende_data_tem_o_funil_de_sempre(monkeypatch, pool):
    """Padaria, pet shop: a data da festa não existe. Nem 'sem data', nem trilho —
    mas um lead que por acaso tem data continua mostrando."""
    import finance.vendas as v
    monkeypatch.setattr(v, "vende_data", lambda pool, conta_id: False)
    _lead(pool, "Padaria Bom Pão", whatsapp="86977770000")
    _lead(pool, "Com Data", evento_em=date(2027, 1, 16), tipo="Formatura")
    html = _html(monkeypatch, pool)
    assert 'class="kbev sem"' not in html and 'id="trilho"' not in html
    assert "🎓 <b>Formatura</b>" in html


def test_evento_que_ja_passou_com_etapa_aberta_fica_marcado(monkeypatch, pool, vende_data):
    a = _lead(pool, "Festa Passada", evento_em=date(2026, 1, 10), tipo="Casamento")
    b = _lead(pool, "Festa Feita", status="ganho", evento_em=date(2026, 1, 10), tipo="Casamento")
    html = _html(monkeypatch, pool)
    assert 'class="kbev passou"' in _card(html, a)
    assert 'class="kbev passou"' not in _card(html, b)


# ------------------------------------------------------------------ grupos na coluna
def test_a_coluna_separa_por_mes_do_evento_depois_sem_data_por_entrada(monkeypatch, pool, vende_data):
    _lead(pool, "Janeiro", evento_em=date(2027, 1, 16))
    _lead(pool, "Novembro", evento_em=date(2026, 11, 14))
    _lead(pool, "Sem Data Set", criado_em=datetime(2026, 9, 2, tzinfo=timezone.utc))
    col = _coluna(_html(monkeypatch, pool), "contatado")
    i_nov, i_jan = col.index("Nov 26 <b>1</b>"), col.index("Jan 27 <b>1</b>")
    i_sem = col.index("Sem data · entrou em set <b>1</b>")
    assert i_nov < i_jan < i_sem
    assert col.index("Novembro") < col.index("Janeiro") < col.index("Sem Data Set")


def test_coluna_com_um_grupo_so_nao_ganha_separador(monkeypatch, pool, vende_data):
    _lead(pool, "Um")
    _lead(pool, "Dois")
    col = _coluna(_html(monkeypatch, pool), "contatado")
    assert 'class="kbgrp"' not in col


def test_parado_ha_15_dias_sem_mensagem_vai_pra_dobra_fechada_no_pe(monkeypatch, pool, vende_data):
    """Decisão do dono: 15 dias. E NADA muda no banco — o lead continua na etapa,
    só dobra na tela e volta sozinho na primeira mensagem."""
    vivo = _lead(pool, "Falou Ontem", evento_em=date(2027, 1, 16))
    _conversa(pool, vivo, msg_ha_dias=1)
    quieto = _lead(pool, "Quieto", evento_em=date(2027, 1, 20),
                   criado_em=datetime.now(timezone.utc) - timedelta(days=30))
    _conversa(pool, quieto, msg_ha_dias=16)
    _lead(pool, "Nunca Falou", criado_em=datetime.now(timezone.utc) - timedelta(days=40))
    col = _coluna(_html(monkeypatch, pool), "contatado")
    dobra = col.split('<details class="kbdobra">')[1]
    assert "Parados 15+ dias <b>2</b>" in dobra
    assert "Quieto" in dobra and "Nunca Falou" in dobra and "Falou Ontem" not in dobra
    assert "<details class=\"kbdobra\" open" not in col
    with pool.connection() as c:
        assert c.execute("select status from prospeccao where id=%s", (quieto,)).fetchone()[0] == "contatado"


def test_a_contagem_da_coluna_continua_sendo_a_coluna_inteira(monkeypatch, pool, vende_data):
    quieto = _lead(pool, "Quieto", criado_em=datetime.now(timezone.utc) - timedelta(days=30))
    _conversa(pool, quieto, msg_ha_dias=20)
    _lead(pool, "Vivo")
    col = _coluna(_html(monkeypatch, pool), "contatado")
    assert '<span class="kbcnt">2</span>' in col


# ------------------------------------------------------------------ trilho + filtro
def test_o_trilho_lista_os_meses_com_contagem_e_sem_data_no_fim(monkeypatch, pool, vende_data):
    _lead(pool, "A", evento_em=date(2027, 1, 16))
    _lead(pool, "B", evento_em=date(2027, 1, 20), status="proposta")
    _lead(pool, "C", evento_em=date(2026, 11, 14))
    _lead(pool, "Perdido", evento_em=date(2026, 11, 30), status="perdido")   # fora do trilho
    _lead(pool, "Sem")
    html = _html(monkeypatch, pool)
    trilho = html.split('id="trilho"')[1].split("</div>")[0]
    assert "Todos <b>4</b>" in trilho
    assert trilho.index("Nov 26 <b>1</b>") < trilho.index("Jan 27 <b>2</b>") < trilho.index("Sem data <b>1</b>")
    assert 'href="/painel/prospeccao?mes=2027-01"' in trilho
    assert 'href="/painel/prospeccao?mes=sem"' in trilho


def test_sem_nenhuma_data_o_trilho_nao_aparece(monkeypatch, pool, vende_data):
    _lead(pool, "Sem")
    assert 'id="trilho"' not in _html(monkeypatch, pool)


def test_filtrar_por_mes_vale_pro_quadro_inteiro_e_mostra_o_de_quantos(monkeypatch, pool, vende_data):
    _lead(pool, "Jan Contatado", evento_em=date(2027, 1, 16))
    _lead(pool, "Jan Proposta", evento_em=date(2027, 1, 20), status="proposta")
    _lead(pool, "Nov Contatado", evento_em=date(2026, 11, 14))
    _lead(pool, "Sem Data")
    html = _html(monkeypatch, pool, mes="2027-01")
    assert "Jan Contatado" in html and "Jan Proposta" in html
    assert "Nov Contatado" not in html and "Sem Data" not in html
    assert '<span class="kbcnt">1 <i>de 3</i></span>' in _coluna(html, "contatado")
    assert '<span class="kbcnt">1 <i>de 1</i></span>' in _coluna(html, "proposta")
    assert "<b>Jan 27</b> · só as festas desse mês" in html
    # a pílula do mês escolhido acende; o trilho inteiro continua (é a régua)
    assert 'class="mes on" href="/painel/prospeccao?mes=2027-01"' in html
    assert "Nov 26 <b>1</b>" in html


def test_filtrar_sem_data_e_a_fila_de_quem_ainda_nao_disse_quando(monkeypatch, pool, vende_data):
    _lead(pool, "Com Data", evento_em=date(2027, 1, 16))
    _lead(pool, "Sem Data")
    html = _html(monkeypatch, pool, mes="sem")
    assert "Sem Data" in html and "Com Data" not in html
    assert "<b>Sem data do evento</b>" in html


def test_mes_invalido_e_ignorado(monkeypatch, pool, vende_data):
    _lead(pool, "Com Data", evento_em=date(2027, 1, 16))
    html = _html(monkeypatch, pool, mes="2027-13; drop table prospeccao")
    assert "Com Data" in html and 'class="trilho-faixa"' not in html


def test_o_filtro_de_mes_sobrevive_a_troca_de_vendedor(monkeypatch, pool, vende_data):
    _lead(pool, "Com Data", evento_em=date(2027, 1, 16))
    html = _html(monkeypatch, pool, mes="2027-01")
    assert '<input type="hidden" name="mes" value="2027-01">' in html
