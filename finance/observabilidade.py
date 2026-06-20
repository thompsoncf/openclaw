"""finance/observabilidade.py — Modulo de Comunicacao (Fase 1): log de interacoes.

Grava 1 linha por turno em conversas_log, pra entender onde os clientes travam.
Best-effort: NUNCA derruba a resposta ao cliente (igual ao padrao do uso_api).

LGPD:
- Texto do cliente/resposta SO' e' gravado se a env LOG_TEXTO_CONVERSA=1.
  Sem ela, grava metadata (tipo, tools, sucesso, repetiu, custo) e texto NULO.
- Retencao 30 dias via expurgar_antigos() (rodar por cron 1x/dia).
- Leitura e' so' admin (a tela/consulta restringe; restrito NAO le).
"""
from __future__ import annotations

import logging
import os

_log = logging.getLogger("openclaw.observabilidade")


def _quer_texto() -> bool:
    return (os.environ.get("LOG_TEXTO_CONVERSA", "") or "").strip().lower() in (
        "1", "true", "sim", "yes")


def registrar_interacao(pool, conta_id, membro_id=None, canal=None,
                        tipo_midia=None, texto_usuario=None, resposta=None,
                        tools_usadas=None, modelo=None, sucesso=None,
                        repetiu=None, custo_centavos=None) -> bool:
    """Grava um turno em conversas_log. Best-effort (engole erro, devolve False).
    Respeita LOG_TEXTO_CONVERSA: sem ela, texto_usuario/resposta vao NULOS."""
    try:
        if not _quer_texto():
            texto_usuario = None
            resposta = None
        with pool.connection() as c:
            c.execute(
                """insert into conversas_log
                   (conta_id, membro_id, canal, tipo_midia, texto_usuario,
                    resposta, tools_usadas, modelo, sucesso, repetiu, custo_centavos)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (conta_id, membro_id, canal, tipo_midia, texto_usuario,
                 resposta, tools_usadas, modelo, sucesso, repetiu, custo_centavos),
            )
            c.commit()
        return True
    except Exception as e:  # noqa: BLE001
        _log.info("registrar_interacao falhou (ignorado): %s", e)
        return False


def expurgar_antigos(pool, dias: int = 30) -> int:
    """Faxina de retencao: apaga conversas com mais de `dias`. Delete ESCOPADO
    (where criado_em < now - dias) — nunca a tabela toda. Pra rodar via cron.
    Retorna quantas linhas apagou."""
    try:
        with pool.connection() as c:
            cur = c.execute(
                "delete from conversas_log "
                "where criado_em < now() - (%s || ' days')::interval",
                (str(int(dias)),),
            )
            n = getattr(cur, "rowcount", 0) or 0
            c.commit()
        _log.info("expurgar_antigos: %s linhas removidas (> %s dias)", n, dias)
        return n
    except Exception as e:  # noqa: BLE001
        _log.warning("expurgar_antigos falhou: %s", e)
        return 0


def resumo_uso(pool, dias: int = 7) -> dict:
    """Visao geral das interacoes nos ultimos `dias`: total, por canal, por tipo,
    taxa de sucesso e custo total. So' leitura (admin)."""
    with pool.connection() as c:
        total, ok, custo = c.execute(
            "select count(*), count(*) filter (where sucesso), "
            "coalesce(sum(custo_centavos),0) from conversas_log "
            "where criado_em > now() - (%s || ' days')::interval",
            (str(int(dias)),)).fetchone()
        por_canal = c.execute(
            "select coalesce(canal,'?'), count(*) from conversas_log "
            "where criado_em > now() - (%s || ' days')::interval "
            "group by 1 order by 2 desc", (str(int(dias)),)).fetchall()
        por_tipo = c.execute(
            "select coalesce(tipo_midia,'?'), count(*) from conversas_log "
            "where criado_em > now() - (%s || ' days')::interval "
            "group by 1 order by 2 desc", (str(int(dias)),)).fetchall()
    total = total or 0
    return {
        "dias": dias, "total": total, "sucesso": ok or 0,
        "taxa_sucesso_pct": int(round(100 * (ok or 0) / total)) if total else 0,
        "custo_reais": round((custo or 0) / 100, 2),
        "por_canal": [{"canal": a, "qtd": b} for a, b in por_canal],
        "por_tipo": [{"tipo": a, "qtd": b} for a, b in por_tipo],
    }


def dificuldades(pool, limite: int = 50) -> list:
    """Onde os clientes travaram: interacoes com sucesso=false OU repetiu=true.
    Mapa de atrito pra melhorar o agente. So' leitura (admin)."""
    cols = ["id", "canal", "tipo_midia", "texto_usuario", "resposta",
            "tools_usadas", "sucesso", "repetiu", "criado_em"]
    with pool.connection() as c:
        rows = c.execute(
            "select id, canal, tipo_midia, texto_usuario, resposta, tools_usadas, "
            "sucesso, repetiu, criado_em from conversas_log "
            "where sucesso is false or repetiu is true "
            "order by id desc limit %s", (int(limite),)).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def interacoes_recentes(pool, conta_id=None, limite: int = 50) -> list:
    """Ultimas interacoes (todas, ou de uma conta). Pra ler na mao. So' leitura (admin)."""
    cols = ["id", "conta_id", "canal", "tipo_midia", "texto_usuario", "resposta",
            "tools_usadas", "modelo", "sucesso", "repetiu", "custo_centavos", "criado_em"]
    base = ("select id, conta_id, canal, tipo_midia, texto_usuario, resposta, "
            "tools_usadas, modelo, sucesso, repetiu, custo_centavos, criado_em "
            "from conversas_log ")
    with pool.connection() as c:
        if conta_id is not None:
            rows = c.execute(base + "where conta_id=%s order by id desc limit %s",
                             (int(conta_id), int(limite))).fetchall()
        else:
            rows = c.execute(base + "order by id desc limit %s", (int(limite),)).fetchall()
    return [dict(zip(cols, r)) for r in rows]
