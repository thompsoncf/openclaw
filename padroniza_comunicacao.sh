#!/usr/bin/env bash
# padroniza_comunicacao.sh — poe /admin/comunicacao no MESMO padrao das outras abas
# (template Jinja que estende "abase": menu + CSS responsivo + mobile alinhado).
# Substitui a pagina auto-contida por template+rota no molde do projeto.
# RODE NA RAIZ: bash padroniza_comunicacao.sh
set -euo pipefail
echo "==> 1/4 Raiz..."; [ -f web/admin.py ] || { echo "ERRO: rode na raiz."; exit 1; }
grep -q '/admin/comunicacao' web/admin.py || { echo "ERRO: rota nao existe."; exit 1; }
echo "==> 2/4 Ja padronizado?"; if grep -q '_ADMIN_COMUNICACAO' web/admin.py; then echo "JA padronizado. Nada a fazer."; exit 0; fi
cp web/admin.py web/admin.py.bak
echo "==> 3/4 Editando..."
PYBIN=python3; command -v python3 >/dev/null 2>&1 || PYBIN=python
"$PYBIN" - << 'PYEOF'
import sys
p = "web/admin.py"
src = open(p, encoding="utf-8").read()

TEMPLATE = '''_ADMIN_COMUNICACAO = """{% extends "abase" %}{% block conteudo %}
<h1>Comunicacao</h1>
{% if aviso %}<div class="card" style="border-color:#1d9e75">{{ aviso }}</div>{% endif %}

<h2>O que chega: cupom x comprovante (ultimos {{ split.dias }} dias)</h2>
<div class="cards">
<div class="metric"><span>Imagens recebidas</span><b>{{ split.total_imagens }}</b></div>
<div class="metric"><span>Cupom fiscal</span><b style="color:#5dcaa5">{{ split.cupom_fiscal }} ({{ split.pct_cupom }}%)</b></div>
<div class="metric"><span>Comprovante/outro</span><b style="color:#7ab0e8">{{ split.comprovante_ou_outro }} ({{ split.pct_outro }}%)</b></div>
</div>
<p class="mut">Cupom = o agente usou tool de cupom (checar_duplicata / registrar_itens_cupom). O resto, tratou como comprovante. Sinal pela acao, nao por chute.</p>

<div class="card"><h2>Banco de ouro - Fase B: {{ faseb.veredito }}</h2>
<table>
{% for m in faseb.metricas %}<tr>
<td>{{ m.nome }}</td>
<td class="mut">{{ m.valor }}{{ m.unidade }} / {{ m.gatilho }}{{ m.unidade }}</td>
<td style="width:38%"><div style="background:#2a2a2b;border-radius:6px;height:8px">
<div style="width:{{ m.pct }}%;height:8px;border-radius:6px;background:{% if m.ok %}#1d9e75{% else %}#8a5a1c{% endif %}"></div></div></td>
<td style="white-space:nowrap">{% if m.ok %}<span class="tag ativa">ok</span>{% else %}<span class="mut">{{ m.pct }}%</span>{% endif %}</td>
</tr>{% endfor %}
</table>
<p class="mut">Multi-loja (ouro): {{ faseb.contexto.produtos_multiloja }} &middot; Confirmados: {{ faseb.contexto.produtos_confirmados }} &middot; Cupons com QR: {{ faseb.contexto.cupons_com_qr }} &middot; Faltam (est.): {{ faseb.contexto.cupons_faltando_estimado }}</p>
</div>

<div class="card"><h2>Leitura de QR (auditoria por classe)</h2>
<table>
<tr><td>Cupom lido (QR ok)</td><td><b style="color:#5dcaa5">{{ qr.classes.cupom_lido }}</b></td></tr>
<tr><td>Imagem sem QR</td><td>{{ qr.classes.cupom_sem_qr }}</td></tr>
<tr><td>PDF indefinido</td><td>{{ qr.classes.pdf_indefinido }}</td></tr>
<tr><td>Outro</td><td>{{ qr.classes.outro }}</td></tr>
</table>
<p class="mut">Assertividade de cupom: <b>{{ qr.assertividade_cupom_pct }}%</b> sobre {{ qr.cupons_fiscais }} cupons fiscais. Total auditado: {{ qr.total_leituras }}. (comprovante/outro fora da conta)</p>
</div>

<div class="card"><h2>Qualidade da foto (Foto x PDF)</h2>
<table><tr><th>Tipo</th><th>Total</th><th>QR lido</th><th>Sem QR</th><th>KB lido</th><th>KB sem</th></tr>
{% for t in qr.tipos %}<tr><td>{{ t.tipo }}</td><td>{{ t.total }}</td><td>{{ t.acertos }}</td><td>{{ t.falhas }}</td><td class="mut">{{ t.kb_medio_acerto }}</td><td class="mut">{{ t.kb_medio_falha }}</td></tr>{% endfor %}
{% if not qr.tipos %}<tr><td colspan="6" class="mut">sem leituras</td></tr>{% endif %}
</table>
<p class="mut">KB do "lido" &gt; KB do "sem" = foto pequena/comprimida derrubando o QR.</p>
</div>

<div class="card"><h2>Uso (ultimos {{ uso.dias }} dias)</h2>
<div class="cards">
<div class="metric"><span>Interacoes</span><b>{{ uso.total }}</b></div>
<div class="metric"><span>Sucesso</span><b>{{ uso.taxa_sucesso_pct }}%</b></div>
<div class="metric"><span>Custo (R$)</span><b>{{ uso.custo_reais }}</b></div>
</div>
<p class="mut">Por canal: {% for x in uso.por_canal %}{{ x.canal|e }}: {{ x.qtd }}{% if not loop.last %}, {% endif %}{% endfor %} &middot; Por tipo: {% for x in uso.por_tipo %}{{ x.tipo|e }}: {{ x.qtd }}{% if not loop.last %}, {% endif %}{% endfor %}</p>
</div>

<div class="card"><h2>Atrito (falhas e repeticoes)</h2>
<table><tr><th>id</th><th>canal</th><th>tipo</th><th>texto</th><th>sucesso</th><th>repetiu</th></tr>
{% for a in atrito %}<tr><td>{{ a.id }}</td><td class="mut">{{ a.canal|e or '-' }}</td><td class="mut">{{ a.tipo_midia|e or '-' }}</td><td class="mut">{{ (a.texto_usuario or '')[:50]|e }}</td><td>{% if a.sucesso %}sim{% else %}<span class="tag suspensa">nao</span>{% endif %}</td><td>{% if a.repetiu %}sim{% else %}-{% endif %}</td></tr>{% endfor %}
{% if not atrito %}<tr><td colspan="6" class="mut">nenhum atrito recente</td></tr>{% endif %}
</table></div>
{% endblock %}"""


'''

