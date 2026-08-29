"""A hora na conversa, como no WhatsApp.

O PEDIDO (28/08/2026)
"Coloca a hora no chat da conversa que foi aberta a última vez ou mensagem enviada
igual tem no WhatsApp."

Até aqui a conversa do Cockpit era uma fila SEM TEMPO. Dava pra ler tudo e não
saber se a última mensagem do cliente tinha chegado às 8h da manhã ou três semanas
atrás — e a única saída era abrir o WhatsApp no celular, que é o hábito que este
trabalho todo veio quebrar.

DUAS COISAS, e as duas são do WhatsApp:
  a HORA em cada bolha (HH:MM, no rodapé, à direita);
  a TARJA DO DIA (HOJE / ONTEM / a data), que só entra quando o dia VIRA.

Sem a tarja, a hora sozinha mente por omissão: "20:28" não diz o dia. Com a tarja
repetida em toda bolha, ela vira ruído. Só na virada é o que funciona.

O FUSO É RESOLVIDO NO SERVIDOR, e isso é decisão. O banco guarda em UTC; se a
conversão ficasse no navegador, a mensagem das 20:28 apareceria como 23:28 pra
quem estivesse com o celular em UTC — e o vendedor de Teresina leria a hora errada
no meio de um atendimento.
"""
import inspect
from datetime import datetime, timedelta, timezone

from web import painel_cockpit as pc


def _quando(**kw):
    from finance import agenda as ag
    return datetime.now(ag.BRT) + timedelta(**kw)


# ------------------------------------------------------------------ a hora

def test_a_hora_sai_em_horario_de_brasilia():
    """O banco guarda UTC. Sem o fuso, 20:28 de Teresina vira 23:28 na tela."""
    from finance import agenda as ag
    d = datetime(2026, 8, 28, 23, 28, tzinfo=timezone.utc)
    assert pc._hora_br(d) == "20:28"
    assert pc._hora_br(d.astimezone(ag.BRT)) == "20:28"


def test_hora_de_lixo_nao_estoura():
    """Mensagem sem data não pode derrubar a conversa inteira."""
    for v in (None, "", 0, "ontem"):
        assert pc._hora_br(v) == ""
        assert pc._dia_br(v) == ""


# ------------------------------------------------------------- a tarja do dia

def test_hoje_e_ontem_tem_nome():
    assert pc._dia_br(_quando(hours=-1)) == "HOJE"
    assert pc._dia_br(_quando(days=-1)) == "ONTEM"


def test_mais_velho_que_ontem_vira_data():
    d = pc._dia_br(_quando(days=-9))
    assert d not in ("HOJE", "ONTEM")
    assert len(d) == 10 and d[2] == "/" and d[5] == "/", f"esperava dd/mm/aaaa, veio {d!r}"


def test_a_tarja_so_entra_quando_o_dia_vira():
    """Repetida em toda bolha ela vira ruído — e ruído deixa de ser lido."""
    fonte = inspect.getsource(pc._lead_vendedor)
    assert 'if dia and dia != dia_atual:' in fonte
    assert "dia_atual = dia" in fonte


# ----------------------------------------------- as duas cópias, servidor e polling

def test_a_hora_aparece_nas_DUAS_cargas():
    """O Cockpit desenha a conversa duas vezes: no HTML da primeira carga e no JS
    do polling. Numa cópia só, a hora apareceria ao abrir e sumiria no que chega
    depois (ou o contrário) — e ninguém entende por quê."""
    fonte = inspect.getsource(pc)
    assert "<span class=hora>" in fonte, "primeira carga (servidor)"
    assert "'<span class=hora>'+txt(m.hora)+'</span>'" in fonte, "polling (JS)"


def test_a_tarja_aparece_nas_DUAS_cargas():
    fonte = inspect.getsource(pc)
    assert "<div class=diadia>" in fonte, "servidor"
    assert "sep.className='diadia'" in fonte, "polling"


def test_o_polling_recebe_hora_e_dia_prontos():
    """Prontos do SERVIDOR: o celular do vendedor pode estar em qualquer fuso, ou
    com a hora errada, e a conversa tem que mostrar o mesmo horário pra todos."""
    fonte = inspect.getsource(pc.cockpit_lead_mensagens)
    assert '"hora": _hora_br(m.get("quando"))' in fonte
    assert '"dia": _dia_br(m.get("quando"))' in fonte


def test_o_polling_nao_repete_a_tarja_que_ja_esta_na_tela():
    """Sem ler a última tarja desenhada pelo servidor, a primeira mensagem do
    polling poria um 'HOJE' logo abaixo de outro 'HOJE'."""
    fonte = inspect.getsource(pc)
    assert "chat.querySelectorAll('.diadia')" in fonte
    assert "tarjas[tarjas.length-1].textContent" in fonte


def test_a_hora_nao_atropela_o_texto_nem_a_midia():
    """A bolha continua mostrando o que mostrava: rótulo, mídia e texto. A hora é
    o ÚLTIMO pedaço, e some quando não há data."""
    fonte = inspect.getsource(pc._lead_vendedor)
    i = fonte.index("{rot}{_midia_html(lead_id, m)}")
    assert "{esc(m['texto'])}{selo}" in fonte[i:i + 120]
    assert 'selo = f"<span class=hora>' in fonte
    assert 'if hora else ""' in fonte, "sem data, sem selo — e a bolha fica como era"


# ------------------------------------------------------------------- o desenho

def test_a_hora_flutua_pra_caber_na_ultima_linha():
    """`float:right` e não flex: a bolha tem texto corrido, e flutuando a hora se
    encaixa na última linha quando cabe e desce sozinha quando não cabe — que é o
    comportamento do WhatsApp."""
    css = pc._CSS_TEXTO
    i = css.index(".bub .hora{")
    assert "float:right" in css[i:i + 200]
    assert "nowrap" in css[i:i + 200], "a hora não pode quebrar no meio"
