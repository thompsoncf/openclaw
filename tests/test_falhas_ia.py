"""Tratamento de falha da IA (core.falhas): o cliente NUNCA ve o erro tecnico.

Garante que o erro de SALDO da Anthropic (e qualquer outra falha do LLM) vira
uma mensagem amigavel pro cliente e dispara UM aviso ao admin (com anti-spam),
sem depender de banco, SMTP ou rede. Funcoes puras / com dependencia mockada.
"""
import core.falhas as falhas
import finance.notificar as notificar


# erro REAL que o cliente recebeu (o que motivou este tratamento)
ERRO_SALDO = (
    "Error code: 400 - {'type': 'error', 'error': {'type': "
    "'invalid_request_error', 'message': 'Your credit balance is too low to "
    "access the Anthropic API. Please go to Plans & Billing to upgrade or "
    "purchase credits.'}, 'request_id': 'req_011CdLyfzDUmB8XE9YFH8MhC'}"
)


def test_detecta_falha_de_saldo():
    assert falhas.eh_falha_saldo(ERRO_SALDO) is True
    assert falhas.eh_falha_saldo(Exception(ERRO_SALDO)) is True


def test_falha_generica_nao_e_saldo():
    assert falhas.eh_falha_saldo("Connection reset by peer") is False
    assert falhas.eh_falha_saldo(TimeoutError("read timeout")) is False


def test_mensagem_pro_cliente_nao_vaza_nada_tecnico():
    msg = falhas.mensagem_para_cliente(ERRO_SALDO)
    assert msg == falhas.MSG_INSTABILIDADE
    baixo = msg.lower()
    for termo in ("credit", "balance", "400", "anthropic", "billing", "req_"):
        assert termo not in baixo, f"vazou termo tecnico: {termo}"


def test_tratar_falha_avisa_admin_uma_vez_e_devolve_msg(monkeypatch):
    # zera o throttle e mocka o aviso ao admin (sem SMTP/Telegram/rede)
    monkeypatch.setattr(falhas, "_ultimo_aviso_ts", None)
    chamadas = []
    monkeypatch.setattr(
        notificar, "avisar_admin_saldo_ia",
        lambda detalhe="", canal="": chamadas.append((canal, detalhe)) or True,
    )

    m1 = falhas.tratar_falha_ia(ERRO_SALDO, canal="whatsapp")
    m2 = falhas.tratar_falha_ia(ERRO_SALDO, canal="whatsapp")  # anti-spam

    assert m1 == m2 == falhas.MSG_INSTABILIDADE
    assert len(chamadas) == 1, "anti-spam falhou: deveria avisar o admin so' 1x"
    assert chamadas[0][0] == "whatsapp"


def test_tratar_falha_generica_nao_avisa_admin(monkeypatch):
    monkeypatch.setattr(falhas, "_ultimo_aviso_ts", None)
    chamadas = []
    monkeypatch.setattr(
        notificar, "avisar_admin_saldo_ia",
        lambda detalhe="", canal="": chamadas.append((canal, detalhe)) or True,
    )
    msg = falhas.tratar_falha_ia("Connection reset by peer", canal="telegram")
    assert msg == falhas.MSG_INSTABILIDADE
    assert chamadas == [], "falha generica nao deve acionar aviso de saldo"