# E1: insere o template ANTES da linha do _env
anc = "_env = Environment(loader=DictLoader({"
if src.count(anc) != 1:
    print(f"ERRO: ancora _env {src.count(anc)}x.", file=sys.stderr); sys.exit(2)
src = src.replace(anc, TEMPLATE + anc, 1)

# E2: registra "acomunic" no DictLoader
anc2 = '"apesquisas": _ADMIN_PESQUISAS}'
if src.count(anc2) != 1:
    print(f"ERRO: ancora DictLoader {src.count(anc2)}x.", file=sys.stderr); sys.exit(2)
src = src.replace(anc2, '"apesquisas": _ADMIN_PESQUISAS, "acomunic": _ADMIN_COMUNICACAO}', 1)

# E3: substitui a rota (do decorator ate o FIM) por uma que renderiza o template
marca = '@router.get("/admin/comunicacao"'
i = src.find(marca)
if i < 0:
    print("ERRO: rota nao encontrada.", file=sys.stderr); sys.exit(2)
src = src[:i].rstrip() + "\n\n\n" + '''@router.get("/admin/comunicacao", response_class=HTMLResponse)
def admin_comunicacao(request: Request):
    if _admin(request) is None:
        return _NEGADO
    pool = get_pool()
    from finance.estatisticas import pronto_para_fase_b, estatisticas_leituras_qr
    from finance.observabilidade import resumo_uso, dificuldades, split_midia
    return HTMLResponse(_env.get_template("acomunic").render(
        faseb=pronto_para_fase_b(pool),
        qr=estatisticas_leituras_qr(pool),
        uso=resumo_uso(pool, 7),
        atrito=dificuldades(pool, 30),
        split=split_midia(pool, 30),
        aviso=request.session.pop("admin_aviso", None)))
'''

open(p, "w", encoding="utf-8").write(src)
print("  template + registro + rota aplicados.")
PYEOF
echo "==> 4/4 Validar + commit..."
"$PYBIN" -m py_compile web/admin.py 2>/tmp/_pc || { echo "ERRO compila:"; cat /tmp/_pc; mv web/admin.py.bak web/admin.py; exit 1; }
grep -q "_ADMIN_COMUNICACAO" web/admin.py && grep -q '"acomunic"' web/admin.py || { echo "ERRO: nao aplicou."; mv web/admin.py.bak web/admin.py; exit 1; }
echo "    OK compila."
git add web/admin.py
git diff --cached --quiet || { git commit -m "fix: /admin/comunicacao no padrao das abas (estende abase, menu + responsivo)"; git push; }
rm -f web/admin.py.bak
echo "PRONTO."
