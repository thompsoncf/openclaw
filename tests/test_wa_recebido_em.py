"""O carimbo de hora que vem do wa-qr, e o TETO que impede ele de estragar a caixa.

POR QUE ISTO EXISTE. Em 15/09/2026 a conta 38 foi pareada e o sync de histórico
dela derrubou o web; nos ~2 minutos de 502 o wa-qr perdeu três mensagens de
cliente, porque o repasse era um `fetch` único sem retentativa. O conserto é a
fila do lado Node (migração 261): a mensagem fica gravada e entra quando o web
voltar.

Só que uma entrega que atrasa minutos não pode entrar com `criado_em = now()` —
a conversa sairia fora de ordem no painel, com a mensagem que chegou primeiro
aparecendo depois. Daí o `recebido_em`.

E daí o TETO, que é o que este arquivo protege. O Baileys REENTREGA mensagem
antiga quando a conexão oscila, com o timestamp original. Aceitar qualquer data
ressuscitaria conversa no lugar errado da caixa — pior que o problema que o
campo veio resolver. Então só vale uma janela; fora dela, `now()`, como sempre.
"""
from datetime import datetime, timedelta, timezone

from web.painel_prospeccao import WA_RECEBIDO_EM_JANELA_H, _wa_recebido_em


def _iso(quando):
    return quando.isoformat().replace("+00:00", "Z")


def test_hora_recente_e_aceita():
    """O caso normal: a mensagem ficou minutos na fila e entra com a hora do envio."""
    ha_dez_min = datetime.now(timezone.utc) - timedelta(minutes=10)
    lido = _wa_recebido_em(_iso(ha_dez_min))
    assert lido is not None
    assert abs((lido - ha_dez_min).total_seconds()) < 2


def test_sem_valor_devolve_none():
    """Sem carimbo (Node velho, mensagem sem timestamp) o insert cai no now()."""
    for vazio in (None, "", 0):
        assert _wa_recebido_em(vazio) is None


def test_lixo_nao_derruba_o_webhook():
    """Data podre é `None`, nunca exceção: o webhook não pode cair por causa disso."""
    for lixo in ("ontem", "2026-13-45T99:99:99Z", "{}", [], {"a": 1}):
        assert _wa_recebido_em(lixo) is None


def test_futuro_e_recusado():
    """Celular com relógio adiantado jogaria a mensagem pro topo da caixa pra sempre."""
    daqui_uma_hora = datetime.now(timezone.utc) + timedelta(hours=1)
    assert _wa_recebido_em(_iso(daqui_uma_hora)) is None


def test_pequeno_adiantamento_passa():
    """Alguns minutos à frente é desencontro de relógio normal, não erro."""
    daqui_dois_min = datetime.now(timezone.utc) + timedelta(minutes=2)
    assert _wa_recebido_em(_iso(daqui_dois_min)) is not None


def test_reentrega_antiga_e_recusada():
    """O CASO QUE MOTIVOU O TETO.

    O Baileys reentrega mensagem velha como 'append' quando a conexão oscila. Sem
    o teto, uma conversa de semanas atrás voltaria pro meio da caixa com a data
    original — e o vendedor veria ordem trocada sem entender por quê.
    """
    ha_muito = datetime.now(timezone.utc) - timedelta(hours=WA_RECEBIDO_EM_JANELA_H + 1)
    assert _wa_recebido_em(_iso(ha_muito)) is None


def test_a_borda_da_janela():
    """Logo dentro passa, logo fora não — a janela é de verdade, não decorativa."""
    agora = datetime.now(timezone.utc)
    dentro = agora - timedelta(hours=WA_RECEBIDO_EM_JANELA_H) + timedelta(minutes=5)
    fora = agora - timedelta(hours=WA_RECEBIDO_EM_JANELA_H) - timedelta(minutes=5)
    assert _wa_recebido_em(_iso(dentro)) is not None
    assert _wa_recebido_em(_iso(fora)) is None


def test_sem_fuso_e_lido_como_utc():
    """O Node manda ISO com Z, mas um ISO sem fuso não pode virar hora local calada."""
    ingenuo = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None)
    lido = _wa_recebido_em(ingenuo.isoformat())
    assert lido is not None and lido.tzinfo is not None
