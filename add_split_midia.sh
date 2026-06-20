#!/usr/bin/env bash
# add_split_midia.sh — Pergunta 1: "a maioria das imagens e' cupom ou comprovante?"
# ANEXA split_midia() em finance/observabilidade.py. SO' leitura do conversas_log.
# Cupom fiscal = a interacao com imagem usou tool de cupom (checar_duplicata ou
# registrar_itens_cupom). O resto o agente tratou como comprovante/outro.
# Nao cruza tabela, nao toca no agente. RODE NA RAIZ: bash add_split_midia.sh
set -euo pipefail
echo "==> 1/4 Raiz..."; [ -f finance/observabilidade.py ] || { echo "ERRO: rode na raiz."; exit 1; }
echo "==> 2/4 Ja aplicado?"; if grep -q "def split_midia" finance/observabilidade.py; then echo "JA EXISTE. Nada a fazer."; exit 0; fi
cp finance/observabilidade.py finance/observabilidade.py.bak
echo "==> 3/4 Anexando split_midia..."
cat >> finance/observabilidade.py << 'PYEOF'


# tools que SO' aparecem em cupom fiscal (o agente as chama ao processar cupom);
# comprovante de PIX/banco vira so' um lancar_despesa simples, sem essas.
_TOOLS_CUPOM = r"(checar_duplicata|registrar_itens_cupom)"


def split_midia(pool, dias: int = 30) -> dict:
    """Das interacoes com IMAGEM (foto/pdf) nos ultimos `dias`, quantas foram
    CUPOM FISCAL (usaram tool de cupom) vs COMPROVANTE/outro. So' leitura.

    Responde 'o que mais chega: cupom ou comprovante?' sem cruzar tabelas e sem
    chute por tipo de arquivo — o sinal e' a ACAO do agente (ele agiu como cupom
    => era cupom)."""
    with pool.connection() as c:
        total, cupom = c.execute(
            "select count(*), count(*) filter (where tools_usadas ~ %s) "
            "from conversas_log "
            "where tipo_midia in ('foto','pdf') "
            "and criado_em > now() - (%s || ' days')::interval",
            (_TOOLS_CUPOM, str(int(dias)))).fetchone()
        por_tipo = c.execute(
            "select tipo_midia, count(*), count(*) filter (where tools_usadas ~ %s) "
            "from conversas_log "
            "where tipo_midia in ('foto','pdf') "
            "and criado_em > now() - (%s || ' days')::interval "
            "group by 1 order by 2 desc",
            (_TOOLS_CUPOM, str(int(dias)))).fetchall()
    total = total or 0
    cupom = cupom or 0
    outro = total - cupom
    return {
        "dias": dias,
        "total_imagens": total,
        "cupom_fiscal": cupom,
        "comprovante_ou_outro": outro,
        "pct_cupom": int(round(100 * cupom / total)) if total else 0,
        "pct_outro": int(round(100 * outro / total)) if total else 0,
        "por_tipo": [
            {"tipo": t, "total": (tot or 0), "cupom": (cup or 0),
             "outro": (tot or 0) - (cup or 0)}
            for t, tot, cup in por_tipo
        ],
    }
PYEOF
echo "==> 4/4 Validar + commit..."
PYBIN=python3; command -v python3 >/dev/null 2>&1 || PYBIN=python
"$PYBIN" -m py_compile finance/observabilidade.py 2>/tmp/_s_err || { echo "ERRO compila:"; cat /tmp/_s_err; mv finance/observabilidade.py.bak finance/observabilidade.py; exit 1; }
grep -q "def split_midia" finance/observabilidade.py || { echo "ERRO: nao anexou."; mv finance/observabilidade.py.bak finance/observabilidade.py; exit 1; }
echo "    OK compila."
git add finance/observabilidade.py
git diff --cached --quiet || { git commit -m "feat: split_midia - cupom x comprovante por tool usada (Pergunta 1)"; git push; }
rm -f finance/observabilidade.py.bak
echo "PRONTO (split_midia)."
