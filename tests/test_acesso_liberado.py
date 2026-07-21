"""Regra de acesso da conta (contas.acesso_liberado), incluindo o modo beta.

O beta grátis NÃO corta ninguém por vencimento (ninguém está pagando ainda):
com beta ligado, o acesso continua como cortesia — mas suspensa/cancelada
(decisões explícitas do admin) continuam bloqueadas mesmo no beta.
Função pura: sem banco.
"""
from datetime import date, timedelta

from contas.contas import Conta, acesso_liberado


def _conta(status, vencimento):
    return Conta(
        id=1, tipo="pf", nome="Thompson", documento=None, plano="pf_familia",
        status=status, vencimento=vencimento, limite_mensagens_dia=50,
        limite_cupons_dia=5, cidade=None,
    )


ONTEM = date.today() - timedelta(days=1)
AMANHA = date.today() + timedelta(days=1)


def test_ativa_em_dia_libera():
    assert acesso_liberado(_conta("ativa", AMANHA)) is True


def test_vencida_bloqueia_sem_beta():
    assert acesso_liberado(_conta("ativa", ONTEM)) is False


def test_beta_segura_vencida():
    # Caso Thompson: venceu, mas com beta ligado continua usando.
    assert acesso_liberado(_conta("ativa", ONTEM), beta_ativo=True) is True


def test_beta_segura_trial_vencido():
    assert acesso_liberado(_conta("trial", ONTEM), beta_ativo=True) is True


def test_beta_nao_ressuscita_suspensa():
    assert acesso_liberado(_conta("suspensa", AMANHA), beta_ativo=True) is False


def test_beta_nao_ressuscita_cancelada():
    assert acesso_liberado(_conta("cancelada", AMANHA), beta_ativo=True) is False


def test_sem_vencimento_libera():
    assert acesso_liberado(_conta("ativa", None)) is True
