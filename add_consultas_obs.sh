#!/usr/bin/env bash
# add_consultas_obs.sh — Fase 1: consultas admin da observabilidade (só leitura).
# ANEXA resumo_uso, dificuldades, interacoes_recentes em finance/observabilidade.py.
# RODE NA RAIZ DO REPO: bash add_consultas_obs.sh
set -euo pipefail
echo "==> 1/4 Raiz..."; [ -f finance/observabilidade.py ] || { echo "ERRO: rode na raiz (finance/observabilidade.py nao achado)."; exit 1; }
echo "==> 2/4 Ja aplicado?"; if grep -q "def dificuldades" finance/observabilidade.py; then echo "JA EXISTE. Nada a fazer."; exit 0; fi
cp finance/observabilidade.py finance/observabilidade.py.bak
echo "==> 3/4 Anexando..."
cat >> finance/observabilidade.py << 'PYEOF'


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
PYEOF
echo "==> 4/4 Validar + commit..."
PYBIN=python3; command -v python3 >/dev/null 2>&1 || PYBIN=python
"$PYBIN" -m py_compile finance/observabilidade.py 2>/tmp/_c_err || { echo "ERRO compila:"; cat /tmp/_c_err; mv finance/observabilidade.py.bak finance/observabilidade.py; exit 1; }
grep -q "def dificuldades" finance/observabilidade.py || { echo "ERRO: nao anexou."; mv finance/observabilidade.py.bak finance/observabilidade.py; exit 1; }
echo "    OK compila."
git add finance/observabilidade.py
git diff --cached --quiet || { git commit -m "feat: consultas admin de observabilidade (resumo_uso, dificuldades, interacoes_recentes)"; git push; }
rm -f finance/observabilidade.py.bak
echo "PRONTO (consultas)."
