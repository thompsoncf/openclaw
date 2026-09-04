"""O selo do canal no card do funil vira BOTÃO — mas só quando existe conversa
de verdade, não só telefone/e-mail cadastrado.

POR QUE ISTO EXISTE. O selo 💬 já existia no card, mas só avisava "tem WhatsApp
cadastrado" — não abria nada, e não sabia se alguém já tinha escrito. Clicar em
qualquer parte do card abria a FICHA do lead, nunca o chat. Isto testa a regra
nova: o selo só clica quando `conversas` tem uma linha de verdade pra aquele
lead+canal — buscada em LOTE (`= any(%s)`) na mesma consulta do kanban, uma
query pro board inteiro, não uma por card.
"""
import os

import pytest
from psycopg_pool import ConnectionPool
from starlette.datastructures import QueryParams
from types import SimpleNamespace

from web import painel_prospeccao as pp

CONTA = 11

_SQL = """
create table contas (id bigserial primary key, chip_de bigint, nome text);
create table prospeccao (id bigserial primary key, evento_em date, evento_tipo text, evento_convidados int, ultimo_contato_em timestamptz, conta_id bigint, vendedor_id bigint,
  empresa text not null, segmento text, cidade text, uf text, telefone text, whatsapp text,
  email text, instagram text, temperatura text default 'frio',
  valor_estimado_centavos bigint default 0, proximo_contato_em date,
  enriquecido_em timestamptz, estagio text default 'lead', status text default 'novo',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false,
  criado_em timestamptz default now(), constraint uq_funil_etapa unique (conta_id, chave));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true);
create table conversas (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint references prospeccao(id), canal text, chip_id bigint,
  ultima_msg_em timestamptz, criado_em timestamptz default now(), visto_ate_id bigint);
create table campanhas (id bigserial primary key, conta_id bigint, nome text);
create table campanha_alvos (id bigserial primary key, campanha_id bigint,
  prospeccao_id bigint references prospeccao(id), ultima_msg_em timestamptz);
create table canais_config (id bigserial primary key, conta_id bigint,
  canal text, rotulo text);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_kanban_chat_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
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


def _lead(pool, *, empresa, whatsapp="", email="", instagram=""):
    with pool.connection() as c:
        lid = c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp, email, instagram)
               values (%s,%s,%s,%s,%s) returning id""",
            (CONTA, empresa, whatsapp or None, email or None, instagram or None)
        ).fetchone()[0]
        c.commit()
    return lid


def _conversa(pool, lead_id, canal):
    with pool.connection() as c:
        cid = c.execute(
            "insert into conversas (conta_id, prospeccao_id, canal) values (%s,%s,%s) returning id",
            (CONTA, lead_id, canal)).fetchone()[0]
        c.commit()
    return cid


def _campanha(pool, nome="Black Friday Padarias"):
    with pool.connection() as c:
        cid = c.execute("insert into campanhas (conta_id, nome) values (%s,%s) returning id",
                        (CONTA, nome)).fetchone()[0]
        c.commit()
    return cid


def _alvo(pool, campanha_id, lead_id):
    with pool.connection() as c:
        c.execute("insert into campanha_alvos (campanha_id, prospeccao_id, ultima_msg_em) "
                  "values (%s,%s,now())", (campanha_id, lead_id))
        c.commit()


def _kanban_html(monkeypatch, pool) -> str:
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": 1, "gerencia": True, "pode_atribuir": True}, None))
    req = SimpleNamespace(session={}, query_params=QueryParams(""))
    r = pp.prospeccao_kanban(req, vendedor="")
    assert r.status_code == 200
    return bytes(r.body).decode("utf-8")


def test_lead_com_conversa_de_verdade_vira_botao(monkeypatch, pool):
    lid = _lead(pool, empresa="Padaria Bom Pão", whatsapp="86999998888")
    cid = _conversa(pool, lid, "whatsapp")
    html = _kanban_html(monkeypatch, pool)
    assert f"kbAbrirChat(event,{cid},'conversas',this)" in html, (
        "o selo de quem já tem conversa não virou botão")


