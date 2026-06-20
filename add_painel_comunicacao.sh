#!/usr/bin/env bash
# add_painel_comunicacao.sh — adiciona a pagina /admin/comunicacao em web/admin.py
# Mostra: barras Fase B + assertividade QR (Foto/PDF) + resumo de uso + atrito.
# Auto-contida (HTML inline) -> nao mexe no DictLoader. + link no menu.
# RODE NA RAIZ DO REPO: bash add_painel_comunicacao.sh
set -euo pipefail
echo "==> 1/4 Raiz..."; [ -f web/admin.py ] || { echo "ERRO: rode na raiz (web/admin.py)."; exit 1; }
echo "==> 2/4 Ja aplicado?"; if grep -q "/admin/comunicacao" web/admin.py; then echo "JA EXISTE. Nada a fazer."; exit 0; fi
cp web/admin.py web/admin.py.bak
echo "==> 3/4 Editando (link no menu + rota nova)..."
PYBIN=python3; command -v python3 >/dev/null 2>&1 || PYBIN=python
"$PYBIN" - << 'PYEOF'
import sys
p = "web/admin.py"
src = open(p, encoding="utf-8").read()

# 1) link no menu, logo depois de "Banco"
old_nav = '<a href="/admin/banco">Banco</a>'
if src.count(old_nav) != 1:
    print(f"ERRO: ancora do menu apareceu {src.count(old_nav)}x (esperava 1).", file=sys.stderr); sys.exit(2)
src = src.replace(old_nav, old_nav + '<a href="/admin/comunicacao">Comunicacao</a>', 1)

# 2) rota nova, anexada ao fim do arquivo
rota = '''

@router.get("/admin/comunicacao", response_class=HTMLResponse)
def admin_comunicacao(request: Request):
    # Painel do Modulo de Comunicacao: Fase B + assertividade QR + uso + atrito.
    if _admin(request) is None:
        return _NEGADO
    pool = get_pool()
    from finance.estatisticas import pronto_para_fase_b, estatisticas_leituras_qr
    from finance.observabilidade import resumo_uso, dificuldades

    faseb = pronto_para_fase_b(pool)
    qr = estatisticas_leituras_qr(pool)
    uso = resumo_uso(pool, 7)
    atrito = dificuldades(pool, 30)

    def _esc(t):
        return (str(t) if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _barra(pct):
        cheio = max(0, min(10, round((pct or 0) / 10)))
        return "\\u2588" * cheio + "\\u2591" * (10 - cheio)

    fb_rows = "".join(
        "<tr><td>%s</td><td>%s%s/%s%s</td><td><code>%s</code> %s%%</td><td>%s</td></tr>" % (
            _esc(m["nome"]), m["valor"], m["unidade"], m["gatilho"], m["unidade"],
            _barra(m["pct"]), m["pct"], "OK" if m["ok"] else "...")
        for m in faseb["metricas"])
    ctx = faseb["contexto"]

    qr_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s%%</td><td>%s</td><td>%s</td></tr>" % (
            _esc(t["tipo"]), t["total"], t["acertos"], t["falhas"],
            t["acertividade_pct"], t["kb_medio_acerto"], t["kb_medio_falha"])
        for t in qr["tipos"]) or "<tr><td colspan=7>sem leituras ainda</td></tr>"

    canal_rows = ", ".join("%s: %s" % (_esc(x["canal"]), x["qtd"]) for x in uso["por_canal"]) or "-"
    tipo_rows = ", ".join("%s: %s" % (_esc(x["tipo"]), x["qtd"]) for x in uso["por_tipo"]) or "-"

    atrito_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            a["id"], _esc(a["canal"]), _esc(a["tipo_midia"]),
            _esc((a["texto_usuario"] or "")[:60]),
            "nao" if a["sucesso"] is False else "sim",
            "sim" if a["repetiu"] else "-")
        for a in atrito) or "<tr><td colspan=6>nenhum atrito recente \\U0001f389</td></tr>"

    html = """<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Comunicacao - Zaq admin</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f1115;color:#e6e6e6;margin:0;padding:1rem;}
a{color:#6cf;} h2{margin:1.4rem 0 .5rem;} .nav{margin-bottom:1rem;font-size:.9rem;}
table{border-collapse:collapse;width:100%%;margin:.3rem 0 1rem;font-size:.9rem;}
th,td{border:1px solid #2a2f3a;padding:.35rem .5rem;text-align:left;}
th{background:#1a1f29;} code{color:#7CFC9B;} .v{font-weight:bold;}
.card{background:#161a22;border:1px solid #2a2f3a;border-radius:8px;padding:.8rem 1rem;margin:.5rem 0;}
</style></head><body>
<div class=nav><a href="/admin">&larr; admin</a> &nbsp;|&nbsp; <b>Comunicacao</b></div>
<h1>Modulo de Comunicacao</h1>

<h2>Fase B - %s</h2>
<table><tr><th>Metrica</th><th>Atual/Meta</th><th>Progresso</th><th>OK</th></tr>%s</table>
<div class=card>
Produtos multi-loja (ouro): <span class=v>%s</span> &nbsp;|&nbsp;
Confirmados (>=2): <span class=v>%s</span> &nbsp;|&nbsp;
Cupons com QR: <span class=v>%s</span> &nbsp;|&nbsp;
Cupons faltando (est.): <span class=v>%s</span>
</div>

<h2>Assertividade do QR (Foto x PDF)</h2>
<table><tr><th>Tipo</th><th>Total</th><th>Acertos</th><th>Falhas</th><th>Acerto</th><th>KB ok</th><th>KB falha</th></tr>%s</table>
<div class=card>Geral: <span class=v>%s%%</span> de acerto em %s leituras</div>

<h2>Uso (ultimos %s dias)</h2>
<div class=card>
Interacoes: <span class=v>%s</span> &nbsp;|&nbsp;
Sucesso: <span class=v>%s%%</span> &nbsp;|&nbsp;
Custo: <span class=v>R$ %s</span><br>
Por canal: %s<br>Por tipo: %s
</div>

<h2>Atrito (falhas e repeticoes)</h2>
<table><tr><th>id</th><th>canal</th><th>tipo</th><th>texto</th><th>sucesso</th><th>repetiu</th></tr>%s</table>
</body></html>""" % (
        faseb["veredito"], fb_rows,
        ctx["produtos_multiloja"], ctx["produtos_confirmados"],
        ctx["cupons_com_qr"], ctx.get("cupons_faltando_estimado"),
        qr_rows, qr["acertividade_geral_pct"], qr["total_leituras"],
        uso["dias"], uso["total"], uso["taxa_sucesso_pct"], uso["custo_reais"],
        canal_rows, tipo_rows, atrito_rows,
    )
    return HTMLResponse(html)
'''
src = src.rstrip() + "\n" + rota
open(p, "w", encoding="utf-8").write(src)
print("    link + rota /admin/comunicacao aplicados.")
PYEOF
echo "==> 4/4 Validar + commit..."
"$PYBIN" -m py_compile web/admin.py 2>/tmp/_a_err || { echo "ERRO compila:"; cat /tmp/_a_err; mv web/admin.py.bak web/admin.py; exit 1; }
grep -q "/admin/comunicacao" web/admin.py || { echo "ERRO: nao aplicou."; mv web/admin.py.bak web/admin.py; exit 1; }
echo "    OK compila."
git add web/admin.py
git diff --cached --quiet || { git commit -m "feat: painel /admin/comunicacao (Fase B + assertividade QR + uso + atrito)"; git push; }
rm -f web/admin.py.bak
echo "PRONTO (painel). Acesse /admin/comunicacao logado como admin."
