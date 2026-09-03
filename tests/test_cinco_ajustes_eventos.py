"""Os quatro primeiros dos cinco ajustes pedidos pro nicho eventos (02/09/2026).

O pedido do dono foram cinco itens; o quinto (termo aditivo) espera o modelo em
papel pra ser transcrito, e não está aqui. Os quatro que estão têm uma coisa em
comum, e é o que torna este arquivo um arquivo só: **três deles são a mesma
doença — a coisa existe e não fala.**

  1. DISTRIBUIÇÃO. O rodízio da conta 34 repartiu 68 leads em 7 dias, equilibrado
     (23/24/21), e `avisar` estava `false` desde 31/08. O vendedor nunca soube de
     nenhum: descobria abrindo o painel. A coluna nasce `default true` na 132 —
     ou seja, foi um formulário salvo com a chave apagada, e a tela não tinha como
     denunciar. Agora tem, e é o espelho do alerta que já existia pro caso
     inverso (fila montada, distribuição desligada).

  2. CHOQUE DE DATA. `choques_de_data` detecta e o painel mostra; o que faltava
     era encerrar a pergunta. Dos 10 dias com mais de um compromisso na Prime, 9
     já passaram, e o único futuro (12/09: visita 10:00 + aniversário 19:00) é a
     rotina da casa. O cabeçalho promete "some daqui sozinho quando você
     resolver" e não cumpria, porque não havia o que resolver.

  3. FUNIL. O card tinha o botão 💬 e não dizia nada do que havia dentro. 253
     leads com conversa, 192 com mensagem não vista, todos com a mesma cara de
     quem nunca falou.

  4. EXCLUIR. Este é o único que não é "existe e não fala": é porta que faltava.
     Só havia cancelar (`status='cancelado'`), que é outra coisa — cancelar é um
     fato do negócio, excluir é dizer que a linha nunca deveria ter existido.
     O que este bloco protege de verdade é a TRAVA, por causa dos dois `on delete
     cascade` que apontam pra `eventos_agenda`: apagar leva junto a lista de
     convidados e o log de convites/lembretes enviados ao cliente, e isso é
     informação do cliente (regra 0). Medido na Prime: 52 dos 66 compromissos
     passam pela trava, então ela não engessa o botão.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

from finance import agenda as ag
from web import painel_agenda as pa
from web import painel_prospeccao as pp

CONTA = 11
OUTRA = 12
BRT = timezone(timedelta(hours=-3))

_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text,
  email text, ativo boolean default true);
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  lembrete_min int, tipo text default 'pessoal', link_online text, desfecho text,
  status text default 'ativo', criado_em timestamptz default now(),
  pre_reserva_ate timestamptz, sinal_centavos int, tipo_evento text, convidados int,
  hora_sugerida boolean default false);
create table orcamentos (id bigserial primary key, conta_id bigint,
  evento_agenda_id bigint);
create table evento_convidados (id bigserial primary key,
  evento_id bigint not null references eventos_agenda(id) on delete cascade,
  nome text);
create table agenda_mensagens_log (id bigserial primary key,
  evento_id bigint references eventos_agenda(id) on delete cascade, tipo text);
create table agenda_dia_conferido (conta_id bigint not null, dia date not null,
  eventos bigint[] not null default '{}', marcado_em timestamptz not null default now(),
  marcado_por text not null default '', primary key (conta_id, dia));
"""


@pytest.fixture
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_cinco_ajustes"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into contas (id, nome) values (%s,'Prime'), (%s,'Vizinha')",
                  (CONTA, OUTRA))
        c.commit()
    yield p
    p.close()


def _evento(pool, quando, titulo="Festa", *, conta=CONTA, status="ativo", sinal=None):
    with pool.connection() as c:
        eid = c.execute(
            "insert into eventos_agenda (conta_id, titulo, inicio, status, sinal_centavos) "
            "values (%s,%s,%s,%s,%s) returning id",
            (conta, titulo, quando, status, sinal)).fetchone()[0]
        c.commit()
    return eid


def _daqui(dias, hora=19):
    d = (datetime.now(BRT) + timedelta(days=dias)).replace(
        hour=hora, minute=0, second=0, microsecond=0)
    return d


# ======================================================================== 1
# O ALARME DA DISTRIBUIÇÃO CALADA
#
# É template, então o teste renderiza o TRECHO de verdade com jinja em vez de
# procurar a string no arquivo: procurar string provaria que o texto existe, não
# que ele aparece na hora certa — e a hora certa é o item inteiro.

def _bloco_alerta_calado() -> str:
    """Recorta do _COMUNICACAO_TPL o {% if %} do alarme novo, pra renderizar
    sozinho. Recorte por marcador, e não por linha, pra não quebrar quando a tela
    andar pra cima ou pra baixo."""
    tpl = pp._COMUNICACAO_TPL
    ini = tpl.index("{% if dist_cfg and dist_cfg.ativo and not dist_cfg.avisar %}")
    fim = tpl.index("{% endif %}", ini) + len("{% endif %}")
    return tpl[ini:fim]