def test_lead_so_com_telefone_cadastrado_continua_span_inerte(monkeypatch, pool):
    """O caso que motivou a regra: número cadastrado não é conversa. Sem isso o
    selo clicaria em algo que não existe (nenhum conversa_id pra abrir)."""
    _lead(pool, empresa="Distribuidora Rio Poti", whatsapp="86988887777")
    html = _kanban_html(monkeypatch, pool)
    assert "kbAbrirChat" not in html.split("Distribuidora Rio Poti")[1].split("</div>")[0], (
        "virou botão sem ter conversa nenhuma")
    assert '<span title="WhatsApp">💬</span>' in html


def test_os_tres_canais_viram_botao_cada_um_com_a_conversa_certa(monkeypatch, pool):
    lid = _lead(pool, empresa="Grupo Cerrado", whatsapp="86977776666",
               email="contato@cerrado.com", instagram="@cerrado")
    c_wa = _conversa(pool, lid, "whatsapp")
    c_em = _conversa(pool, lid, "email")
    c_ig = _conversa(pool, lid, "instagram")
    html = _kanban_html(monkeypatch, pool)
    assert f"kbAbrirChat(event,{c_wa},'conversas',this)" in html
    assert f"kbAbrirChat(event,{c_em},'emails',this)" in html
    assert f"kbAbrirChat(event,{c_ig},'conversas',this)" in html


def test_conversa_de_outra_conta_nao_vaza_pro_selo(monkeypatch, pool):
    """Isolamento multi-tenant: uma conversa com o MESMO prospeccao_id só é
    válida dentro da própria conta — a query já filtra por conta_id, mas o teste
    trava que ninguém tire esse filtro num refactor futuro."""
    lid = _lead(pool, empresa="Papelaria Central", whatsapp="86966665555")
    with pool.connection() as c:
        c.execute("insert into conversas (conta_id, prospeccao_id, canal) values (999,%s,'whatsapp')",
                  (lid,))
        c.commit()
    html = _kanban_html(monkeypatch, pool)
    assert "kbAbrirChat" not in html.split("Papelaria Central")[1].split("</div>")[0]


def test_uma_unica_query_de_conversas_pro_board_inteiro(monkeypatch, pool):
    """N+1: 20 leads não podem virar 20 idas ao banco só pra saber quem tem chat.
    Conta as chamadas de `execute` que mencionam a tabela `conversas`.

    Uma query em lote (`= any(%s)`) resolve tanto o selo de canal (💬 vira botão)
    quanto o chip que entrega o "· 📱 apelido" no selo de campanha — a MESMA
    conversa, lida uma vez só. Chegou a ser uma lateral separada, correlacionada
    por `prospeccao_id`; virou batch depois de um relato em produção (conta com
    2 chips batizados, campanha aparecendo mas o apelido do chip nunca) sem dado
    de banco pra confirmar a causa exata — a troca elimina qualquer chance de a
    lateral escolher uma conversa diferente da que o Inbox usa pro mesmo lead,
    o que a torna uma correção segura mesmo sem a causa raiz 100% confirmada.

    São DUAS em lote desde 02/09/2026, não uma: a segunda traz a última mensagem
    de cada lead pro card. Ela é separada de propósito — roda em savepoint e cai
    calada em instalação sem `mensagens`, coisa que um join na primeira não
    permitiria sem levar o funil junto. O que este teste protege continua sendo o
    mesmo: o número não pode CRESCER com a quantidade de leads. Por isso o
    assert é sobre duas consultas em 20 leads, e não sobre um número bonito."""
    for i in range(20):
        lid = _lead(pool, empresa=f"Lead {i}", whatsapp=f"8690000{i:04d}")
        if i % 2 == 0:
            _conversa(pool, lid, "whatsapp")
    chamadas = []
    real_connection = pool.connection
    class _ConnSpy:
        def __init__(self, conn):
            self._conn = conn
        def __enter__(self):
            self._c = self._conn.__enter__()
            real_execute = self._c.execute
            def execute_espiao(sql, *a, **k):
                if "from conversas" in sql or "conversas c" in sql:
                    chamadas.append(sql)
                return real_execute(sql, *a, **k)
            self._c.execute = execute_espiao
            return self._c
        def __exit__(self, *a):
            return self._conn.__exit__(*a)
    monkeypatch.setattr(pool, "connection", lambda: _ConnSpy(real_connection()))
    _kanban_html(monkeypatch, pool)
    assert len(chamadas) == 2, (
        f"esperava 2 queries em lote (conversas + última mensagem), não uma por "
        f"lead; teve {len(chamadas)}: {chamadas}")
    assert all("= any(" in q for q in chamadas), (
        "alguma das consultas não é em lote — é o N+1 voltando pela porta dos fundos")


