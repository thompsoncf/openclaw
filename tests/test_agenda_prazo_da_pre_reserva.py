"""O prazo da pré-reserva acompanha a FESTA, não o relógio de hoje.

Nascido de nove datas da Prime Eventos canceladas sozinhas pelo prazo vencendo
(19/09/2026). O prazo dado era sempre de 4 a 6 dias, qualquer que fosse a
distância da festa — e o casamento da Maria Carolina venceu 312 dias antes dela.
"""
from datetime import datetime, timedelta

from finance import agenda as ag

AGORA = datetime(2026, 9, 19, 12, 0, tzinfo=ag.BRT)


def _festa(dias):
    return AGORA + timedelta(days=dias)


# ------------------------------------------------- o caso que gerou a regra
def test_o_casamento_de_dez_meses_nao_vence_em_cinco_dias():
    # Maria Carolina: festa em 24/07/27, prazo de 5 dias, venceu 312 dias antes.
    festa = datetime(2027, 7, 24, 17, 0, tzinfo=ag.BRT)
    ate = ag.prazo_da_pre_reserva(festa, dias_config=5, agora=AGORA)
    assert ate == festa - timedelta(days=60)
    assert (festa - ate).days == 60          # e não 312


def test_o_aniversario_de_dezessete_meses_tambem_nao():
    festa = datetime(2028, 2, 5, 21, 0, tzinfo=ag.BRT)
    ate = ag.prazo_da_pre_reserva(festa, dias_config=5, agora=AGORA)
    assert ate == festa - timedelta(days=60)


def test_o_prazo_antigo_teria_vencido_antes_em_todos_os_nove():
    """A regressão que a regra impede, escrita como asserção."""
    for dias_ate_a_festa in (79, 86, 138, 312, 505, 506):
        festa = _festa(dias_ate_a_festa)
        antigo = AGORA + timedelta(days=5)
        novo = ag.prazo_da_pre_reserva(festa, dias_config=5, agora=AGORA)
        assert novo > antigo, dias_ate_a_festa


# ------------------------------------------------- os três degraus da regra
def test_dias_config_e_o_piso_pra_festa_perto():
    # festa daqui a 10 dias: 60 dias antes já passou, então vale o piso da conta
    festa = _festa(10)
    assert ag.prazo_da_pre_reserva(festa, dias_config=5, agora=AGORA) == AGORA + timedelta(days=5)


def test_o_prazo_nunca_passa_da_festa():
    festa = _festa(2)                         # festa antes do piso de 5 dias
    assert ag.prazo_da_pre_reserva(festa, dias_config=5, agora=AGORA) == festa


def test_havendo_espaco_segura_ate_sessenta_dias_antes():
    festa = _festa(200)
    assert ag.prazo_da_pre_reserva(festa, dias_config=5, agora=AGORA) == festa - timedelta(days=60)


def test_a_folga_e_configuravel_pela_chamada():
    festa = _festa(200)
    ate = ag.prazo_da_pre_reserva(festa, dias_config=5, agora=AGORA, folga_dias=30)
    assert ate == festa - timedelta(days=30)


def test_folga_zero_segura_ate_a_propria_festa():
    """folga=0 é "solta zero dias antes da festa" — o limite da regra, não o antigo."""
    festa = _festa(200)
    ate = ag.prazo_da_pre_reserva(festa, dias_config=5, agora=AGORA, folga_dias=0)
    assert ate == festa


# ------------------------------------------------- bordas
def test_sem_data_de_festa_usa_o_piso():
    assert ag.prazo_da_pre_reserva(None, dias_config=5, agora=AGORA) == AGORA + timedelta(days=5)


def test_festa_no_passado_usa_o_piso_e_nao_nasce_vencida():
    festa = _festa(-30)
    ate = ag.prazo_da_pre_reserva(festa, dias_config=5, agora=AGORA)
    assert ate == AGORA + timedelta(days=5)
    assert ate > AGORA                        # o que importa: não nasce vencida


def test_dias_config_ausente_cai_no_padrao_do_codigo():
    festa = _festa(3)
    ate = ag.prazo_da_pre_reserva(festa, dias_config=None, agora=AGORA)
    assert ate == min(festa, AGORA + timedelta(days=ag.PRE_RESERVA_DIAS))


def test_dias_config_zero_cai_no_padrao_como_os_chamadores_ja_liam():
    """`dias_config or PRE_RESERVA_DIAS` é a mesma leitura que painel e proposta
    fazem da configuração da conta — zero ali sempre quis dizer "não escolheram"."""
    festa_perto = _festa(3)
    ate = ag.prazo_da_pre_reserva(festa_perto, dias_config=0, agora=AGORA)
    assert ate == min(festa_perto, AGORA + timedelta(days=ag.PRE_RESERVA_DIAS))


def test_dias_config_negativo_vira_um_dia_e_nao_explode():
    festa_perto = _festa(3)
    assert ag.prazo_da_pre_reserva(festa_perto, dias_config=-5, agora=AGORA) == AGORA + timedelta(days=1)
    festa_longe = _festa(400)
    assert ag.prazo_da_pre_reserva(festa_longe, dias_config=-5, agora=AGORA) == festa_longe - timedelta(days=60)


def test_a_folga_padrao_e_sessenta_dias():
    """O número que o dono escolheu em 19/09/2026 — se mudar, é decisão, não acidente."""
    assert ag.FOLGA_REVENDA_DIAS == 60


def test_sem_agora_usa_o_relogio_e_devolve_futuro():
    festa = ag.agora_brt() + timedelta(days=400)
    assert ag.prazo_da_pre_reserva(festa, dias_config=5) > ag.agora_brt()
