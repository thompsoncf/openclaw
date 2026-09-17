"""O QUE O CLIENTE ESCREVEU ENCERRA O TURNO, OU PEDE RESPOSTA?

Este arquivo fixa a regra que decide se um lead ocupa o topo da coluna e se ele
pode virar 🚨 Crítico. Errar aqui pro lado ERRADO esconde uma venda, então os
casos abaixo são os de produção — as frases são as que os clientes da Prime (conta
34) realmente mandaram, copiadas do banco em 16/09/2026, não inventadas.
"""
import pytest

from finance import evento_lead as ev
from finance import fecho_de_conversa as fc
from finance import follow_up as fu

# as frases que o dono viu no quadro e que motivaram a mudança
FECHOS_REAIS = [
    "Obrigada!", "Bgd", "Ok", "Certo", "Tá certo", "Ta bom", "Está bem",
    "Adorei", "Assim entendi", "Ah sim, entendi", "Certo, obrigada",
    "Ok obrigada", "Ok. Obrigada", "Certo. Mt obg!", "Sim sim", "Okay💖",
    "Obrigada 🙏🏻", "Obrigada pelo atendimento", "Obrigada pela indicação",
    "Agradeço pela disponibilidade 🙏", "Certo, tudo bem", "Ta bom , obrigada",
    "Tudo bem, obrigada pelo atendimento!", "Até 🙌🏾", "Tranquilo",
]

# o que NÃO pode ser tratado como fecho — cada um esconderia um pedido real
PEDIDOS_REAIS = [
    "Esses valores inclui o que no pacote?",
    "Gostaria de marcar para conhecer o espaço",
    "Tem disponibilidade 29/09 'terça feira', 55 pessoas",
    "Vamos marcar depois uma visita no espaço",
    "26 de setembro",
    "horário de início: 19h",
    "Avenida presidente Kenedy, 4630",
    "Falo com você no decorrer do dia",
    "Bom dia!",
]


@pytest.mark.parametrize("texto", FECHOS_REAIS)
def test_cortesia_do_cliente_e_fecho(texto):
    assert fc.eh_fecho(texto), f"{texto!r} é cortesia e estava cobrando o vendedor"


@pytest.mark.parametrize("texto", PEDIDOS_REAIS)
def test_pedido_do_cliente_nunca_e_fecho(texto):
    assert not fc.eh_fecho(texto), f"{texto!r} pede resposta e sumiria do topo"


# ------------------------------------------------------------------ as três travas
def test_pergunta_vence_por_mais_curta_que_seja():
    """Trava 1. 'Ok?' é uma pergunta, não um fecho — e 'Obrigada. Qual o valor?'
    tem um fecho colado num pedido: o pedido manda."""
    assert not fc.eh_fecho("Ok?")
    assert not fc.eh_fecho("Obrigada. Qual o valor?")
    assert not fc.eh_fecho("Blz, mas qual o valor?")


def test_frase_longa_nunca_e_fecho():
    """Trava 2. Quem escreveu bastante disse alguma coisa, mesmo começando com ok."""
    longa = "Ok, mas eu queria entender melhor como funciona o pagamento parcelado"
    assert len(longa) > fc.LIMITE
    assert not fc.eh_fecho(longa)


def test_audio_foto_e_documento_nunca_sao_fecho():
    """Trava 3. Não dá pra saber o que tem dentro — e supor que um áudio é
    'obrigada' esconderia um pedido. Foi por um áudio assim que a Larissa Rakel
    estava no topo com a festa em 2028."""
    assert not fc.eh_fecho("🎤 Áudio (0:14)\nOi, Tiago. Boa tarde. Sim, domingo")
    assert not fc.eh_fecho("📷 Foto")
    assert not fc.eh_fecho("📄 Documento")
    assert not fc.eh_fecho("🩷 Figurinha")


def test_um_pedaco_desconhecido_derruba_a_frase_inteira():
    """A quebra por pontuação não afrouxa: TODOS os pedaços têm que ser fecho."""
    assert fc.eh_fecho("Ok. Obrigada")
    assert not fc.eh_fecho("Ok. Me manda o contrato")
    assert not fc.eh_fecho("Certo. Vou pensar")


