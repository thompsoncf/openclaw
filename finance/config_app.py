"""Configuração global do app (chave→valor) — tabela app_config.

Ponto único de verdade pra flags globais. Hoje usada pelo "modo beta grátis":
quando ligado, o cadastro mostra "Grátis (beta)" e ninguém é cobrado; os preços
reais ficam guardados na tabela planos, prontos pra valer quando você desligar.

Todas as funções recebem `pool` (desacoplado, testável). Leituras têm fallback:
se a tabela ainda não existe (migração não rodou), degradam sem quebrar.
"""
from __future__ import annotations

import logging

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
