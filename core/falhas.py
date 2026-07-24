"""Tratamento CENTRAL de falhas de qualquer provedor externo (IA, pagamento,
transcricao, WhatsApp, etc).

Nasceu so' pro saldo da Anthropic; virou a regra geral do sistema. Duas ideias:

1. O CLIENTE nunca ve erro tecnico. Toda falha no caminho critico (o que
   bloqueia a resposta) passa por `tratar_falha_externa`, que devolve uma
   mensagem AMIGAVEL de "instabilidade" — sem nada de "Error 400", "credit
   balance", token, URL, etc.

2. Falha SISTEMICA avisa o ADMIN. `avaliar_falha_provedor` classifica a falha
   e, se for acionavel (SEM CREDITO / CHAVE INVALIDA / QUOTA estourada), avisa
   o admin por e-mail + Telegram — com anti-spam POR (servico, categoria), pra
   qualquer provedor pago (nao so' a IA). Falha so' de rede/indisponibilidade
   (transitoria) NAO avisa, pra nao encher o saco.

Tudo tolerante a falha: nunca levanta excecao — no pior caso devolve a mensagem
amigavel (Tier 1) ou simplesmente nao avisa (Tier 2/3).

Uso:
    # Tier 1 (caminho critico, cliente esperando resposta):
    try:
        resp = agente.responder(...)
    except Exception as e:
        return tratar_falha_externa(e, servico="Anthropic (IA)", canal="whatsapp")

    # Tier 2/3 (best-effort: engole a falha, mas avisa admin se for sistemica):
    except Exception as e:
        avaliar_falha_provedor(e, servico="Asaas")
        return {"ok": False, ...}
"""
from __future__ import annotations

import logging
import time

_log = logging.getLogger("openclaw.falhas")

# Mensagem que o CLIENTE ve quando qualquer provedor esta' fora do ar (saldo,
# chave, quota ou instabilidade). Persona do Zaq: leve, 1a pessoa, sem termo tecnico.
MSG_INSTABILIDADE = (
    "Opa, tive uma instabilidade tecnica passageira aqui e nao consegui "
    "processar agora. 🙏 Ja estou resolvendo — me manda de novo daqui a "
    "pouquinho que eu registro certinho!"
)

# --- Categorias de falha -----------------------------------------------------
CAT_CREDITO = "credito"        # saldo/credito acabou (acao: recarregar)
CAT_AUTH = "auth"              # chave invalida/expirada/sem permissao (acao: girar chave)
CAT_QUOTA = "quota"            # rate limit / quota estourada (acao: subir plano)
CAT_INDISPONIVEL = "indisponivel"  # 5xx / timeout / rede (transitoria: NAO avisa)
CAT_DESCONHECIDA = "desconhecida"

# Categorias que merecem avisar o admin (acionaveis por voce, nao transitorias).
_ACIONAVEIS = frozenset({CAT_CREDITO, CAT_AUTH, CAT_QUOTA})

# Pistas (minusculas) por categoria. String-match e' proposital: robusto a
# versao de SDK e a formato do erro (Exception, dict, texto solto). A ORDEM
# importa: credito antes de quota (ex "insufficient_quota" da OpenAI = SEM
# CREDITO, apesar do "quota" no nome).
_PISTAS_CREDITO = (
    "credit balance is too low", "credit balance", "purchase credits",
    "plans & billing", "insufficient_quota", "insufficient funds",
    "insufficient balance", "payment required", " 402", "billing",
)
_PISTAS_AUTH = (
    "invalid api key", "invalid x-api-key", "incorrect api key",
    "unauthorized", "authentication", "auth recusada", "invalid token",
    "permission denied", "forbidden", " 401", " 403", "http_401", "http_403",
)
_PISTAS_QUOTA = (
    "rate limit", "too many requests", "quota exceeded", " 429", "http_429",
    "overloaded",
)
_PISTAS_INDISPONIVEL = (
    " 500", " 502", " 503", " 504", "http_500", "http_502", "http_503",
    "http_504", "service unavailable", "bad gateway", "gateway timeout",
    "timeout", "timed out", "connection", "econnreset", "temporarily",
)

# Anti-spam do aviso ao admin: no maximo 1 a cada 30 min POR (servico,categoria).
_JANELA_AVISO_S = 30 * 60
_ultimo_aviso: dict[tuple[str, str], float] = {}