def _trecho_card(html, empresa):
    """Isola o miolo de um card (cabeçalho + sub + selo de campanha + kbch),
    até a linha `.ft` — que sempre renderiza, sem `{% if %}` em volta do
    próprio <div>, o que torna esse ponto um limite seguro pra cortar."""
    return html.split(empresa)[1].split('<div class="ft">')[0]


def test_selo_de_campanha_aparece_quando_lead_veio_de_campanha(monkeypatch, pool):
    lid = _lead(pool, empresa="Padaria Bom Pão")
    camp = _campanha(pool)
    _alvo(pool, camp, lid)
    html = _kanban_html(monkeypatch, pool)
    assert '<div class="camp">📣 Black Friday Padarias</div>' in _trecho_card(html, "Padaria Bom Pão"), (
        "o selo de campanha não apareceu pro lead que veio dela")


def test_sem_campanha_nao_mostra_selo_nem_vazio(monkeypatch, pool):
    """Nem todo lead veio de campanha — sem dado, o card não pode ter selo vazio."""
    _lead(pool, empresa="Sem Vendedor Ltda")
    html = _kanban_html(monkeypatch, pool)
    assert 'class="camp"' not in _trecho_card(html, "Sem Vendedor Ltda")


def test_apelido_do_chip_so_aparece_com_dois_chips_na_conta(monkeypatch, pool):
    """Mesma regra do Inbox (_tem_dois_chips): com um chip só não existe "de qual
    chip" pra confundir ninguém — o sufixo "· 📱 apelido" fica de fora."""
    lid = _lead(pool, empresa="Padaria Bom Pão")
    camp = _campanha(pool)
    _alvo(pool, camp, lid)
    with pool.connection() as c:
        c.execute("insert into canais_config (conta_id, canal, rotulo) values (%s,'whatsapp','Chip Principal')",
                  (CONTA,))
        c.execute("insert into conversas (conta_id, prospeccao_id, canal, ultima_msg_em) "
                  "values (%s,%s,'whatsapp',now())", (CONTA, lid))
        c.commit()
    trecho = _trecho_card(_kanban_html(monkeypatch, pool), "Padaria Bom Pão")
    assert '<div class="camp">📣 Black Friday Padarias</div>' in trecho
    assert "Chip Principal" not in trecho, "mostrou o apelido do chip com um chip só na conta"


def test_apelido_do_chip_aparece_com_dois_chips_na_conta(monkeypatch, pool):
    lid = _lead(pool, empresa="Doce & Cia")
    camp = _campanha(pool)
    _alvo(pool, camp, lid)
    with pool.connection() as c:
        chip2 = c.execute("insert into contas (chip_de, nome) values (%s,'Chip Vendas') returning id",
                          (CONTA,)).fetchone()[0]
        c.execute("insert into conversas (conta_id, prospeccao_id, canal, chip_id, ultima_msg_em) "
                  "values (%s,%s,'whatsapp',%s,now())", (CONTA, lid, chip2))
        c.commit()
    trecho = _trecho_card(_kanban_html(monkeypatch, pool), "Doce & Cia")
    assert "📣 Black Friday Padarias" in trecho
    assert "· 📱 Chip Vendas" in trecho


def test_chip_sem_apelido_batizado_nao_vira_sufixo_vazio(monkeypatch, pool):
    """Dois chips na conta, mas ninguém batizou o principal ainda — o rótulo
    resolve pra string vazia; o selo não pode virar "Black Friday · 📱 "."""
    lid = _lead(pool, empresa="Empório Sabor Norte")
    camp = _campanha(pool)
    _alvo(pool, camp, lid)
    with pool.connection() as c:
        c.execute("insert into contas (chip_de, nome) values (%s,'Chip Vendas')", (CONTA,))
        c.execute("insert into conversas (conta_id, prospeccao_id, canal, ultima_msg_em) "
                  "values (%s,%s,'whatsapp',now())", (CONTA, lid))
        c.commit()
    trecho = _trecho_card(_kanban_html(monkeypatch, pool), "Empório Sabor Norte")
    assert '<div class="camp">📣 Black Friday Padarias</div>' in trecho
    assert "📱" not in trecho, "sufixo de chip apareceu vazio (apelido não batizado)"


