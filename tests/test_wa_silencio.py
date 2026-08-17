"""finance/wa_silencio.py::deve_avisar — o alarme de "conectado e sem receber".

O caso real (Confeitaria Doce Mell, 17/08/2026): a sessão foi repareada do zero, o
cofre de chaves do Signal ficou horas se reconstruindo, e as mensagens chegavam como
frame sem virar conversa. O painel dizia CONECTADO o tempo todo. A empresa passou
mais de duas horas sem receber nada e ninguém soube — a faixa do painel avisava, mas
aviso em tela só serve pra quem está com a tela aberta.

Este teste guarda os três freios que impedem o alarme de virar spam. Eles importam
tanto quanto o alarme: alarme que toca à toa é alarme que o dono aprende a ignorar,
e aí ele não serve nem no dia que importa.

Teste puro — deve_avisar não toca banco nem rede.
"""
import pytest

from finance import wa_silencio as ws

EP = "2026-08-17T12:39:41+00:00"        # carimbo da última recebida (o "episódio")
OUTRO_EP = "2026-08-17T15:02:00+00:00"
HORA_OK = 14                             # dentro do horário comercial


def test_o_caso_da_doce_mell_dispara():
    """Os números reais de 17/08: 163min mudos, 468 recebidas em 7 dias (~11min entre
    mensagens). Quinze vezes o ritmo dela — é exatamente o alarme."""
    assert ws.deve_avisar(163, 468, HORA_OK, None, EP) is True


def test_a_conta_zaq_no_MESMO_silencio_nao_dispara():
    """O contra-exemplo que calibrou a regra, com números reais do mesmo instante: a
    ZAQ estava 165min sem receber — MAIS que a Doce Mell — e não é incidente, porque
    ela recebe 108 vezes em 7 dias (~47min entre mensagens). Mesma duração, ritmos
    diferentes: limiar fixo confundiria as duas."""
    assert ws.deve_avisar(165, 108, HORA_OK, None, EP) is False
    # e a mesma conta, parada tempo demais até pro ritmo dela, dispara
    assert ws.deve_avisar(600, 108, HORA_OK, None, EP) is True


def test_silencio_curto_nao_dispara():
    """Uma hora quieta é almoço, não incidente. E o vigia do wa-qr ainda tenta
    religar sessão muda sozinho antes disso."""
    assert ws.deve_avisar(45, 468, HORA_OK, None, EP) is False
    assert ws.deve_avisar(ws._SILENCIO_MIN - 1, 5000, HORA_OK, None, EP) is False


def test_piso_protege_a_conta_muito_movimentada():
    """Ritmo de 4min entre mensagens daria um limiar de 24min — alarme a cada respiro
    do cliente. O piso segura isso."""
    assert ws.limiar_de_silencio(1219) == ws._SILENCIO_MIN
    assert ws.deve_avisar(30, 1219, HORA_OK, None, EP) is False
    assert ws.deve_avisar(120, 1219, HORA_OK, None, EP) is True


def test_conta_parada_nao_vira_alarme():
    """Empresa que recebe pouco fica horas quieta sem nada de errado — pra ela o
    silêncio é o estado normal, e alarme no normal ninguém escuta."""
    assert ws.deve_avisar(300, 3, HORA_OK, None, EP) is False
    assert ws.deve_avisar(3000, 0, HORA_OK, None, EP) is False


@pytest.mark.parametrize("hora", [0, 3, 7, 20, 23])
def test_fora_do_horario_comercial_espera(hora):
    """Silêncio às 3h da manhã é o esperado. Acordar o dono pra dizer isso queima o
    alarme pra quando ele importar."""
    assert ws.deve_avisar(300, 468, hora, None, EP) is False


@pytest.mark.parametrize("hora", [8, 12, 19])
def test_dentro_do_horario_dispara(hora):
    assert ws.deve_avisar(300, 468, hora, None, EP) is True


def test_um_aviso_por_episodio():
    """O ticker roda a cada 2 minutos: sem dedup, o mesmo silêncio viraria dezenas de
    mensagens. O episódio é a última recebida — enquanto for a mesma, é o mesmo
    silêncio."""
    assert ws.deve_avisar(300, 468, HORA_OK, EP, EP) is False


def test_novo_silencio_depois_de_voltar_dispara_de_novo():
    """A conta voltou a receber (episódio mudou) e emudeceu outra vez: é incidente
    novo e tem que avisar. É o que faz o dedup se resetar sozinho, sem faxina."""
    assert ws.deve_avisar(300, 468, HORA_OK, EP, OUTRO_EP) is True


def test_sem_episodio_nao_avisa():
    """Conta que nunca recebeu nada não tem carimbo pra deduplicar — avisar aqui
    viraria repetição a cada volta do ticker."""
    assert ws.deve_avisar(300, 468, HORA_OK, None, None) is False


def test_nunca_recebeu_nao_e_silencio():
    """minutos=None é conta nova, não conta muda."""
    assert ws.deve_avisar(None, 468, HORA_OK, None, EP) is False


def test_texto_diz_o_que_fazer():
    """Aviso que só assusta não ajuda: tem que ter o teste (mandar mensagem de outro
    celular) e o que NÃO fazer (Desconectar apaga o cofre e custa horas)."""
    tg, email = ws._texto("Confeitaria Doce Mell", 133)
    for t in (tg, email):
        assert "Confeitaria Doce Mell" in t
        assert "outro celular" in t
        assert "Desconectar" in t
    assert "2 horas" in email and "2 horas" in tg
    assert "45 minutos" in ws._texto("X", 45)[1]
