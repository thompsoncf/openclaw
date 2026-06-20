#!/usr/bin/env bash
# atualiza_painel_qr.sh — reescreve a rota /admin/comunicacao mostrando as classes
# (cupom_lido / cupom_sem_qr / pdf_indefinido / outro) e a assertividade SO' de cupom.
# Substitui do decorator ate o FIM do arquivo (a rota foi anexada no fim).
# RODE NA RAIZ: bash atualiza_painel_qr.sh
set -euo pipefail
echo "==> 1/4 Raiz..."; [ -f web/admin.py ] || { echo "ERRO: rode na raiz."; exit 1; }
echo "==> 2/4 Tem a rota?"; grep -q '/admin/comunicacao' web/admin.py || { echo "ERRO: rota /admin/comunicacao nao existe (rode add_painel_comunicacao.sh antes)."; exit 1; }
cp web/admin.py web/admin.py.bak
echo "==> 3/4 Reescrevendo a rota..."
PYBIN=python3; command -v python3 >/dev/null 2>&1 || PYBIN=python
"$PYBIN" - << 'PYEOF'
import sys
p = "web/admin.py"
src = open(p, encoding="utf-8").read()
marca = '@router.get("/admin/comunicacao"'
i = src.find(marca)
if i < 0:
    print("ERRO: marcador da rota nao encontrado.", file=sys.stderr); sys.exit(2)
# remove tudo da rota ate o fim (ela e' a ultima coisa do arquivo)
src = src[:i].rstrip() + "\n\n\n"

