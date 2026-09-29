"""Página pública de venda de estandes: /e/<slug> (migração 448, finance/evento_stands.py).

SLUG SEM ANO (pedido do dono, 28/09/2026): o link é da EMPRESA, não da edição
— ver o comentário de evento_stands_config em db/migracoes/448_evento_stands.sql.

Resolve a conta pelo slug do MESMO jeito que web/portal.loja_fornecedor (/f/{slug})
já faz — mas o "catálogo" aqui é outra coisa (estande numerado com posição na
planta, não produto de prateleira), então nada do MODELO de dados da loja de
fornecedor é reaproveitado, só o padrão de rota pública sem login.

Sem maquete 3D: uma planta 2D por pavilhão/zona, colorida por status, com um
painel de detalhe ao clicar um estande LIVRE (preço, Pix copiável, upload do
comprovante). Mobile-first — é o link que vai pro Instagram/WhatsApp.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import comprovantes as comprov
from finance import evento_stands as es
from web.portal import _env, brl

router = APIRouter()
_log = logging.getLogger("openclaw.loja_stands")

_TPL_NOME = "evento_stands_publico.html"


def _data_br(d) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _criar_prospeccao_simples(pool, conta_id: int, nome: str, whatsapp: str):
    """Registro MÍNIMO do interessado (nome/whatsapp) na tabela de CRM
    (prospeccao, migração 075) — pra que `evento_stands.prospeccao_id` aponte
    pra algo navegável no Funil, em vez de ficar solto. Não é o fluxo de
    inbound completo (aquele trata mensagem dentro de uma conversa que já
    existe); aqui o visitante da página ainda não conversou com ninguém.

    Best-effort e SILENCIOSO: o upload do comprovante — a parte que importa —
    não pode falhar por causa de um cadastro de lead que é só um bônus."""
    nome = (nome or "").strip()
    if not nome:
        return None
    try:
        with pool.connection() as c:
            pid = c.execute(
                """insert into prospeccao (conta_id, empresa, whatsapp, status, origem)
                   values (%s,%s,%s,'novo','pagina_stands') returning id""",
                (conta_id, nome[:200], (whatsapp or "").strip()[:40] or None)).fetchone()[0]
            c.commit()
        return pid
    except Exception as e:  # noqa: BLE001
        _log.info("loja_stands: não deu pra registrar o interessado: %s: %s",
                  type(e).__name__, e)
        return None


@router.get("/e/{slug}", response_class=HTMLResponse)
def loja_stands(request: Request, slug: str):
    pool = get_pool()
    cfg = es.buscar_config_por_slug(pool, slug)
    if cfg is None:
        return HTMLResponse("<h1>Página não encontrada</h1>", status_code=404)
    conta_id = cfg["conta_id"]
    stands = es.listar(pool, conta_id)
    with pool.connection() as c:
        r = c.execute("select nome from contas where id=%s", (conta_id,)).fetchone()
    empresa_nome = (r[0] if r else "") or cfg["slug"]

    # agrupa pra render: pavilhão -> zona -> [estandes], na ordem que `listar`
    # já devolve (pavilhão, zona, ordem, código).
    pavilhoes: dict = {}
    for s in stands:
        zonas = pavilhoes.setdefault(s["pavilhao"], {})
        zonas.setdefault(s["zona"] or "", []).append(s)

    totais = {"livre": 0, "pre_reservado": 0, "vendido": 0}
    for s in stands:
        totais[s["status"]] = totais.get(s["status"], 0) + 1

    # SÓ o que é público vai pro JS (nunca comprovante_url/prospeccao_id/etc):
    # é este objeto que abastece o painel de detalhe ao clicar um estande.
    stands_json = json.dumps({
        s["codigo"]: {"pavilhao": s["pavilhao"], "zona": s["zona"] or "",
                      "tamanho": s["tamanho"],
                      "preco": brl(s["preco_centavos"]) if s["preco_centavos"] else None}
        for s in stands
    })

    html = _env.get_template(_TPL_NOME).render(
        cfg=cfg, empresa_nome=empresa_nome, pavilhoes=pavilhoes, totais=totais,
        n_total=len(stands), stands_json=stands_json, brl=brl, data_br=_data_br,
        msg=(request.query_params.get("msg") or ""),
        msg_codigo=(request.query_params.get("codigo") or ""),
        sem_storage=not comprov.configurado())
    return HTMLResponse(html)


@router.post("/e/{slug}/comprovante")
async def loja_stands_comprovante(request: Request, slug: str,
                                  codigo: str = Form(...), nome: str = Form(""),
                                  whatsapp: str = Form(""),
                                  arquivo: UploadFile = File(...)):
    """Recebe o comprovante do sinal — é ESTE POST que, no modo 'pagamento',
    trava o estande (livre -> pre_reservado). Sem login: qualquer visitante da
    página pode mandar, pro modo mais rápido possível de reservar (é o pedido
    do dono: concorrência real entre interessados até o Pix cair)."""
    pool = get_pool()
    cfg = es.buscar_config_por_slug(pool, slug)
    if cfg is None:
        return HTMLResponse("<h1>Página não encontrada</h1>", status_code=404)
    conta_id = cfg["conta_id"]
    codigo = (codigo or "").strip()
    if not codigo:
        return RedirectResponse(f"/e/{slug}?msg=erro_generico", status_code=303)

    conteudo = await arquivo.read()
    prospeccao_id = _criar_prospeccao_simples(pool, conta_id, nome, whatsapp)
    r = es.subir_e_registrar_comprovante(pool, conta_id, codigo, conteudo,
                                         arquivo.content_type or "",
                                         prospeccao_id=prospeccao_id)
    if not r["ok"]:
        _log.info("loja_stands: comprovante recusado (%s/%s): %s", conta_id, codigo,
                  r.get("erro"))
        return RedirectResponse(f"/e/{slug}?msg=erro&codigo={codigo}", status_code=303)
    return RedirectResponse(f"/e/{slug}?msg=ok&codigo={codigo}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────
# Template — registrado como string no MESMO `_env` do portal (o padrão de
# web/recibo_publico.py e outras páginas públicas): a extensão .html no NOME
# é o que faz o Jinja escapar por padrão (select_autoescape olha a extensão),
# e nome/observação de interessado digitados na página passam por aqui.
# ─────────────────────────────────────────────────────────────────────────
_TPL = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{{ empresa_nome }}{% if cfg.edicao_label %} — {{ cfg.edicao_label }}{% endif %}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Manrope:wght@400;600;800&family=IBM+Plex+Mono:wght@500;600&display=swap" rel="stylesheet">
{% raw %}<style>
:root{ --preto:#0A0A0A; --mint:#16E3AE; --amar:#FFDE2E; --card:#151515; --bord:#2A2A2A; --txt:#F2F2F0; --sub:#9A9A96; }
*{box-sizing:border-box}
body{margin:0;background:var(--preto);color:var(--txt);font-family:'Manrope',sans-serif;padding-bottom:40px}
h1,h2,.titulo{font-family:'Anton',sans-serif;letter-spacing:.02em;text-transform:uppercase}
.mono{font-family:'IBM Plex Mono',monospace}
.topo{padding:22px 16px 14px;border-bottom:1px solid var(--bord);position:sticky;top:0;background:rgba(10,10,10,.94);backdrop-filter:blur(6px);z-index:5}
.topo h1{margin:0;font-size:26px;color:var(--mint)}
.topo .sub{color:var(--sub);font-size:13px;margin-top:4px}
.topo .datas{color:var(--amar);font-size:12.5px;margin-top:6px;font-weight:600}
.legenda{display:flex;gap:14px;flex-wrap:wrap;margin-top:12px;font-size:12px;color:var(--sub)}
.legenda span{display:inline-flex;align-items:center;gap:5px}
.dot{width:10px;height:10px;border-radius:3px;display:inline-block}
.dot.livre{background:transparent;border:2px solid var(--mint)}
.dot.pre_reservado{background:var(--amar)}
.dot.vendido{background:#4A4A4A}
.wrap{max-width:900px;margin:0 auto;padding:0 12px}
.msg{margin:14px 12px 0;padding:12px 14px;border-radius:10px;font-size:13.5px;font-weight:600}
.msg.ok{background:rgba(22,227,174,.12);border:1px solid var(--mint);color:var(--mint)}
.msg.erro{background:rgba(255,222,46,.1);border:1px solid var(--amar);color:var(--amar)}
.pav{margin:22px 12px 0}
.pav h2{font-size:18px;color:var(--txt);margin:0 0 4px}
.zona{margin-top:14px}
.zona .nome{font-size:12px;color:var(--sub);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(64px,1fr));gap:8px}
.stand{border-radius:9px;padding:8px 4px 7px;text-align:center;cursor:pointer;border:2px solid var(--bord);background:var(--card);transition:transform .1s}
.stand:active{transform:scale(.96)}
.stand .cod{font-family:'IBM Plex Mono',monospace;font-weight:600;font-size:13px}
.stand .tam{font-size:9.5px;color:var(--sub);margin-top:2px}
.stand.livre{border-color:var(--mint)}
.stand.pre_reservado{border-color:var(--amar);background:rgba(255,222,46,.08)}
.stand.vendido{border-color:#3A3A3A;background:#1A1A1A;color:#6A6A6A;cursor:default}
.stand.vendido .tam{color:#5A5A5A}
.resumo{display:flex;gap:18px;margin-top:10px;font-size:12px;color:var(--sub)}
.resumo b{color:var(--txt)}
/* painel de detalhe */
.veu{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;z-index:20}
.veu.on{display:block}
.painel{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:2px solid var(--mint);border-radius:16px 16px 0 0;padding:20px 18px 26px;z-index:21;transform:translateY(110%);transition:transform .22s ease;max-height:85vh;overflow:auto}
.painel.on{transform:translateY(0)}
.painel .fechar{position:absolute;right:14px;top:14px;background:none;border:none;color:var(--sub);font-size:22px;cursor:pointer;line-height:1}
.painel h3{font-family:'IBM Plex Mono',monospace;color:var(--mint);font-size:20px;margin:0 0 2px}
.painel .info{color:var(--sub);font-size:13px;margin-bottom:14px}
.preco{font-family:'Anton',sans-serif;color:var(--amar);font-size:28px;margin:6px 0 16px}
.pix{background:#0F0F0F;border:1px solid var(--bord);border-radius:10px;padding:12px 14px;margin-bottom:16px}
.pix .lbl{font-size:11px;color:var(--sub);text-transform:uppercase;letter-spacing:.05em}
.pix .chave{font-family:'IBM Plex Mono',monospace;font-size:14px;margin-top:4px;word-break:break-all}
.pix .titular{font-size:12px;color:var(--sub);margin-top:4px}
.copiar{margin-top:8px;background:var(--mint);color:#04231B;border:none;border-radius:8px;padding:8px 12px;font-weight:700;font-size:12.5px;cursor:pointer}
form.up{display:flex;flex-direction:column;gap:10px}
form.up label{font-size:12px;color:var(--sub)}
form.up input[type=text]{background:#0F0F0F;border:1px solid var(--bord);border-radius:8px;padding:10px;color:var(--txt);font-size:14px;font-family:inherit}
form.up input[type=file]{color:var(--sub);font-size:13px}
.enviar{background:var(--mint);color:#04231B;border:none;border-radius:10px;padding:13px;font-weight:800;font-size:15px;cursor:pointer;margin-top:4px}
.wa{display:inline-block;margin-top:10px;color:var(--sub);font-size:12.5px;text-decoration:underline}
.avisosem{background:rgba(255,222,46,.1);border:1px solid var(--amar);color:var(--amar);padding:10px;border-radius:8px;font-size:12.5px;margin-top:10px}
.tag-vendido, .tag-pre{font-size:12px;color:var(--sub);margin-top:2px}
</style>{% endraw %}
</head><body>
<div class="topo">
  <h1>{{ empresa_nome }}</h1>
  <div class="sub">{% if cfg.edicao_label %}{{ cfg.edicao_label }}{% endif %}{% if cfg.evento_local %} · {{ cfg.evento_local }}{% endif %}</div>
  {% if cfg.evento_inicio %}<div class="datas">{{ data_br(cfg.evento_inicio) }}{% if cfg.evento_fim and cfg.evento_fim != cfg.evento_inicio %} a {{ data_br(cfg.evento_fim) }}{% endif %}</div>{% endif %}
  <div class="legenda">
    <span><i class="dot livre"></i> Livre ({{ totais.get('livre',0) }})</span>
    <span><i class="dot pre_reservado"></i> Em análise ({{ totais.get('pre_reservado',0) }})</span>
    <span><i class="dot vendido"></i> Vendido ({{ totais.get('vendido',0) }})</span>
  </div>
</div>

{% if msg == 'ok' %}<div class="msg ok wrap">✓ Comprovante recebido{% if msg_codigo %} pro estande {{ msg_codigo }}{% endif %}! A equipe confere e confirma em breve.</div>{% endif %}
{% if msg == 'erro' %}<div class="msg erro wrap">Não deu pra registrar o comprovante{% if msg_codigo %} do estande {{ msg_codigo }}{% endif %}. Ele pode já ter sido vendido — dá uma olhada no mapa e tenta outro, ou chama no WhatsApp.</div>{% endif %}
{% if msg == 'erro_generico' %}<div class="msg erro wrap">Não deu pra processar. Tenta de novo.</div>{% endif %}

<div class="wrap">
{% if sem_storage %}<div class="avisosem" style="margin:14px 0">⚠ Upload de comprovante temporariamente indisponível — chama no WhatsApp.</div>{% endif %}
{% if n_total == 0 %}<p style="color:var(--sub);margin-top:30px">Nenhum estande cadastrado ainda.</p>{% endif %}

{% for pavilhao, zonas in pavilhoes.items() %}
<div class="pav">
  <h2>{{ pavilhao|replace('_',' ')|title }}</h2>
  {% for zona, lista in zonas.items() %}
  <div class="zona">
    {% if zona %}<div class="nome">{{ zona }}</div>{% endif %}
    <div class="grid">
      {% for s in lista %}
      <div class="stand {{ s.status }}" data-codigo="{{ s.codigo }}" {% if s.status == 'livre' %}onclick="abrir('{{ s.codigo }}')"{% endif %}>
        <div class="cod">{{ s.codigo }}</div>
        <div class="tam">{{ s.tamanho }}</div>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endfor %}
</div>
{% endfor %}
</div>

<div class="veu" id="veu" onclick="fechar()"></div>
<div class="painel" id="painel">
  <button class="fechar" onclick="fechar()">✕</button>
  <h3 id="p-cod"></h3>
  <div class="info" id="p-info"></div>
  <div id="p-preco" class="preco"></div>
  <div class="pix">
    <div class="lbl">Chave Pix</div>
    <div class="chave mono" id="p-pix">{{ cfg.pix_chave or '—' }}</div>
    {% if cfg.pix_titular %}<div class="titular">{{ cfg.pix_titular }}</div>{% endif %}
    {% if cfg.pix_chave %}<button class="copiar" onclick="copiarPix()">Copiar chave</button>{% endif %}
  </div>
  {% if not sem_storage and cfg.pix_chave %}
  <form class="up" method="post" action="/e/{{ cfg.slug }}/comprovante" enctype="multipart/form-data">
    <input type="hidden" name="codigo" id="p-codigo-form">
    <label>Seu nome
      <input type="text" name="nome" required></label>
    <label>WhatsApp (opcional)
      <input type="text" name="whatsapp"></label>
    <label>Comprovante do Pix (foto ou PDF)
      <input type="file" name="arquivo" accept="image/*,application/pdf" required></label>
    <button class="enviar" type="submit">Enviar comprovante e reservar</button>
  </form>
  {% elif sem_storage %}
  <div class="avisosem">Upload indisponível no momento — manda o comprovante pelo WhatsApp.</div>
  {% endif %}
  {% if cfg.whatsapp_numero %}
  <a class="wa" href="https://wa.me/{{ cfg.whatsapp_numero }}" target="_blank" rel="noopener">Prefere falar no WhatsApp?</a>
  {% endif %}
</div>

<script>
var STANDS = {{ stands_json|safe }};
function abrir(codigo){
  var s = STANDS[codigo];
  if(!s) return;
  document.getElementById('p-cod').textContent = codigo;
  document.getElementById('p-info').textContent = (s.pavilhao||'').replace('_',' ') + (s.zona ? ' · '+s.zona : '') + ' · ' + s.tamanho;
  document.getElementById('p-preco').textContent = s.preco ? s.preco : 'Consultar valor';
  document.getElementById('p-codigo-form').value = codigo;
  document.getElementById('veu').classList.add('on');
  document.getElementById('painel').classList.add('on');
}
function fechar(){
  document.getElementById('veu').classList.remove('on');
  document.getElementById('painel').classList.remove('on');
}
function copiarPix(){
  var chave = document.getElementById('p-pix').textContent.trim();
  if(!chave || chave === '—') return;
  navigator.clipboard && navigator.clipboard.writeText(chave).then(function(){
    var b = document.querySelector('.copiar');
    if(b){ var t = b.textContent; b.textContent = 'Copiado ✓'; setTimeout(function(){ b.textContent = t; }, 1600); }
  });
}
{% if msg_codigo %}
document.addEventListener('DOMContentLoaded', function(){
  var el = document.querySelector('[data-codigo="{{ msg_codigo }}"]');
  if(el) el.scrollIntoView({behavior:'smooth', block:'center'});
});
{% endif %}
</script>
</body></html>
"""

_env.loader.mapping[_TPL_NOME] = _TPL
