"""A FAIXA DE VENCIMENTO no painel do cliente — e a chave que a cala no beta.

POR QUE ISTO EXISTE. Durante o beta grátis ninguém é cobrado, mas o vencimento
do plano continua correndo no banco. O resultado: contas "vencidas" que estão
usando o sistema de graça, e com a nossa bênção, ganhavam uma faixa vermelha
pedindo pagamento em toda tela. Ruído puro enquanto a cobrança não começou — e
ruído constante ensina o cliente a ignorar faixa vermelha, justo antes de
existir uma que importa.

DUAS COISAS QUE ESTE ARQUIVO GUARDA, e que são fáceis de quebrar sem perceber:

1. A chave só decide o que APARECE. Nunca libera nem corta acesso: quem decide
   isso é contas.acesso_liberado, e ele não lê esta chave. Um teste aqui cruza
   os dois de propósito.

2. Calar só vale com o BETA LIGADO. São duas chaves que precisam ser mexidas na
   ordem certa pra começar a cobrar, e a segunda é exatamente a que se esquece.
   O preço do esquecimento seria cortar o acesso de quem nunca foi avisado —
   então o código não depende de ninguém lembrar.

`_plano_aviso` é pura: a decisão inteira é testada sem banco e sem tela.
"""
from datetime import date, timedelta

import pytest

from contas.contas import Conta, acesso_liberado
from web.portal import _plano_aviso

ONTEM = date.today() - timedelta(days=1)
DAQUI_3 = date.today() + timedelta(days=3)
DAQUI_60 = date.today() + timedelta(days=60)


def conta(status="ativa", vencimento=None):
    """A tupla que o conta_logada devolve — [5]=status, [6]=vencimento."""
    return (1, "pf", "Thompson", None, "pf_familia", status, vencimento)


# ------------------------------------------------ o comportamento de sempre
# (avisar=True é o padrão: nada aqui muda pra quem não mexer na chave)

def test_em_dia_nao_mostra_nada():
    assert _plano_aviso(conta(vencimento=DAQUI_60), True) is None


def test_vencida_no_beta_avisa_sem_cortar():
    a = _plano_aviso(conta(vencimento=ONTEM), True)
    assert a["nivel"] == "vencido" and a["cortado"] is False


def test_vencida_sem_beta_avisa_e_diz_que_cortou():
    a = _plano_aviso(conta(vencimento=ONTEM), False)
    assert a["nivel"] == "vencido" and a["cortado"] is True


def test_vence_em_tres_dias_avisa_em_amarelo():
    a = _plano_aviso(conta(vencimento=DAQUI_3), True)
    assert a["nivel"] == "avencer" and a["dias"] == 3


@pytest.mark.parametrize("status", ["suspensa", "cancelada"])
def test_suspensa_e_cancelada_avisam_mesmo_no_beta(status):
    a = _plano_aviso(conta(status=status, vencimento=DAQUI_60), True)
    assert a["nivel"] == "vencido" and a["cortado"] is True


# ------------------------------------------------ com a chave CALADA

@pytest.mark.parametrize("venc", [ONTEM, DAQUI_3], ids=["vencida", "a-vencer"])
def test_calada_no_beta_nao_mostra_faixa(venc):
    assert _plano_aviso(conta(vencimento=venc), True, avisar=False) is None


@pytest.mark.parametrize("status", ["suspensa", "cancelada"])
def test_calada_cala_ATE_suspensa_e_cancelada(status):
    """Decisão explícita do dono: "cala tudo, até conta suspensa".

    O preço disso está registrado aqui pra não ser redescoberto por acidente —
    um cliente suspenso vê o SAC parado e a tela não explica por quê. A trava do
    beta (abaixo) é o que impede isso de virar permanente."""
    assert _plano_aviso(conta(status=status, vencimento=ONTEM),
                        True, avisar=False) is None


def test_em_dia_continua_sem_faixa_com_a_chave_calada():
    assert _plano_aviso(conta(vencimento=DAQUI_60), True, avisar=False) is None


# ------------------------------------------------ A TRAVA: calar exige beta