rota = r'''@router.get("/admin/comunicacao", response_class=HTMLResponse)
def admin_comunicacao(request: Request):
    # Painel do Modulo de Comunicacao: Fase B + leitura de QR (por CLASSE) + uso + atrito.
    if _admin(request) is None:
        return _NEGADO
    pool = get_pool()
    from finance.estatisticas import pronto_para_fase_b, estatisticas_leituras_qr
    from finance.observabilidade import resumo_uso, dificuldades

    faseb = pronto_para_fase_b(pool)
    qr = estatisticas_leituras_qr(pool)
    uso = resumo_uso(pool, 7)
    atrito = dificuldades(pool, 30)

    def _e(t):
        return (str(t) if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _barra(pct):
        cheio = max(0, min(10, round((pct or 0) / 10)))
        return "█" * cheio + "░" * (10 - cheio)

    fb = "".join(
        "<tr><td>%s</td><td>%s%s/%s%s</td><td><code>%s</code> %s%%</td><td>%s</td></tr>" % (
            _e(m["nome"]), m["valor"], m["unidade"], m["gatilho"], m["unidade"],
            _barra(m["pct"]), m["pct"], "OK" if m["ok"] else "...")
        for m in faseb["metricas"])
    ctx = faseb["contexto"]

    cl = qr["classes"]
    rotulos = {
        "cupom_lido": "Cupom lido (QR ok) - vai pro banco",
        "cupom_sem_qr": "Cupom sem QR (foto, nao leu) - recuperavel",
        "pdf_indefinido": "PDF indefinido (nota OU comprovante)",
        "outro": "Outro",
        "nao_classificado": "Nao classificado (antes do fix)",
    }
    classe_rows = "".join(
        "<tr><td>%s</td><td>%s</td></tr>" % (_e(rotulos.get(k, k)), cl.get(k, 0))
        for k in ("cupom_lido", "cupom_sem_qr", "pdf_indefinido", "outro", "nao_classificado"))

    tipo_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            _e(t["tipo"]), t["total"], t["acertos"], t["falhas"],
            t["kb_medio_acerto"], t["kb_medio_falha"])
        for t in qr["tipos"]) or "<tr><td colspan=6>sem leituras</td></tr>"

    canal = ", ".join("%s: %s" % (_e(x["canal"]), x["qtd"]) for x in uso["por_canal"]) or "-"
    tmid = ", ".join("%s: %s" % (_e(x["tipo"]), x["qtd"]) for x in uso["por_tipo"]) or "-"

    atrito_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            a["id"], _e(a["canal"]), _e(a["tipo_midia"]), _e((a["texto_usuario"] or "")[:60]),
            "nao" if a["sucesso"] is False else "sim", "sim" if a["repetiu"] else "-")
        for a in atrito) or "<tr><td colspan=6>nenhum atrito recente</td></tr>"

    d = {
        "ver": faseb["veredito"], "fb": fb,
        "multiloja": ctx["produtos_multiloja"], "confirm": ctx["produtos_confirmados"],
        "comqr": ctx["cupons_com_qr"], "faltam": ctx.get("cupons_faltando_estimado"),
        "classe_rows": classe_rows, "assert": qr["assertividade_cupom_pct"],
        "fiscais": qr["cupons_fiscais"], "tipo_rows": tipo_rows,
        "total_qr": qr["total_leituras"],
        "dias": uso["dias"], "inter": uso["total"], "suc": uso["taxa_sucesso_pct"],
        "custo": uso["custo_reais"], "canal": canal, "tmid": tmid, "atrito_rows": atrito_rows,
    }

    html = """<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Comunicacao - Zaq admin</title><style>
body{font-family:system-ui,sans-serif;background:#0f1115;color:#e6e6e6;margin:0;padding:1rem}
a{color:#6cf}h2{margin:1.4rem 0 .5rem}.nav{margin-bottom:1rem;font-size:.9rem}
table{border-collapse:collapse;width:100%%;margin:.3rem 0 1rem;font-size:.9rem}
th,td{border:1px solid #2a2f3a;padding:.35rem .5rem;text-align:left}
th{background:#1a1f29}code{color:#7CFC9B}.v{font-weight:bold}
.card{background:#161a22;border:1px solid #2a2f3a;border-radius:8px;padding:.8rem 1rem;margin:.5rem 0}
.hint{color:#9aa;font-size:.82rem}</style></head><body>
<div class=nav><a href="/admin">&larr; admin</a> &nbsp;|&nbsp; <b>Comunicacao</b></div>
<h1>Modulo de Comunicacao</h1>

<h2>Fase B - %(ver)s</h2>
<table><tr><th>Metrica</th><th>Atual/Meta</th><th>Progresso</th><th>OK</th></tr>%(fb)s</table>
<div class=card>Multi-loja (ouro): <span class=v>%(multiloja)s</span> &nbsp;|&nbsp;
Confirmados (>=2): <span class=v>%(confirm)s</span> &nbsp;|&nbsp;
Cupons com QR: <span class=v>%(comqr)s</span> &nbsp;|&nbsp;
Faltam (est.): <span class=v>%(faltam)s</span></div>

<h2>Leitura de cupom (por classe)</h2>
<table><tr><th>Classe</th><th>Qtd</th></tr>%(classe_rows)s</table>
<div class=card>Assertividade de <b>cupom fiscal</b>: <span class=v>%(assert)s%%</span>
(sobre %(fiscais)s cupons fiscais = lido + sem QR)<br>
<span class=hint>Comprovante / PDF indefinido / outro NAO entram nessa conta. Total de midias recebidas: %(total_qr)s.</span></div>

<h2>Qualidade da foto (Foto x PDF)</h2>
<table><tr><th>Tipo</th><th>Total</th><th>QR lido</th><th>Sem QR</th><th>KB lido</th><th>KB sem</th></tr>%(tipo_rows)s</table>
<div class=card class=hint>KB do "lido" > KB do "sem" indica foto pequena/comprimida derrubando o QR.</div>

<h2>Uso (ultimos %(dias)s dias)</h2>
<div class=card>Interacoes: <span class=v>%(inter)s</span> &nbsp;|&nbsp;
Sucesso: <span class=v>%(suc)s%%</span> &nbsp;|&nbsp; Custo: <span class=v>R$ %(custo)s</span><br>
Por canal: %(canal)s<br>Por tipo: %(tmid)s</div>

<h2>Atrito (falhas e repeticoes)</h2>
<table><tr><th>id</th><th>canal</th><th>tipo</th><th>texto</th><th>sucesso</th><th>repetiu</th></tr>%(atrito_rows)s</table>
</body></html>""" % d
    return HTMLResponse(html)
'''
src = src.rstrip() + "\n\n\n" + rota
open(p, "w", encoding="utf-8").write(src)
print("  rota /admin/comunicacao reescrita.")
PYEOF
echo "==> 4/4 Validar + commit..."
"$PYBIN" -m py_compile web/admin.py 2>/tmp/_p_err || { echo "ERRO compila:"; cat /tmp/_p_err; mv web/admin.py.bak web/admin.py; exit 1; }
grep -q "Leitura de cupom (por classe)" web/admin.py || { echo "ERRO: nao aplicou."; mv web/admin.py.bak web/admin.py; exit 1; }
echo "    OK compila."
git add web/admin.py
git diff --cached --quiet || { git commit -m "feat: painel comunicacao mostra classes de cupom + assertividade honesta"; git push; }
rm -f web/admin.py.bak
echo "PRONTO (painel)."
