"""Configuração global do app (chave→valor) — tabela app_config.

Ponto único de verdade pra flags globais. Hoje usada pelo "modo beta grátis":
quando ligado, o cadastro mostra "Grátis (beta)" e ninguém é cobrado; os preços
reais ficam guardados na tabela planos, prontos pra valer quando você desligar.

Todas as funções recebem `pool` (desacoplado, testável). Leituras têm fallback:
se a tabela ainda não existe (migração não rodou), degradam sem quebrar.
"""
from __future__ import annotations

import logging
import os

_log = logging.getLogger("openclaw.config")


def get_config(pool, chave: str, padrao: str | None = None) -> str | None:
    """Lê uma configuração. Retorna `padrao` se não existir (ou se a tabela
    ainda não foi criada)."""
    try:
        with pool.connection() as c:
            r = c.execute(
                "select valor from app_config where chave=%s", (chave,)
            ).fetchone()
        return r[0] if r else padrao
    except Exception:  # tabela ausente / erro de leitura: degrada
        return padrao


def set_config(pool, chave: str, valor: str) -> None:
    """Grava (cria ou atualiza) uma configuração."""
    with pool.connection() as c:
        c.execute(
            """insert into app_config (chave, valor, atualizado_em)
               values (%s, %s, now())
               on conflict (chave)
               do update set valor=excluded.valor, atualizado_em=now()""",
            (chave, valor),
        )
        c.commit()


# ── Modo beta grátis ──────────────────────────────────────────────────────
BETA_GRATIS = "beta_gratis"


def beta_gratis_ativo(pool) -> bool:
    """True se o modo beta grátis está ligado.

    PADRÃO SEGURO: se a configuração não existir por qualquer motivo, assume
    LIGADO ('on') — melhor mostrar grátis do que cobrar sem querer na validação.
    """
    return (get_config(pool, BETA_GRATIS, "on") or "on").strip().lower() == "on"


def set_beta_gratis(pool, ligado: bool) -> None:
    """Liga/desliga o modo beta grátis."""
    set_config(pool, BETA_GRATIS, "on" if ligado else "off")


# ── Aviso de vencimento no painel do cliente ──────────────────────────────
#
# POR QUE EXISTE. Durante o beta ninguem e' cobrado, mas o vencimento do plano
# continua correndo no banco — entao contas "vencidas" que estao usando o
# sistema de graca, e com a nossa benca, ganhavam uma faixa vermelha pedindo
# pagamento em toda tela. Ruido puro enquanto a cobranca nao comecou.
#
# Esta chave CALA a faixa. Ela nao libera nem corta acesso de ninguem: quem
# decide isso e' contas.acesso_liberado, que nao olha pra ca. Aqui so' se
# escolhe o que aparece na tela.
#
# TRAVA: so' vale enquanto o beta gratis estiver LIGADO (ver web/portal.py,
# _plano_aviso). No dia em que o beta for desligado pra comecar a cobrar, os
# avisos voltam sozinhos, mesmo que esta chave tenha ficado calada — cobrar sem
# avisar quem esta vencendo nao e' opcao, e depender de lembrar de duas chaves
# na ordem certa e' como se esquece uma.
AVISO_VENCIMENTO = "aviso_vencimento"


def aviso_vencimento_ativo(pool) -> bool:
    """True se a faixa de vencimento deve aparecer pro cliente.

    PADRAO SEGURO, e o INVERSO do beta: sem configuracao, AVISA. O beta assume
    'gratis' porque cobrar sem querer e' pior; aqui e' ao contrario — deixar de
    avisar sem querer e' o que cobra caro.
    """
    return (get_config(pool, AVISO_VENCIMENTO, "on") or "on").strip().lower() == "on"


def set_aviso_vencimento(pool, ligado: bool) -> None:
    """Liga/desliga a faixa de vencimento no painel do cliente."""
    set_config(pool, AVISO_VENCIMENTO, "on" if ligado else "off")


# ── Alertas do admin (pra ONDE vao os avisos do sistema) ──────────────────
# Antes isso vivia SO' em variavel de ambiente (ADMIN_EMAIL / ADMIN_TELEGRAM_ID)
# — e a env so' estava setada no cron de saldos, entao os alertas disparados
# pelo web (core.falhas: chave do Asaas expirou, Twilio sem saldo...) caiam no
# fallback silencioso do remetente SMTP. Agora o valor mora no banco e da' pra
# conferir/trocar em /admin/comunicacao, sem redeploy.
#
# Precedencia (o que ganha): banco (setado no /admin) → env → fallback.
ADMIN_EMAIL = "admin_email"
ADMIN_TELEGRAM_ID = "admin_telegram_id"


def admin_email(pool) -> str | None:
    """E-mail do admin do SaaS: /admin (banco) → env ADMIN_EMAIL → None."""
    v = (get_config(pool, ADMIN_EMAIL) or "").strip()
    return v or (os.environ.get("ADMIN_EMAIL") or "").strip() or None


def set_admin_email(pool, email: str) -> None:
    """Grava o e-mail do admin. String vazia = volta a valer o env."""
    set_config(pool, ADMIN_EMAIL, (email or "").strip())


def admin_telegram_id(pool) -> str | None:
    """Chat do Telegram do admin: /admin (banco) → env ADMIN_TELEGRAM_ID."""
    v = (get_config(pool, ADMIN_TELEGRAM_ID) or "").strip()
    return v or (os.environ.get("ADMIN_TELEGRAM_ID") or "").strip() or None


def set_admin_telegram_id(pool, chat_id: str) -> None:
    """Grava o chat do admin no Telegram. Vazio = volta a valer o env."""
    set_config(pool, ADMIN_TELEGRAM_ID, (chat_id or "").strip())
