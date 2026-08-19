"""O ESTADO DA DATA na linha do funil — quatro, não um.

POR QUE ESTE ARQUIVO EXISTE. Em 19/08/2026 apareceu um orçamento aprovado que
não tinha virado pré-reserva na agenda. Lendo o fluxo, a reserva nasce num lugar
só (o cliente assinando o link público) e sai calada por quatro portas —
orçamento sem hora de início, exceção engolida, processo reiniciado antes da
tarefa de segundo plano rodar, e o prazo do sinal vencendo depois.

O problema não era só a porta: era que a linha do funil não sabia contar. Ela
desenhava um estado (a pré-reserva correndo) e os outros três ficavam iguais:

    reservada  compromisso firme          tudo certo
    fora       nunca entrou na agenda     PARECIA tudo certo
    liberada   entrou e o prazo venceu    PARECIA tudo certo

Duas dessas caras são problema, e a empresa vende DATA — perder uma sem saber é
o pior desfecho possível deste módulo.

`estado_da_data` é pura de propósito: a REDAÇÃO que o dono lê é testada aqui,
sem banco e sem tela.
"""
from datetime import date, datetime, timedelta

import pytest

from finance import agenda as ag
from finance import vendas as v

HOJE = date(2026, 8, 19)
FUTURO = "2026-12-31"
PASSADO = "2026-07-01"


def _e(**kw):
    base = dict(status="aprovada", modo="evento", evento={"data": FUTURO, "inicio": "19:00"},
                evento_status=None, pre_reserva_ate=None, hoje=HOJE)
    base.update(kw)
    return v.estado_da_data(**base)


# ------------------------------------------------------- os quatro estados

def test_compromisso_firme_diz_que_esta_tudo_certo():
    r = _e(evento_status="ativo")
    assert r["estado"] == v.DATA_RESERVADA
    assert r["texto"] == "Data reservada"
    assert r["acao"] is None            # não há o que consertar


def test_pre_reserva_correndo_mostra_o_prazo_e_pede_o_sinal():
    ate = datetime(2026, 8, 22, 18, 0, tzinfo=ag.BRT)
    r = _e(evento_status=ag.PRE_RESERVADO, pre_reserva_ate=ate)
    assert r["estado"] == v.DATA_SEGURADA
    assert r["texto"] == "Data segurada até 22/08 18:00"
    assert r["acao"] == "sinal"


def test_sem_compromisso_nenhum_e_o_estado_que_ninguem_via():
    """O caso que trouxe este arquivo: aprovado, e a data nunca entrou."""
    r = _e(evento_status=None)
    assert r["estado"] == v.DATA_FORA
    assert r["texto"] == "Fora da agenda"
    assert r["acao"] == "marcar"


def test_prazo_vencido_continua_visivel_em_vez_de_sumir():
    """Quando o prazo vence, o compromisso é cancelado e o selo âmbar some. Antes
    disto a linha voltava a ficar IDÊNTICA à de uma data firme — e o único aviso
    era um Telegram que podia passar batido."""
    r = _e(evento_status="cancelado")
    assert r["estado"] == v.DATA_LIBERADA
    assert r["texto"] == "Data liberada"
    assert r["acao"] == "resegurar"


# ------------------------------------------------- quando NÃO se fala da data

def test_data_que_ja_passou_nao_vira_alarme_eterno():
    """A lição do aviso do contrato, aplicada aqui: alarme que não tem conserto
    ensina o dono a ignorar o próximo. Evento de julho não tem o que salvar."""
    assert _e(evento={"data": PASSADO, "inicio": "19:00"}, evento_status=None) is None
    assert _e(evento={"data": PASSADO, "inicio": "19:00"}, evento_status="cancelado") is None


def test_a_data_de_hoje_ainda_conta():
    """A festa é hoje à noite e a data não está na agenda: é o dia em que MAIS
    importa avisar. O corte é `< hoje`, não `<= hoje`."""
    r = _e(evento={"data": HOJE.isoformat(), "inicio": "19:00"}, evento_status=None)
    assert r["estado"] == v.DATA_FORA


@pytest.mark.parametrize("status", ["rascunho", "enviado", "negociando", "perdido", ""])
def test_antes_do_aceite_a_data_nao_e_promessa(status):
    """Proposta enviada não reserva nada, por definição — cobrar reserva de um
    rascunho encheria o funil de alarme falso."""
    assert _e(status=status) is None


def test_orcamento_fechado_continua_falando_da_data():
    """Contrato fechado não faz a festa deixar de existir: a data segue tendo que
    estar na agenda."""
    assert _e(status="fechado", evento_status=None)["estado"] == v.DATA_FORA


def test_nicho_recorrente_nao_vende_data():
    assert _e(modo="recorrente", evento_status="ativo") is None


def test_orcamento_sem_data_nenhuma_e_sem_compromisso_nao_promete_nada():
    assert _e(evento={}, evento_status=None) is None
    assert _e(evento=None, evento_status=None) is None


# --------------------------------------------------------- o texto do conserto

def test_falta_a_hora_de_inicio_e_dito_por_extenso():
    """A porta mais larga das quatro, e a que tem conserto na mão do vendedor: o
    campo Início é livre e não é obrigatório. Dizer só "não entrou na agenda"
    mandaria ele procurar no escuro justamente o campo que falta."""
    r = _e(evento={"data": FUTURO, "inicio": ""}, evento_status=None)
    assert r["estado"] == v.DATA_FORA
    assert "hora de início" in r["dica"]
    assert "O evento" in r["dica"]


def test_com_a_hora_preenchida_a_dica_e_outra():
    r = _e(evento={"data": FUTURO, "inicio": "19:00"}, evento_status=None)
    assert "hora de início" not in r["dica"]
    assert "Marcar agora" in r["dica"]


def test_todo_estado_tem_texto_e_dica():
    """Selo sem explicação vira adivinhação — e estes quatro moram numa linha
    densa, onde o `title` é a única chance de explicar."""
    casos = [dict(evento_status="ativo"),
             dict(evento_status=ag.PRE_RESERVADO,
                  pre_reserva_ate=ag.agora_brt() + timedelta(days=3)),
             dict(evento_status="cancelado"),
             dict(evento_status=None)]
    for kw in casos:
        r = _e(**kw)
        assert r["texto"].strip() and r["dica"].strip()
        assert r["estado"] in (v.DATA_RESERVADA, v.DATA_SEGURADA,
                               v.DATA_FORA, v.DATA_LIBERADA)