def _render_alerta(**cfg) -> str:
    from jinja2 import Template
    return Template(_bloco_alerta_calado()).render(dist_cfg=cfg).strip()


def test_distribuindo_e_calado_a_tela_denuncia():
    """O caso da conta 34: repartindo e sem avisar ninguém."""
    html = _render_alerta(ativo=True, avisar=False)
    assert "ninguém é avisado" in html
    assert "só descobre" in html


def test_distribuindo_e_avisando_nao_alarma():
    assert _render_alerta(ativo=True, avisar=True) == ""


def test_desligado_nao_alarma_por_aqui():
    """Distribuição desligada já tem alerta PRÓPRIO (a fila montada sem repartir).
    Dois alarmes vermelhos ao mesmo tempo dizendo coisas diferentes sobre o mesmo
    formulário é ruído — cada estado tem o seu."""
    assert _render_alerta(ativo=False, avisar=False) == ""


def test_o_aviso_por_email_e_push_e_o_mesmo_interruptor():
    """`avisar` governa e-mail e push juntos (o WhatsApp tem chave própria desde a
    185). Se alguém separar os dois um dia, o texto do alarme fica mentindo."""
    from finance import distribuicao as dist
    import inspect
    fonte = inspect.getsource(dist.avisar_vendedor)
    assert 'if not cfg["avisar"]:' in fonte and "return" in fonte
    assert "enviar_push" in fonte and "enviar_aviso" in fonte


# ======================================================================== 2
# "ESTÁ CERTO" NO CHOQUE DE DATA

def test_dois_no_mesmo_dia_aparecem_como_choque(pool):
    dia = _daqui(10)
    _evento(pool, dia.replace(hour=10), "Visita")
    _evento(pool, dia.replace(hour=19), "Aniversário")
    ch = ag.choques_de_data(pool, CONTA)
    assert len(ch) == 1 and len(ch[0]["eventos"]) == 2


def test_esta_certo_cala_o_dia(pool):
    dia = _daqui(10)
    _evento(pool, dia.replace(hour=10), "Visita")
    _evento(pool, dia.replace(hour=19), "Aniversário")
    n = ag.marcar_dia_conferido(pool, CONTA, dia.date(), por="1")
    assert n == 2
    assert ag.choques_de_data(pool, CONTA) == []


def test_compromisso_novo_no_dia_conferido_traz_o_alerta_de_volta(pool):
    """A parte que justifica guardar os IDS e não só a data. Sem isso, a data mais
    perigosa da agenda — a que já tem gente marcada — ficaria calada pra sempre."""
    dia = _daqui(10)
    _evento(pool, dia.replace(hour=10), "Visita")
    _evento(pool, dia.replace(hour=19), "Aniversário")
    ag.marcar_dia_conferido(pool, CONTA, dia.date())
    assert ag.choques_de_data(pool, CONTA) == []
    _evento(pool, dia.replace(hour=22), "Festa que ninguém viu chegar")
    ch = ag.choques_de_data(pool, CONTA)
    assert len(ch) == 1 and len(ch[0]["eventos"]) == 3


def test_tirar_compromisso_do_dia_nao_traz_o_alerta_de_volta(pool):
    """Menos gente no mesmo dia nunca é notícia pior — o conjunto atual continua
    cabendo no que foi conferido."""
    dia = _daqui(10)
    a = _evento(pool, dia.replace(hour=10), "Visita")
    _evento(pool, dia.replace(hour=19), "Aniversário")
    _evento(pool, dia.replace(hour=22), "Confraternização")
    ag.marcar_dia_conferido(pool, CONTA, dia.date())
    with pool.connection() as c:
        c.execute("update eventos_agenda set status='cancelado' where id=%s", (a,))
        c.commit()
    assert ag.choques_de_data(pool, CONTA) == []


def test_esta_certo_nao_apaga_nem_cancela_nada(pool):
    """Regra 0. A marca é ao LADO da agenda; o compromisso continua inteiro."""
    dia = _daqui(10)
    _evento(pool, dia.replace(hour=10), "Visita")
    _evento(pool, dia.replace(hour=19), "Aniversário")
    ag.marcar_dia_conferido(pool, CONTA, dia.date())
    with pool.connection() as c:
        vivos = c.execute("select count(*) from eventos_agenda "
                          "where conta_id=%s and status='ativo'", (CONTA,)).fetchone()[0]
    assert vivos == 2


