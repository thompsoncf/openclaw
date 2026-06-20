#!/usr/bin/env bash
# ============================================================================
# add_metricas_banco.sh
# ANEXA duas funcoes (so' leitura) ao fim de finance/estatisticas.py:
#   - pronto_para_fase_b(pool)       -> gatilho da Fase B + barra de progresso
#   - estatisticas_leituras_qr(pool) -> assertividade do QR por tipo (Foto/PDF)
# NAO reescreve nada existente (so' append). RODE NA RAIZ DO REPO:
#   bash add_metricas_banco.sh
# Aborta se as funcoes ja existirem (evita anexar 2x) ou se nao compilar.
# ============================================================================
set -euo pipefail

echo "==> 1/4  Conferindo raiz do repo..."
[ -d finance ] || { echo "    ERRO: rode na RAIZ do repo (finance/)." >&2; exit 1; }

echo "==> 2/4  Checando se ja' foi aplicado..."
if grep -q "def pronto_para_fase_b" finance/estatisticas.py; then
  echo "    JA EXISTE 'pronto_para_fase_b' no arquivo. Nada a fazer (evita duplicar)." >&2
  exit 0
fi
cp finance/estatisticas.py finance/estatisticas.py.bak && echo "    backup: finance/estatisticas.py.bak"

echo "==> 3/4  Anexando as funcoes..."
cat >> finance/estatisticas.py << 'PYEOF'


# ===========================================================================
# GATILHO DA FASE B + ASSERTIVIDADE DO QR (so' leitura; visao admin)
# Adicionadas pra acompanhar o enchimento do banco de ouro e a qualidade da
# leitura de QR por tipo de midia. Calibravel via dict 'gatilhos'.
# ===========================================================================

# Numeros calibrados pra realidade de Teresina (ajustaveis):
GATILHOS_FASE_B = {
    "observacoes": 1200,       # observacoes cruas em precos_observados
    "produtos": 400,           # produtos distintos (descricao_norm)
    "lojas": 6,                # lojas distintas (comparacao precisa de mercados diferentes)
    "pct_confirmado": 0.35,    # % de produtos com >=2 leituras (preco confirmado, nao unico)
    "cobertura_gtin": 0.40,    # % de observacoes com GTIN (ponto forte do fluxo)
}


def _metrica(nome: str, valor, gatilho, unidade: str = "") -> dict:
    """Monta uma linha de metrica pronta pra barra de progresso no painel."""
    try:
        pct = int(round(100 * float(valor) / float(gatilho))) if gatilho else 100
    except (TypeError, ValueError, ZeroDivisionError):
        pct = 0
    return {
        "nome": nome, "valor": valor, "gatilho": gatilho, "unidade": unidade,
        "pct": max(0, min(pct, 100)), "ok": (valor or 0) >= gatilho,
    }


def pronto_para_fase_b(pool, gatilhos: dict | None = None) -> dict:
    """Diz se o banco de precos ja' encheu o suficiente pra liberar a Fase B
    (raio-x opt-in + Haiku no cupom simples). So' leitura.

    Retorna:
      - veredito: 'LIBERADA' | 'BLOQUEADA'
      - metricas: lista pronta pra barra de progresso (nome/valor/gatilho/pct/ok)
      - faltam: nomes das metricas que ainda nao bateram
      - contexto: cupons com QR, obs/cupom, produtos confirmados, produtos
        multi-loja (o "ouro" de verdade), estimativa de cupons faltando, data
        da observacao mais recente.
    """
    G = gatilhos or GATILHOS_FASE_B
    with pool.connection() as conn:
        tot_obs = conn.execute("select count(*) from precos_observados").fetchone()[0] or 0
        tot_prod = conn.execute(
            "select count(distinct descricao_norm) from precos_observados").fetchone()[0] or 0
        tot_lojas = conn.execute(
            "select count(distinct loja_id) from precos_observados where loja_id is not null"
        ).fetchone()[0] or 0
        com_gtin = conn.execute(
            "select count(*) from precos_observados where gtin is not null").fetchone()[0] or 0
        prod_confirmados = conn.execute(
            "select count(*) from (select 1 from precos_observados "
            "group by descricao_norm having count(*) >= 2) t").fetchone()[0] or 0
        prod_multiloja = conn.execute(
            "select count(*) from (select 1 from precos_observados where loja_id is not null "
            "group by descricao_norm having count(distinct loja_id) >= 2) t").fetchone()[0] or 0
        try:
            cupons = conn.execute(
                "select count(distinct chave) from qr_leituras where chave is not null"
            ).fetchone()[0] or 0
        except Exception:  # noqa: BLE001
            cupons = 0
        try:
            data_recente = conn.execute(
                "select max(data_compra) from precos_observados").fetchone()[0]
        except Exception:  # noqa: BLE001
            data_recente = None

    pct_conf = (prod_confirmados / tot_prod) if tot_prod else 0.0
    pct_gtin = (com_gtin / tot_obs) if tot_obs else 0.0
    obs_por_cupom = (tot_obs / cupons) if cupons else 0.0

    metricas = [
        _metrica("Observacoes cruas", tot_obs, G["observacoes"]),
        _metrica("Produtos distintos", tot_prod, G["produtos"]),
        _metrica("Lojas distintas", tot_lojas, G["lojas"]),
        _metrica("% confirmado (>=2 leituras)", round(pct_conf * 100),
                 round(G["pct_confirmado"] * 100), unidade="%"),
        _metrica("Cobertura GTIN", round(pct_gtin * 100),
                 round(G["cobertura_gtin"] * 100), unidade="%"),
    ]
    liberada = all(m["ok"] for m in metricas)
    faltam = [m["nome"] for m in metricas if not m["ok"]]

    cupons_faltando = None
    if obs_por_cupom > 0 and tot_obs < G["observacoes"]:
        cupons_faltando = int(round((G["observacoes"] - tot_obs) / obs_por_cupom))

    return {
        "veredito": "LIBERADA" if liberada else "BLOQUEADA",
        "liberada": liberada,
        "faltam": faltam,
        "metricas": metricas,
        "contexto": {
            "cupons_com_qr": cupons,
            "obs_por_cupom": round(obs_por_cupom, 1),
            "produtos_confirmados": prod_confirmados,
            "produtos_multiloja": prod_multiloja,
            "cupons_faltando_estimado": cupons_faltando,
            "data_mais_recente": (data_recente.isoformat()
                                  if hasattr(data_recente, "isoformat") else data_recente),
        },
    }