def classificar_falha(erro: object) -> str:
    """Classifica a falha numa categoria (credito/auth/quota/indisponivel/
    desconhecida) so' pelo texto do erro."""
    txt = f" {str(erro).lower()} "
    for pistas, cat in (
        (_PISTAS_CREDITO, CAT_CREDITO),
        (_PISTAS_AUTH, CAT_AUTH),
        (_PISTAS_QUOTA, CAT_QUOTA),
        (_PISTAS_INDISPONIVEL, CAT_INDISPONIVEL),
    ):
        if any(p in txt for p in pistas):
            return cat
    return CAT_DESCONHECIDA


def eh_falha_sistemica(erro: object) -> bool:
    """True se a falha e' acionavel pelo admin (credito/auth/quota) — ou seja,
    algo que VOCE precisa resolver (recarregar, girar chave, subir plano), e nao
    uma instabilidade passageira de rede."""
    return classificar_falha(erro) in _ACIONAVEIS


def eh_falha_saldo(erro: object) -> bool:
    """True se a falha e' especificamente de SALDO/credito. (mantida por compat:
    e' o caso que originou este modulo, o do saldo da Anthropic.)"""
    return classificar_falha(erro) == CAT_CREDITO


def mensagem_para_cliente(erro: object = None) -> str:
    """Mensagem AMIGAVEL pro cliente. Toda falha de provedor vira a mesma
    mensagem de instabilidade — o cliente nunca precisa saber o motivo tecnico."""
    return MSG_INSTABILIDADE


def _pode_avisar_agora(servico: str, categoria: str) -> bool:
    chave = (servico, categoria)
    agora = time.monotonic()
    ultimo = _ultimo_aviso.get(chave)
    if ultimo is not None and (agora - ultimo) < _JANELA_AVISO_S:
        return False
    _ultimo_aviso[chave] = agora
    return True


def avaliar_falha_provedor(erro: object, servico: str, canal: str = "") -> str:
    """Avalia uma falha de provedor externo e, se for SISTEMICA (credito/auth/
    quota), avisa o admin (e-mail + Telegram) com anti-spam por (servico,
    categoria). Best-effort: NUNCA levanta excecao. Devolve a categoria.

    Este e' o gancho do Tier 2/3: chame no `except` que ja' engole a falha, pra
    que um provedor PAGO nunca morra em silencio (ex: chave do Asaas expirou).

    `servico`: rotulo do provedor (ex "Asaas", "Twilio (WhatsApp)").
    `canal`: onde ocorreu, se aplicavel (ex "whatsapp", "telegram")."""
    try:
        categoria = classificar_falha(erro)
        if categoria in _ACIONAVEIS and _pode_avisar_agora(servico, categoria):
            try:
                from finance.notificar import avisar_admin_falha_provedor
                avisar_admin_falha_provedor(servico, categoria, str(erro), canal)
            except Exception as e:  # noqa: BLE001 - aviso nunca quebra o fluxo
                _log.warning("falha ao avisar admin (%s/%s): %s", servico, categoria, e)
        else:
            # transitoria ou desconhecida: loga pra investigar, sem spammar.
            _log.warning("falha de provedor %s (%s/%s): %s",
                         servico, canal or "?", categoria, erro)
        return categoria
    except Exception:  # noqa: BLE001 - blindagem total
        _log.exception("erro inesperado em avaliar_falha_provedor")
        return CAT_DESCONHECIDA


def tratar_falha_externa(erro: object, servico: str = "IA", canal: str = "") -> str:
    """Porta do Tier 1 (caminho critico): avalia a falha (avisa admin se
    sistemica) e devolve a mensagem AMIGAVEL pra mostrar pro cliente. Nunca
    levanta excecao."""
    avaliar_falha_provedor(erro, servico, canal)
    return mensagem_para_cliente(erro)


def tratar_falha_ia(erro: object, canal: str = "") -> str:
    """Atalho historico pra falha da IA (Anthropic). Mantido pra nao mexer nos
    call-sites ja' existentes. Equivale a tratar_falha_externa(..., 'Anthropic (IA)')."""
    return tratar_falha_externa(erro, servico="Anthropic (IA)", canal=canal)