def test_a_marca_e_por_conta(pool):
    """Multi-tenant: conferir o dia na Prime não pode calar o mesmo dia da vizinha."""
    dia = _daqui(10)
    _evento(pool, dia.replace(hour=10), "Visita", conta=OUTRA)
    _evento(pool, dia.replace(hour=19), "Festa", conta=OUTRA)
    ag.marcar_dia_conferido(pool, CONTA, dia.date())
    assert len(ag.choques_de_data(pool, OUTRA)) == 1


def test_dia_conferido_duas_vezes_nao_quebra(pool):
    """Clique repetido é o normal de botão que não dá retorno visual imediato."""
    dia = _daqui(10)
    _evento(pool, dia.replace(hour=10), "Visita")
    _evento(pool, dia.replace(hour=19), "Festa")
    ag.marcar_dia_conferido(pool, CONTA, dia.date())
    ag.marcar_dia_conferido(pool, CONTA, dia.date())
    assert ag.choques_de_data(pool, CONTA) == []


def test_banco_sem_a_194_ainda_mostra_o_choque(pool):
    """Falhar pro lado de MOSTRAR. Perder o alerta é pior que repeti-lo — e o
    deploy roda a migração antes do código, mas o inverso já aconteceu."""
    dia = _daqui(10)
    _evento(pool, dia.replace(hour=10), "Visita")
    _evento(pool, dia.replace(hour=19), "Festa")
    with pool.connection() as c:
        c.execute("drop table agenda_dia_conferido")
        c.commit()
    assert len(ag.choques_de_data(pool, CONTA)) == 1


# ======================================================================== 4
# EXCLUIR ≠ CANCELAR

def test_excluir_apaga_de_verdade(pool):
    eid = _evento(pool, _daqui(5), "Teste que nunca deveria existir")
    assert ag.excluir_evento(pool, CONTA, eid) == {"ok": True, "motivo": ""}
    with pool.connection() as c:
        assert c.execute("select count(*) from eventos_agenda where id=%s",
                         (eid,)).fetchone()[0] == 0


def test_cancelar_continua_nao_apagando(pool):
    """Os dois são coisas diferentes e continuam separados — foi o que o dono
    pediu explicitamente ("quero os dois separados")."""
    eid = _evento(pool, _daqui(5))
    ag.cancelar_evento(pool, CONTA, eid)
    with pool.connection() as c:
        st = c.execute("select status from eventos_agenda where id=%s", (eid,)).fetchone()[0]
    assert st == "cancelado"


def test_com_orcamento_nao_apaga(pool):
    eid = _evento(pool, _daqui(5))
    with pool.connection() as c:
        c.execute("insert into orcamentos (conta_id, evento_agenda_id) values (%s,%s)",
                  (CONTA, eid))
        c.commit()
    assert ag.excluir_evento(pool, CONTA, eid) == {"ok": False, "motivo": "orcamento"}


def test_com_sinal_nao_apaga(pool):
    eid = _evento(pool, _daqui(5), sinal=150000)
    assert ag.excluir_evento(pool, CONTA, eid)["motivo"] == "sinal"


def test_com_convidado_nao_apaga(pool):
    """`evento_convidados` some por cascade — quem confirmou presença é
    informação do cliente."""
    eid = _evento(pool, _daqui(5))
    with pool.connection() as c:
        c.execute("insert into evento_convidados (evento_id, nome) values (%s,'Ana')", (eid,))
        c.commit()
    assert ag.excluir_evento(pool, CONTA, eid)["motivo"] == "convidado"


def test_com_mensagem_enviada_nao_apaga(pool):
    """`agenda_mensagens_log` também some por cascade — e ele é a prova de que
    convite ou lembrete SAIU pro cliente."""
    eid = _evento(pool, _daqui(5))
    with pool.connection() as c:
        c.execute("insert into agenda_mensagens_log (evento_id, tipo) values (%s,'convite')",
                  (eid,))
        c.commit()
    assert ag.excluir_evento(pool, CONTA, eid)["motivo"] == "mensagem"


def test_evento_de_outra_conta_nao_some(pool):
    eid = _evento(pool, _daqui(5), conta=OUTRA)
    assert ag.excluir_evento(pool, CONTA, eid) == {"ok": False, "motivo": "sumiu"}
    with pool.connection() as c:
        assert c.execute("select count(*) from eventos_agenda where id=%s",
                         (eid,)).fetchone()[0] == 1


