"""Tratamento CENTRAL de falha de provedor externo (core.falhas).

Garante que:
- o cliente NUNCA ve o erro tecnico (msg amigavel);
- falhas SISTEMICAS (sem credito / chave invalida / quota) sao classificadas e
  disparam UM aviso ao admin (com anti-spam por servico+categoria);
- falhas transitorias (rede/5xx/timeout) NAO avisam o admin.
Tudo sem depender de banco, SMTP ou rede (dependencia mockada).
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


# --- classificacao -----------------------------------------------------------

def test_classifica_saldo_como_credito():
    assert falhas.classificar_falha(ERRO_SALDO) == falhas.CAT_CREDITO
    assert falhas.eh_falha_saldo(ERRO_SALDO) is True
    assert falhas.eh_falha_sistemica(ERRO_SALDO) is True


def test_classifica_auth_quota_indisponivel():
    assert falhas.classificar_falha("Asaas 401 em /payments: unauthorized") == falhas.CAT_AUTH
    assert falhas.classificar_falha("http_429: too many requests") == falhas.CAT_QUOTA
    assert falhas.classificar_falha("Asaas 503 em /x: service unavailable") == falhas.CAT_INDISPONIVEL
    assert falhas.classificar_falha("read timeout") == falhas.CAT_INDISPONIVEL


def test_insufficient_quota_e_credito_nao_quota():
    # OpenAI devolve 429 "insufficient_quota" quando o CREDITO acabou (nao rate limit)
    assert falhas.classificar_falha("Error: insufficient_quota") == falhas.CAT_CREDITO


def test_falha_generica_nao_e_sistemica():
    assert falhas.eh_falha_sistemica("Connection reset by peer") is False
    assert falhas.eh_falha_sistemica("Asaas 404 em /payments/x") is False  # 404 = nao existe


# --- mensagem pro cliente (no-leak) ------------------------------------------

def test_mensagem_pro_cliente_nao_vaza_nada_tecnico():
    msg = falhas.tratar_falha_externa(ERRO_SALDO, servico="Anthropic (IA)", canal="whatsapp")
    assert msg == falhas.MSG_INSTABILIDADE
    baixo = msg.lower()
    for termo in ("credit", "balance", "400", "anthropic", "billing", "req_", "401", "http"):
        assert termo not in baixo, f"vazou termo tecnico: {termo}"


# --- aviso ao admin + anti-spam ----------------------------------------------

def _mock_aviso(monkeypatch):
    monkeypatch.setattr(falhas, "_ultimo_aviso", {})  # zera o throttle
    chamadas = []
    monkeypatch.setattr(
        notificar, "avisar_admin_falha_provedor",
        lambda servico, categoria="credito", detalhe="", canal="":
            chamadas.append((servico, categoria, canal)) or True,
    )
    return chamadas


def test_avalia_saldo_avisa_admin_uma_vez_por_servico(monkeypatch):
    chamadas = _mock_aviso(monkeypatch)
    falhas.avaliar_falha_provedor(ERRO_SALDO, servico="Anthropic (IA)", canal="whatsapp")
    falhas.avaliar_falha_provedor(ERRO_SALDO, servico="Anthropic (IA)", canal="whatsapp")  # anti-spam
    assert len(chamadas) == 1
    assert chamadas[0] == ("Anthropic (IA)", falhas.CAT_CREDITO, "whatsapp")


def test_anti_spam_e_por_servico_e_categoria(monkeypatch):
    chamadas = _mock_aviso(monkeypatch)
    # servicos diferentes -> avisos independentes
    falhas.avaliar_falha_provedor("http_401 unauthorized", servico="Asaas")
    falhas.avaliar_falha_provedor("http_401 unauthorized", servico="Twilio (WhatsApp)")
    # mesma (servico, categoria) de novo -> silenciado
    falhas.avaliar_falha_provedor("http_401 unauthorized", servico="Asaas")
    assert len(chamadas) == 2
    assert {c[0] for c in chamadas} == {"Asaas", "Twilio (WhatsApp)"}
    assert all(c[1] == falhas.CAT_AUTH for c in chamadas)


def test_falha_transitoria_nao_avisa_admin(monkeypatch):
    chamadas = _mock_aviso(monkeypatch)
    falhas.avaliar_falha_provedor("read timeout", servico="Data Market")
    falhas.avaliar_falha_provedor("Asaas 503 em /x", servico="Asaas")
    assert chamadas == [], "falha transitoria (rede/5xx) nao deve avisar o admin"


def test_tratar_falha_ia_ainda_funciona(monkeypatch):
    chamadas = _mock_aviso(monkeypatch)
    msg = falhas.tratar_falha_ia(ERRO_SALDO, canal="telegram")
    assert msg == falhas.MSG_INSTABILIDADE
    assert len(chamadas) == 1
    assert chamadas[0][0] == "Anthropic (IA)"