def test_lead_sem_conversa_de_whatsapp_nao_ganha_sufixo_do_chip_principal(monkeypatch, pool):
    """Achado após um relato em produção (campanha aparecia, apelido do chip
    nunca — mesmo com 2 chips batizados). Um dos casos que a lateral antiga
    fazia diferente do Inbox: "sem conversa nenhuma" e "conversa pelo chip
    principal" viravam a MESMA coisa (chip_id nulo), e o selo mostrava o
    apelido do principal pra quem nunca trocou mensagem alguma. Precisa
    distinguir "não sei" (sem conversa) de "sei que é o principal"."""
    lid = _lead(pool, empresa="Sapataria Central")
    camp = _campanha(pool)
    _alvo(pool, camp, lid)
    with pool.connection() as c:
        c.execute("insert into contas (chip_de, nome) values (%s,'Chip Vendas')", (CONTA,))
        c.execute("insert into canais_config (conta_id, canal, rotulo) values (%s,'whatsapp','Chip Principal')",
                  (CONTA,))
        c.commit()
    trecho = _trecho_card(_kanban_html(monkeypatch, pool), "Sapataria Central")
    assert '<div class="camp">📣 Black Friday Padarias</div>' in trecho
    assert "📱" not in trecho, "mostrou o chip principal pra lead sem nenhuma conversa de WhatsApp"


def test_apelido_do_chip_aparece_mesmo_sem_campanha_interna_nenhuma(monkeypatch, pool):
    """Relato em produção (conta Prime, 22/08): leads de tráfego pago (Meta/Google
    Ads apontando pra um número específico) chegam direto pelo WhatsApp — nunca
    passam por `campanha_alvos`, porque não é uma campanha DISPARADA pelo Zaq.
    O selo antigo só nascia atrás de `{% if c.campanha %}`, e como esses leads
    não tinham campanha nenhuma, o apelido do chip nunca tinha chance de
    aparecer, mesmo resolvendo certo no Python. Selo de campanha e apelido de
    chip precisam ser independentes: qualquer um dos dois liga o selo."""
    lid = _lead(pool, empresa="Empório Sabor Norte")
    with pool.connection() as c:
        chip2 = c.execute("insert into contas (chip_de, nome) values (%s,'Chip 2 - Ads') returning id",
                          (CONTA,)).fetchone()[0]
        c.execute("insert into conversas (conta_id, prospeccao_id, canal, chip_id, ultima_msg_em) "
                  "values (%s,%s,'whatsapp',%s,now())", (CONTA, lid, chip2))
        c.commit()
    trecho = _trecho_card(_kanban_html(monkeypatch, pool), "Empório Sabor Norte")
    assert "📱 Chip 2 - Ads" in trecho, "apelido do chip não apareceu pra lead sem campanha interna"
    assert "📣" not in trecho, "não tem campanha_alvos nenhuma, não devia ter selo de campanha"


# ===================================================================== 02/09/2026
# A ÚLTIMA MENSAGEM NO CARD
#
# `mensagens` NÃO está no _SQL deste arquivo de propósito — é o que faz os testes
# de cima exercitarem o caminho tolerante (savepoint + funil abre sem a prévia).
# Quem precisa da tabela cria ela dentro do próprio teste.

_MSG_SQL = """
create table mensagens (id bigserial primary key, conversa_id bigint not null,
  canal text not null default 'whatsapp',
  direcao text not null check (direcao in ('in','out')),
  texto text not null default '', criado_em timestamptz not null default now());
"""


def _com_mensagens(pool):
    with pool.connection() as c:
        c.execute(_MSG_SQL)
        c.commit()


def _msg(pool, conversa_id, direcao, texto, *, minutos=0):
    with pool.connection() as c:
        mid = c.execute(
            "insert into mensagens (conversa_id, direcao, texto, criado_em) "
            "values (%s,%s,%s, now() - make_interval(mins => %s)) returning id",
            (conversa_id, direcao, texto, minutos)).fetchone()[0]
        c.commit()
    return mid