@pytest.mark.parametrize("status,venc", [
    ("ativa", ONTEM), ("ativa", DAQUI_3),
    ("suspensa", DAQUI_60), ("cancelada", DAQUI_60),
])
def test_sem_beta_a_chave_calada_nao_tem_efeito(status, venc):
    """O dia de começar a cobrar. Desligou o beta, os avisos voltam sozinhos —
    mesmo que a chave tenha ficado calada, mesmo que ninguém lembre dela."""
    calado = _plano_aviso(conta(status=status, vencimento=venc), False, avisar=False)
    falando = _plano_aviso(conta(status=status, vencimento=venc), False, avisar=True)
    assert calado is not None, "a chave calou um aviso com o beta desligado"
    assert calado == falando, "com beta desligado, calar tem que ser inócuo"


# ------------------------------------------------ a fronteira que não se cruza

@pytest.mark.parametrize("beta", [True, False])
@pytest.mark.parametrize("status", ["ativa", "suspensa", "cancelada"])
def test_calar_o_aviso_nao_mexe_em_quem_entra(beta, status):
    """A faixa é TELA; o acesso é `contas.acesso_liberado`, que nem conhece esta
    chave. Se um dia alguém for tentado a decidir acesso a partir do banner,
    este teste cai — e é pra cair."""
    c = Conta(id=1, tipo="pf", nome="T", documento=None, plano="pf_familia",
              status=status, vencimento=ONTEM, limite_mensagens_dia=50,
              limite_cupons_dia=5, cidade=None)
    antes = acesso_liberado(c, beta_ativo=beta)
    _plano_aviso(conta(status=status, vencimento=ONTEM), beta, avisar=False)
    assert acesso_liberado(c, beta_ativo=beta) is antes


# ------------------------------------------------ o padrão da configuração

def test_sem_configuracao_o_padrao_e_AVISAR(monkeypatch):
    """O inverso do beta, de propósito. O beta assume "grátis" porque cobrar sem
    querer é pior; aqui é ao contrário — deixar de avisar sem querer é o que
    cobra caro."""
    from finance import config_app as cfg
    monkeypatch.setattr(cfg, "get_config", lambda pool, chave, padrao=None: padrao)
    assert cfg.aviso_vencimento_ativo(None) is True


@pytest.mark.parametrize("valor,esperado", [
    ("on", True), ("off", False), ("ON", True), (" off ", False), ("", True),
])
def test_leitura_da_chave_tolera_o_que_o_banco_devolve(monkeypatch, valor, esperado):
    from finance import config_app as cfg
    monkeypatch.setattr(cfg, "get_config", lambda pool, chave, padrao=None: valor)
    assert cfg.aviso_vencimento_ativo(None) is esperado


# ------------------------------------------------ o botão no /admin
#
# O template do admin é uma string Jinja: um {% if %} torto não quebra import
# nenhum, só desenha o cartão errado — e o cartão errado aqui é o dono achando
# que calou quando não calou.

def _cartao(beta, avisa):
    """Renderiza a home do admin e devolve (rótulo, texto do botão, desabilitado)."""
    import re
    from web import admin
    html = admin._env.get_template("ahome").render(
        beta_gratis=beta, avisa_venc=avisa, brl=lambda x: "R$ 0,00",
        resumo={"total": 0, "trial": 0, "ativa": 0, "vencendo": 0, "mrr": 0},
        planos_admin=[], modulos_admin=[], contas=[], aviso="", busca="",
        leads_forn=0)
    assert "/admin/aviso-vencimento" in html, "o cartão sumiu da página"
    b = html.split('action="/admin/aviso-vencimento"')[1].split("</form>")[0]
    return (re.search(r"Aviso de vencimento[^<]*", b).group(0).strip(),
            re.search(r">([^<>]+)</button>", b).group(1).strip(),
            "disabled" in b)


def test_admin_beta_ligado_oferece_calar():
    rot, btn, dis = _cartao(beta=True, avisa=True)
    assert rot.endswith("aparecendo") and btn == "Calar o aviso" and not dis


def test_admin_calado_oferece_voltar_a_avisar():
    rot, btn, dis = _cartao(beta=True, avisa=False)
    assert rot.endswith("calado") and btn == "Voltar a avisar" and not dis


@pytest.mark.parametrize("avisa", [True, False])
def test_admin_sem_beta_o_botao_fica_inerte_e_explica(avisa):
    """Com o beta desligado o controle não some — ele diz por que não vale.
    Um controle que some deixa a pessoa procurando; um que se explica, não."""
    rot, btn, dis = _cartao(beta=False, avisa=avisa)
    assert dis, "o botão devia estar desabilitado com o beta desligado"
    assert btn == "sem efeito agora"
    if not avisa:
        assert "(sem efeito)" in rot
