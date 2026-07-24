"""Tratamento de falhas do provedor de IA (Anthropic).

Duas responsabilidades, uma so' porta de entrada (`tratar_falha_ia`):

1. TRADUZIR a excecao crua da IA numa mensagem AMIGAVEL pro cliente. O usuario
   final NUNCA pode ver texto tecnico do tipo "Error code: 400 ... credit balance
   is too low ...". Isso e' constrangedor e ainda expoe detalhe interno de
   billing. Em vez disso ele ve "estou com uma instabilidade, tenta de novo".

2. AVISAR o admin (dono do SaaS) por e-mail + Telegram quando a falha e' de
   SALDO/credito da Anthropic (a conta secou). Com anti-spam: no maximo 1 aviso
   a cada `_JANELA_AVISO_S` por processo, senao cada mensagem de cliente durante
   o apagao viraria um e-mail.

Tudo tolerante a falha: se o aviso ao admin quebrar, engole e loga — o mais
importante e' devolver a mensagem amigavel pro cliente sem levantar excecao.
"""
from __future__ import annotations

import logging
import time

_log = logging.getLogger("openclaw.falhas")

# Mensagem que o CLIENTE ve quando a IA esta' fora do ar (saldo ou qualquer
# outra instabilidade). Persona do Zaq: leve, primeira pessoa, sem termo tecnico.
MSG_INSTABILIDADE = (
    "Opa, tive uma instabilidade tecnica passageira aqui e nao consegui "
    "processar agora. 🙏 Ja estou resolvendo — me manda de novo daqui a "
    "pouquinho que eu registro certinho!"
)

# Pistas (minusculas) que identificam a falha de SALDO/credito da Anthropic.
# String-match e' proposital: e' robusto a versao da SDK (a mesma condicao pode
# vir como BadRequestError, APIStatusError, dict, etc).
_PISTAS_SALDO = (
    "credit balance is too low",
    "credit balance",
    "purchase credits",
    "plans & billing",
    "billing",
    "insufficient",
)

# Anti-spam do aviso ao admin: no maximo 1 a cada 30 min (por processo).
_JANELA_AVISO_S = 30 * 60
_ultimo_aviso_ts: float | None = None


def eh_falha_saldo(erro: object) -> bool:
    """True se a excecao indica que o SALDO/credito da Anthropic acabou.

    Casa por texto (case-insensitive) pra funcionar independente da classe de
    erro que a SDK levantou."""
    txt = str(erro).lower()
    return any(p in txt for p in _PISTAS_SALDO)


def mensagem_para_cliente(erro: object) -> str:
    """Mensagem AMIGAVEL pro cliente. Hoje toda falha de IA vira a mesma
    mensagem de instabilidade — o cliente nunca precisa saber o motivo tecnico.
    Fica como funcao separada caso um dia a gente queira diferenciar."""
    return MSG_INSTABILIDADE


def _pode_avisar_agora() -> bool:
    global _ultimo_aviso_ts
    agora = time.monotonic()
    if _ultimo_aviso_ts is not None and (agora - _ultimo_aviso_ts) < _JANELA_AVISO_S:
        return False
    _ultimo_aviso_ts = agora
    return True


def tratar_falha_ia(erro: object, canal: str = "") -> str:
    """Porta unica de tratamento de falha da IA.

    - Se for falha de SALDO: avisa o admin (e-mail + Telegram), com anti-spam.
    - Sempre: devolve a mensagem amigavel pra mostrar pro cliente.

    `canal` e' so' rotulo pro aviso do admin (ex "whatsapp", "telegram").
    Nunca levanta excecao — no pior caso so' devolve a mensagem amigavel."""
    try:
        if eh_falha_saldo(erro) and _pode_avisar_agora():
            try:
                from finance.notificar import avisar_admin_saldo_ia
                avisar_admin_saldo_ia(str(erro), canal=canal)
            except Exception as e:  # noqa: BLE001 - aviso nunca quebra o fluxo
                _log.warning("falha ao avisar admin sobre saldo: %s", e)
        else:
            # Falha generica (nao-saldo): loga pra investigar, mas nao spamma.
            _log.warning("falha de IA (%s): %s", canal or "?", erro)
    except Exception:  # noqa: BLE001 - blindagem total
        _log.exception("erro inesperado em tratar_falha_ia")
    return mensagem_para_cliente(erro)