def estatisticas_leituras_qr(pool) -> dict:
    """Assertividade da leitura de QR por TIPO de midia (Foto vs PDF). So' leitura.

    Pra cada tipo: total de leituras, quantas acertaram o QR (leu=true), a taxa
    de acerto, e o tamanho medio (KB) das que ACERTARAM vs FALHARAM â pra ver se
    foto pequena/borrada e' o que derruba a leitura. Ajuda a decidir ajustes no
    decoder e a calibrar o roteamento (sem chave -> Haiku).
    """
    with pool.connection() as conn:
        linhas = conn.execute(
            """select
                 case when media_type ilike 'application/pdf' then 'PDF'
                      when media_type ilike 'image/%' then 'Foto'
                      else coalesce(media_type, '?') end as tipo,
                 count(*) as total,
                 count(*) filter (where leu) as acertos,
                 avg(img_bytes) filter (where leu) as bytes_ok,
                 avg(img_bytes) filter (where not leu) as bytes_falha
               from qr_leituras
               group by 1
               order by total desc"""
        ).fetchall()

    tipos = []
    tot = ok = 0
    for tipo, total, acertos, bytes_ok, bytes_falha in linhas:
        total = total or 0
        acertos = acertos or 0
        tot += total
        ok += acertos
        tipos.append({
            "tipo": tipo,
            "total": total,
            "acertos": acertos,
            "falhas": total - acertos,
            "acertividade_pct": int(round(100 * acertos / total)) if total else 0,
            "kb_medio_acerto": int((bytes_ok or 0) / 1024),
            "kb_medio_falha": int((bytes_falha or 0) / 1024),
        })
    return {
        "tipos": tipos,
        "total_leituras": tot,
        "acertividade_geral_pct": int(round(100 * ok / tot)) if tot else 0,
    }
PYEOF

echo "==> 4/4  Validando + commit..."
PYBIN=python3; command -v python3 >/dev/null 2>&1 || PYBIN=python
if ! "$PYBIN" -m py_compile finance/estatisticas.py 2>/tmp/_est_err; then
  echo "    ERRO: nao compila. Restaurando backup." >&2; cat /tmp/_est_err >&2
  mv finance/estatisticas.py.bak finance/estatisticas.py
  exit 1
fi
grep -q "def pronto_para_fase_b" finance/estatisticas.py && grep -q "def estatisticas_leituras_qr" finance/estatisticas.py || {
  echo "    ERRO: funcoes nao ficaram no arquivo. Restaurando." >&2
  mv finance/estatisticas.py.bak finance/estatisticas.py; exit 1; }
echo "    OK: compila e as 2 funcoes estao presentes."

git add finance/estatisticas.py
if git diff --cached --quiet; then
  echo "    Nada a commitar."
else
  git commit -m "feat: metricas do banco (gatilho Fase B + assertividade QR por tipo)"
  git push
  echo "    Commit + push OK."
fi
rm -f finance/estatisticas.py.bak
echo ""
echo "PRONTO. Funcoes disponiveis: pronto_para_fase_b(pool) e estatisticas_leituras_qr(pool)."