def test_a_ultima_mensagem_do_cliente_aparece_no_card(monkeypatch, pool):
    """O item 2 dos cinco ajustes: o card tinha o 💬 e não dizia o que havia
    dentro. Na Prime eram 192 leads com mensagem não vista, todos com a mesma
    cara de quem nunca falou."""
    _com_mensagens(pool)
    lid = _lead(pool, empresa="Padaria Bom Pão", whatsapp="86999998888")
    cid = _conversa(pool, lid, "whatsapp")
    _msg(pool, cid, "in", "queria saber o valor pra casamento em novembro")
    html = _kanban_html(monkeypatch, pool)
    card = _trecho_card(html, "Padaria Bom Pão")
    assert "queria saber o valor pra casamento" in card
    assert 'class="bolha"' in card, "mensagem do cliente nunca vista tem que acender a bolinha"


def test_mensagem_que_o_vendedor_ja_viu_nao_acende_bolinha(monkeypatch, pool):
    """`visto_ate_id` (188) é o que separa VI de RESPONDI. A mensagem continua no
    card — o que muda é o alarme."""
    _com_mensagens(pool)
    lid = _lead(pool, empresa="Distribuidora Rio Poti", whatsapp="86988887777")
    cid = _conversa(pool, lid, "whatsapp")
    mid = _msg(pool, cid, "in", "bom dia, ainda tem data em outubro?")
    with pool.connection() as c:
        c.execute("update conversas set visto_ate_id=%s where id=%s", (mid, cid))
        c.commit()
    card = _trecho_card(_kanban_html(monkeypatch, pool), "Distribuidora Rio Poti")
    assert "ainda tem data em outubro" in card
    assert 'class="bolha"' not in card


def test_o_que_o_vendedor_mandou_nunca_e_novidade_pra_ele(monkeypatch, pool):
    _com_mensagens(pool)
    lid = _lead(pool, empresa="Grupo Cerrado", whatsapp="86977776666")
    cid = _conversa(pool, lid, "whatsapp")
    _msg(pool, cid, "out", "mandei a proposta no seu e-mail")
    card = _trecho_card(_kanban_html(monkeypatch, pool), "Grupo Cerrado")
    assert "mandei a proposta" in card
    assert 'class="bolha"' not in card
    assert 'class="eu"' in card, "a mensagem do próprio vendedor tem que se identificar"


def test_entre_dois_canais_vale_a_mensagem_mais_nova(monkeypatch, pool):
    """O lead pode ter WhatsApp e e-mail. O card tem uma linha só — a mais nova."""
    _com_mensagens(pool)
    lid = _lead(pool, empresa="Papelaria Central", whatsapp="86966665555",
                email="contato@central.com")
    c_wa = _conversa(pool, lid, "whatsapp")
    c_em = _conversa(pool, lid, "email")
    _msg(pool, c_wa, "in", "falei no zap ontem", minutos=600)
    _msg(pool, c_em, "in", "respondi por e-mail agora", minutos=1)
    card = _trecho_card(_kanban_html(monkeypatch, pool), "Papelaria Central")
    assert "respondi por e-mail agora" in card
    assert "falei no zap ontem" not in card


def test_lead_sem_mensagem_nenhuma_nao_ganha_linha_vazia(monkeypatch, pool):
    _com_mensagens(pool)
    lid = _lead(pool, empresa="Sem Conversa Ltda", whatsapp="86955554444")
    _conversa(pool, lid, "whatsapp")
    card = _trecho_card(_kanban_html(monkeypatch, pool), "Sem Conversa Ltda")
    assert "kbmsg" not in card


def test_mensagem_de_conversa_de_outra_conta_nao_vaza(monkeypatch, pool):
    """Mesmo isolamento do selo: a prévia lê por conta_id, não por prospeccao_id."""
    _com_mensagens(pool)
    lid = _lead(pool, empresa="Vizinha Ltda", whatsapp="86944443333")
    with pool.connection() as c:
        cid = c.execute("insert into conversas (conta_id, prospeccao_id, canal) "
                        "values (999,%s,'whatsapp') returning id", (lid,)).fetchone()[0]
        c.commit()
    _msg(pool, cid, "in", "segredo da conta vizinha")
    card = _trecho_card(_kanban_html(monkeypatch, pool), "Vizinha Ltda")
    assert "segredo da conta vizinha" not in card