def test_vazio_e_nulo_nao_param_cobranca():
    """Sem texto não há como saber, e campo vazio não pode virar motivo pra parar
    de cobrar — o silêncio do dado não é o silêncio do cliente."""
    assert not fc.eh_fecho("")
    assert not fc.eh_fecho(None)
    assert not fc.eh_fecho("   ")


def test_o_vocabulario_e_legivel_e_sem_acento():
    """Ele existe pra o dono corrigir. Uma entrada com acento nunca casaria, porque
    a comparação normaliza os dois lados — seria uma linha morta na lista."""
    assert len(fc.FECHOS) > 50
    for f in fc.FECHOS:
        assert f == f.lower(), f"{f!r} tem maiúscula"
        assert not any(ch in f for ch in "áàâãéêíóôõúç"), f"{f!r} tem acento"


# ------------------------------------------------- o efeito onde o dono reclamou
def test_o_prazo_para_de_tratar_cortesia_como_bola_nossa():
    """O defeito, reproduzido: a Renata Costa com 'Obrigada!' de 7 dias atrás.

    Antes: prazo = ult_in + bola_nossa_min → vencido há 7 dias → 🚨 Crítico.
    Agora: cai na régua da proposta, que é a verdade — estamos esperando ELE.
    """
    from datetime import datetime, timedelta, timezone
    agora = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    cfg = dict(fu._PADRAO, sem_resposta_min=60, bola_nossa_min=60, fu_proposta_dias=3)
    comum = dict(status="proposta", ult_out=agora - timedelta(days=9),
                 ult_in=agora - timedelta(days=7), criado_em=agora - timedelta(days=30),
                 tentativas=1, evento_em=None, cfg=cfg, tem_data=False, agora=agora)

    prazo_pedido, acao_pedido = fu.prazo_automatico(**comum, ult_in_texto="Qual o valor?")
    assert "responder" in acao_pedido
    assert prazo_pedido < agora, "quem perguntou continua vencido — e tem que continuar"

    prazo_fecho, acao_fecho = fu.prazo_automatico(**comum, ult_in_texto="Obrigada!")
    assert "responder" not in acao_fecho
    assert prazo_fecho > prazo_pedido, "a cortesia tem que AFROUXAR o prazo, não apertar"


def test_sem_texto_o_prazo_continua_como_era():
    """Compatibilidade: quem chama sem o texto (e a conversa sem mensagem legível)
    não pode mudar de comportamento — errar aí seria parar de cobrar no escuro."""
    from datetime import datetime, timedelta, timezone
    agora = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    cfg = dict(fu._PADRAO, sem_resposta_min=60, bola_nossa_min=60, fu_proposta_dias=3)
    _, acao = fu.prazo_automatico(
        status="proposta", ult_out=agora - timedelta(days=9),
        ult_in=agora - timedelta(days=7), criado_em=agora - timedelta(days=30),
        tentativas=1, evento_em=None, cfg=cfg, tem_data=False, agora=agora)
    assert "responder" in acao


def test_o_funil_tira_a_cortesia_do_topo_da_coluna():
    """A outra ponta: `evento_lead.esperando_resposta` usa a MESMA regra."""
    def card(texto, minha=False):
        return {"ult": {"texto": texto, "minha": minha}}
    assert not ev.esperando_resposta(card("Obrigada!"))
    assert not ev.esperando_resposta(card("Ok"))
    assert ev.esperando_resposta(card("Qual o valor?"))
    assert ev.esperando_resposta(card("Gostaria de marcar uma visita"))
    assert not ev.esperando_resposta(card("Segue a proposta", minha=True))
    assert not ev.esperando_resposta({})


def test_as_duas_telas_nunca_discordam():
    """O motivo de existir UM módulo: se o funil e o follow-up tivessem cada um a
    sua lista, elas divergiriam no dia em que alguém corrigisse só uma."""
    for texto in FECHOS_REAIS + PEDIDOS_REAIS:
        do_funil = ev.esperando_resposta({"ult": {"texto": texto, "minha": False}})
        do_motor = not fc.eh_fecho(texto)
        assert do_funil == do_motor, f"as telas discordam sobre {texto!r}"
