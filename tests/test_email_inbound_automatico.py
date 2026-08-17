"""finance/email_inbound.py::_eh_automatico — o inbox de Comunicação é pra atender
CLIENTE, e estava virando caixa de newsletter.

Diagnóstico que originou o filtro (conta 34, Prime Eventos): 61 e-mails puxados por
IMAP, 22 conversas abertas, ZERO lead de verdade. Era banco, rede social, boleto,
cobrança — e a própria notificação "Proposta aprovada" do Zaq, que sai pelo SMTP de
sistema pro e-mail da conta e o IMAP puxava de volta como se fosse mensagem de lead.

Os endereços de `_RUIDO` são os que estavam de fato no banco de produção. Os de
`_GENTE` são a outra metade do teste, e a que importa mais: filtro que engole
cliente é pior que inbox sujo — inclusive endereços de cara institucional
(contato@, relacionamento@, ola@), que uma regra ingênua por local-part mataria.

Teste puro (sem banco): _eh_automatico só olha o dicionário do e-mail.
"""
import pytest

from finance import email_inbound as ei


def _msg(addr: str, **cab) -> dict:
    m = {"from_email": addr, "assunto": "", "corpo": ""}
    m.update(cab)
    return m


# remetentes REAIS que abriram conversa no inbox da conta 34
_RUIDO = [
    ("todomundo@nubank.com.br", {"list_unsub": "<https://nubank.com.br/unsub>"}),
    ("todomundo@novidades.nubank.com.br", {}),
    ("noreply-accounts@google.com", {}),
    ("no-reply@accounts.google.com", {}),
    ("security@mail.instagram.com", {}),
    ("mailsender@email.clickdigital.com.br", {}),
    ("contato@comunicacao.serasaexperian.com.br", {}),
    ("activity@notifications.pinterest.com", {}),
    ("no-reply@inter.co", {}),
    ("ola@comunicacao.inter.co", {}),
    ("relacionamento@email.minhaclaro.com.br", {}),
    ("samsunglatam@br.email.samsung.com", {"list_unsub": "<mailto:u@samsung.com>"}),
    ("no-reply@botconversa.com.br", {}),
    ("mfa@kommo.com", {"auto_sub": "auto-generated"}),
    ("boleto@security24h.com.br", {"precedence": "bulk"}),
]

# gente (ou possível gente): TEM que chegar no inbox
_GENTE = [
    "joana.ribeiro@gmail.com",
    "marina@buffetprime.com.br",
    "escontab.assessoria@hotmail.com",
    "cdlinhas@gmail.com",
    "relacionamento@g3pi.com.br",     # institucional, mas domínio próprio: pode ser cliente
    "ola@dinie.com",
    "contato@nerus-edoc.net",
]


@pytest.mark.parametrize("addr,cab", _RUIDO)
def test_robo_nao_vira_conversa(addr, cab):
    assert ei._eh_automatico(_msg(addr, **cab)) is True


@pytest.mark.parametrize("addr", _GENTE)
def test_pessoa_passa(addr):
    assert ei._eh_automatico(_msg(addr)) is False


def test_notificacao_do_proprio_zaq_nao_volta_como_lead(monkeypatch):
    """O 'Proposta aprovada' sai do SMTP de sistema pro e-mail da conta; o IMAP da
    mesma conta puxa de volta. Sem isso, o Zaq abria conversa consigo mesmo."""
    monkeypatch.setenv("SMTP_USER", "contato@zaq-ia.com")
    assert ei._eh_automatico(_msg("contato@zaq-ia.com")) is True
    assert ei._eh_automatico(_msg("CONTATO@ZAQ-IA.COM")) is True
    assert ei._eh_automatico(_msg("cliente@zaq-ia.com")) is False


def test_sem_smtp_user_nao_filtra_todo_mundo(monkeypatch):
    """SMTP_USER ausente não pode virar comparação com string vazia."""
    monkeypatch.delenv("SMTP_USER", raising=False)
    assert ei._eh_automatico(_msg("joana@gmail.com")) is False


def test_auto_submitted_no_e_e_mail_normal(monkeypatch):
    """RFC 3834: 'no' é o valor de e-mail escrito por gente — não filtra."""
    monkeypatch.delenv("SMTP_USER", raising=False)
    assert ei._eh_automatico(_msg("joana@gmail.com", auto_sub="no")) is False
    assert ei._eh_automatico(_msg("joana@gmail.com", auto_sub="auto-replied")) is True


def test_endereco_vazio_ou_torto_nao_quebra(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    assert ei._eh_automatico(_msg("")) is False
    assert ei._eh_automatico(_msg("sem-arroba")) is False
    assert ei._eh_automatico({}) is False