def test_o_lote_concorda_com_a_trava(pool):
    """`excluiveis` desenha o botão e `excluir_evento` decide. Se as duas
    discordarem, o botão aparece e recusa — que é o pior dos dois mundos."""
    limpo = _evento(pool, _daqui(5), "Limpo")
    travados = []
    with pool.connection() as c:
        e1 = _evento(pool, _daqui(6), "Com orçamento")
        c.execute("insert into orcamentos (conta_id, evento_agenda_id) values (%s,%s)", (CONTA, e1))
        e2 = _evento(pool, _daqui(7), "Com sinal", sinal=1000)
        e3 = _evento(pool, _daqui(8), "Com convidado")
        c.execute("insert into evento_convidados (evento_id, nome) values (%s,'Ana')", (e3,))
        e4 = _evento(pool, _daqui(9), "Com mensagem")
        c.execute("insert into agenda_mensagens_log (evento_id, tipo) values (%s,'convite')", (e4,))
        c.commit()
        travados = [e1, e2, e3, e4]
    todos = [limpo] + travados
    assert ag.excluiveis(pool, CONTA, todos) == {limpo}
    for eid in travados:
        assert ag.excluir_evento(pool, CONTA, eid)["ok"] is False, eid


def test_so_o_dono_apaga():
    """A rota barra vendedor antes de chegar no módulo. O módulo não sabe de
    papel de propósito — quem sabe de sessão é a tela."""
    import inspect
    fonte = inspect.getsource(pa.agenda_excluir)
    assert 'ctx.get("papel")' in fonte and '!= "dono"' in fonte
    i_papel = fonte.index('ctx.get("papel")')
    i_chama = fonte.index("r = ag.excluir_evento")  # a chamada, não a citação no docstring
    assert i_papel < i_chama, "a checagem de dono tem que vir ANTES de apagar"


def test_o_botao_so_aparece_pro_dono_e_onde_a_trava_deixa():
    tpl = pa._AGENDA_TPL
    assert "{% if e.pode_excluir and pode_apagar %}" in tpl
    assert tpl.count('action="/painel/agenda/excluir"') == 2, (
        "a linha de próximos é repetida em dois cards; o botão tem que estar nos dois")


# ======================================================================== 3
# A ÚLTIMA MENSAGEM NO CARD DO FUNIL
#
# O board inteiro é testado em tests/test_kanban_chat.py (inclusive o caso de
# instalação SEM a tabela `mensagens`, que o fixture de lá não cria — é o que
# prova que o savepoint segura). Aqui ficam as duas réguas puras e as travas do
# reload, que não dependem de banco nenhum.

@pytest.mark.parametrize("seg,esperado", [
    (10, "agora"), (60 * 5, "5min"), (60 * 59, "59min"),
    (3600 * 3, "3h"), (3600 * 23, "23h"),
])
def test_quando_curto_do_dia(seg, esperado):
    agora = datetime(2026, 9, 2, 15, 0, tzinfo=timezone.utc)
    assert pp._quando_curto(agora - timedelta(seconds=seg), agora) == esperado


def test_quando_curto_vira_data_depois_de_ontem():
    """"há 9 dias" não ajuda ninguém a lembrar de qual conversa era."""
    agora = datetime(2026, 9, 2, 15, 0, tzinfo=BRT)
    assert pp._quando_curto(agora - timedelta(days=1), agora) == "ontem"
    assert pp._quando_curto(agora - timedelta(days=9), agora) == "24/08"


def test_quando_curto_sem_data_nao_inventa():
    assert pp._quando_curto(None) == ""


def test_resumo_corta_e_junta_linhas():
    """Quebra de linha vira espaço: o corte no card sairia no meio de um
    parágrafo e o texto sumiria sem aviso nenhum."""
    assert pp._resumo_msg("oi\n\n  tudo   bem?") == "oi tudo bem?"
    assert pp._resumo_msg("a" * 80).endswith("…")
    assert len(pp._resumo_msg("a" * 80)) == 58
    assert pp._resumo_msg("curto") == "curto"


def test_o_card_mostra_a_mensagem_e_a_bolinha():
    tpl = pp._KANBAN_TPL
    assert "{% if c.ult %}" in tpl and "kbmsg" in tpl
    assert "{% if c.ult.nova %}" in tpl, "a bolinha tem que depender do não-visto"
    assert ".kbmsg{" in tpl, "o CSS do bloco tem que viajar junto com o markup"


def test_o_reload_do_funil_tem_os_freios():
    """Recarregar por baixo de quem está no meio de uma ação perde trabalho — e
    esta é a diferença pro Radar, que é uma lista e não uma mesa."""
    tpl = pp._KANBAN_TPL
    i = tpl.index("}, 60000);")
    bloco = tpl[tpl.index("setInterval(function(){", tpl.index("// ATUALIZAR SOZINHO")):i]
    for freio in ("document.hidden", ".chatpop", "_kbDragEl", "_kbMoved",
                  "getElementById('captar')", "activeElement", "isContentEditable"):
        assert freio in bloco, f"o reload perdeu o freio de {freio}"


def test_o_arrasto_solta_o_freio_no_fim():
    """`_kbDragEl` trava o reload. Se ele nunca for limpo, o funil para de
    atualizar pra sempre depois do primeiro arrasto — e ninguém descobriria."""
    assert "window._kbDragEl=null;" in pp._KANBAN_TPL
