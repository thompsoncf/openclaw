"""O mapa das obras: /painel/obras/mapa — a planta do empreendimento em 3D.

Desenho aprovado pelo dono em 02/10/2026 (docs/mockups/obras_mapa_3d.html,
"segue as recomendações"). PR 1 de 3: a área, a planta, o editor de riscar, o
mapa 2D/3D e o perfil com a casa por camadas.

A TÉCNICA 3D É A DOS STANDS (web/loja_stands.py, validada na Outlet Chic): o
chão deita com `rotateX` e cada lote é um bloco que sobe por `translateZ` +
sombra dura — aqui, a ALTURA DO BLOCO É O ANDAMENTO da obra, então dá pra ver
de longe o que está pronto (ganha faixa de telhado), o que está parado e o que
tem alerta. O perfil desenha a casa camada por camada (fundação → pilares →
paredes → telhado → pintura) com três faces por caixa — a receita calibrada no
mockup, que os números vêm de `finance.obra_mapas.vista`.

AS ROTAS DESTE MÓDULO ENTRAM ANTES das de web/painel_obras.py (web/app.py):
`/painel/obras/mapa` bateria na ficha `/painel/obras/{obra_id}` e morreria no
422 do int. Quem mexer na ordem dos routers quebra a página inteira.

Quem vê é quem vê Obras (dono, gestor, financeiro — `_acesso` é importado de
lá). Tudo que escreve é ação explícita no painel; o agente não risca lote.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from db.conexao import get_pool
from finance import obra_mapas as om
from finance import obras as ob
from web.painel_obras import _acesso, _volta
from web.portal import _env, _render

router = APIRouter()
_log = logging.getLogger("openclaw.painel_obras_mapa")


def _json_pra_tela(dados) -> str:
    """JSON pra dentro de <script>: o `<` escapado impede que um rótulo com
    `</script>` escrito pelo usuário feche a tag no meio do caminho."""
    return json.dumps(dados, ensure_ascii=False).replace("<", "\\u003c")


# ─────────────────────────────────────────────────────────────── o mapa
@router.get("/painel/obras/mapa", response_class=HTMLResponse)
def mapa(request: Request):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    mapas = om.listar(pool, conta[0])
    pedido = request.query_params.get("m") or ""
    m_id = int(pedido) if pedido.isdigit() else (mapas[0]["id"] if mapas else None)
    v = None
    if m_id and any(x["id"] == m_id for x in mapas):
        v = om.vista(pool, conta[0], m_id)
    return _render("obras_mapa", request, titulo="Mapa das obras", secao_ativa="obras",
                   mapas=mapas, v=v,
                   dados=_json_pra_tela({"altura": v["mapa"]["altura"],
                                         "tem_planta": v["mapa"]["tem_planta"],
                                         "id": v["mapa"]["id"],
                                         "lotes": v["lotes"]} if v else None),
                   erro=(request.query_params.get("erro") or "").strip())


@router.post("/painel/obras/mapa/nova")
def mapa_novo(request: Request, nome: str = Form(""), cidade: str = Form("")):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        m = om.criar(get_pool(), conta[0], nome, cidade)
    except ValueError as e:
        return _volta("/painel/obras/mapa", str(e))
    return RedirectResponse(f"/painel/obras/mapa/{m['id']}/editar", status_code=303)


@router.post("/painel/obras/mapa/{mapa_id}/dados")
def mapa_dados(request: Request, mapa_id: int, nome: str = Form(""),
               cidade: str = Form(""), apagar: str = Form("")):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        if apagar:
            om.apagar(get_pool(), conta[0], mapa_id)
            return RedirectResponse("/painel/obras/mapa", status_code=303)
        om.editar(get_pool(), conta[0], mapa_id, nome, cidade)
    except ValueError as e:
        return _volta(f"/painel/obras/mapa?m={mapa_id}", str(e))
    return RedirectResponse(f"/painel/obras/mapa?m={mapa_id}", status_code=303)


@router.post("/painel/obras/mapa/{mapa_id}/planta")
def planta_subir(request: Request, mapa_id: int, planta: UploadFile = File(...),
                 volta: str = Form("")):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    destino = (f"/painel/obras/mapa/{mapa_id}/editar" if volta == "editor"
               else f"/painel/obras/mapa?m={mapa_id}")
    try:
        om.guardar_planta(get_pool(), conta[0], mapa_id,
                          planta.file.read(), planta.content_type or "")
    except ValueError as e:
        return _volta(destino, str(e))
    return RedirectResponse(destino, status_code=303)


@router.get("/painel/obras/mapa/{mapa_id}/planta")
def planta_ver(request: Request, mapa_id: int):
    """A planta sai do bucket privado por aqui, com a sessão conferida — planta
    de loteamento não vira URL pública (finance/comprovantes.py)."""
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    m = om.obter(get_pool(), conta[0], mapa_id)
    if not m or not m["planta_caminho"]:
        return Response(status_code=404)
    from finance.comprovantes import ler
    try:
        dados, ct = ler(m["planta_caminho"])
    except ValueError:
        return Response(status_code=404)
    return Response(content=dados, media_type=ct,
                    headers={"Cache-Control": "private, max-age=3600"})


# ─────────────────────────────────────────────────────────────── o editor
@router.get("/painel/obras/mapa/{mapa_id}/editar", response_class=HTMLResponse)
def editor(request: Request, mapa_id: int):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    m = om.obter(pool, conta[0], mapa_id)
    if not m:
        return RedirectResponse("/painel/obras/mapa", status_code=303)
    obras = [{"id": o["id"], "nome": o["nome"]}
             for o in ob.listar_obras(pool, conta[0], com_custos=False)
             if o["status"] != "arquivada"]
    return _render("obras_mapa_editor", request, titulo=f"Riscar · {m['nome']}",
                   secao_ativa="obras", m=m,
                   dados=_json_pra_tela({"altura": m["altura"], "tem_planta": m["tem_planta"],
                                         "id": m["id"],
                                         "lotes": om.lotes(pool, conta[0], mapa_id),
                                         "obras": obras}),
                   erro=(request.query_params.get("erro") or "").strip())


@router.post("/painel/obras/mapa/{mapa_id}/lotes")
def salvar_lotes(request: Request, mapa_id: int, dados: str = Form("")):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        lista = json.loads(dados or "[]")
        if not isinstance(lista, list):
            raise ValueError("O desenho veio num formato que não entendi.")
        om.salvar_lotes(get_pool(), conta[0], mapa_id, lista)
    except (ValueError, json.JSONDecodeError) as e:
        return _volta(f"/painel/obras/mapa/{mapa_id}/editar", str(e))
    return RedirectResponse(f"/painel/obras/mapa?m={mapa_id}", status_code=303)


# ─────────────────────────────────────────────────────────────── as telas
#
# O CSS e o JS do motor vêm do mockup aprovado (docs/mockups/obras_mapa_3d.html),
# calibrados no navegador — mexer em transform/clip-path sem olhar a tela de
# verdade é o jeito de descalibrar a casa.

_CSS_MAPA = r"""<style>
.om-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box;
  --f0:#c3cfc9;--f0s:#9aa8a1;--f1:#8fd9bd;--f1s:#5fb394;--f2:#3ec997;--f2s:#23a076;
  --f3:#12a07a;--f3s:#0b7a5c;--pronta:#0f5f4a;--prontas:#07392c;
  --alerta:#f2a33a;--alertas:#c57b17;--terc:#aeb8b3;--tercs:#8a948f}
.om-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.om-topo h2{margin:0;font-size:1.5rem}
.om-sub{color:var(--txt-mut);font-size:.88rem;margin-top:.25rem}
.om-volta{font-size:.85rem;color:var(--txt-mut);text-decoration:none}
.om-erro{background:var(--neon-fundo);border:1px solid var(--neon-borda);border-radius:9px;padding:.55rem .75rem;margin:.8rem 0}
.om-box{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin-top:.8rem}
.om-box summary{cursor:pointer;font-weight:600}
.om-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.6rem;margin-top:.6rem}
.om-box label{display:block;font-size:.7rem;color:var(--txt-mut);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.2rem}
.om-box input,.om-box select{width:100%;box-sizing:border-box}
.om-bt{padding:.35rem .7rem;border-radius:7px;border:1px solid var(--borda);background:transparent;color:inherit;cursor:pointer;font-size:.8rem;text-decoration:none;display:inline-block}
.om-bt.prim{background:var(--verde);border-color:var(--verde);color:#fff}
.om-mut{color:var(--txt-mut);font-size:.78rem}

/* ====== o chão e os blocos (a técnica dos stands) ====== */
.mapa-caixa{background:#10201a;border-radius:14px;padding:14px;margin-top:.8rem}
.mapa-barra{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.emp-sel{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.emp{border:1px solid #2d4a3e;background:#16342a;color:#d7ebe2;border-radius:99px;padding:3px 13px;font-size:13px;cursor:pointer;text-decoration:none}
.emp.on{background:#d7ebe2;color:#10201a;font-weight:700}
.emp.mais{border-style:dashed;color:#8fb5a6}
.mb{appearance:none;border:1px solid #2d4a3e;background:#16342a;color:#d7ebe2;border-radius:8px;
  padding:4px 12px;font-size:13px;cursor:pointer;text-decoration:none}
.mb.on{background:var(--verde);color:#fff;border-color:var(--verde)}
.mapa-rolo{overflow:auto}
.palco{display:flex;justify-content:center;min-width:min-content;perspective:2000px;padding:8px 0 2px}
.chao{position:relative;border-radius:14px;background:
    radial-gradient(120% 90% at 50% 0%, #1b3a2f 0%, #142b23 60%, #10231c 100%);
  box-shadow:inset 0 0 0 1px #24453a;transform-style:preserve-3d;transform-origin:50% 85%;
  transition:transform .5s cubic-bezier(.2,.7,.3,1), box-shadow .5s}
.chao.is-3d{transform:rotateX(50deg) scale(0.96);
  box-shadow:inset 0 0 0 1px #24453a, 0 70px 55px -35px rgba(0,0,0,.6)}
.chao .planta-fundo{position:absolute;inset:14px;border-radius:8px;opacity:.16;pointer-events:none;
  background:repeating-linear-gradient(0deg, transparent 0 34px, #7fd9bd 34px 35px),
             repeating-linear-gradient(90deg, transparent 0 34px, #7fd9bd 34px 35px)}
.chao .planta-img{position:absolute;left:14px;top:14px;border-radius:8px;opacity:.28;pointer-events:none;
  filter:saturate(.6)}
.lote3d{appearance:none;border:none;border-radius:4px;position:absolute;cursor:pointer;
  display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;
  font-size:12px;letter-spacing:.2px;padding:0;transform:translateZ(var(--ext,3px));
  transition:transform .15s ease, box-shadow .15s ease}
.lote3d .pc{position:absolute;right:4px;bottom:2px;font-size:10px;font-weight:800;opacity:.85}
.lote3d.s-f0{background:var(--f0);color:#223;box-shadow:0 var(--ext) 0 var(--f0s), 0 calc(var(--ext) + 4px) 8px rgba(0,0,0,.25)}
.lote3d.s-f1{background:linear-gradient(155deg,#a9e6cf,var(--f1));color:#0c3f2e;box-shadow:0 var(--ext) 0 var(--f1s), 0 calc(var(--ext) + 4px) 8px rgba(0,0,0,.28)}
.lote3d.s-f2{background:linear-gradient(155deg,#5fd8ab,var(--f2));box-shadow:0 var(--ext) 0 var(--f2s), 0 calc(var(--ext) + 4px) 9px rgba(0,0,0,.3)}
.lote3d.s-f3{background:linear-gradient(155deg,#1cb98d,var(--f3));box-shadow:0 var(--ext) 0 var(--f3s), 0 calc(var(--ext) + 4px) 10px rgba(0,0,0,.32)}
.lote3d.s-pronta{background:
    linear-gradient(180deg, rgba(201,111,74,.95) 0 26%, transparent 26%),
    repeating-linear-gradient(90deg, transparent 0 7px, rgba(0,0,0,.12) 7px 8px),
    linear-gradient(155deg,#15735a,var(--pronta));
  box-shadow:0 var(--ext) 0 var(--prontas), 0 calc(var(--ext) + 5px) 12px rgba(0,0,0,.38)}
/* o alerta é um SELO no canto, e a cor do lote continua sendo o andamento: ⚠️ da
   obra (documento, prazo, casa pronta travada) e 🧱 do material (furo, irmãs) */
.lote3d .selos{position:absolute;left:-5px;top:-7px;display:flex;gap:2px;pointer-events:none}
.lote3d .selo{width:17px;height:17px;border-radius:50%;display:grid;place-items:center;
  font-size:10px;line-height:1;background:var(--alerta);box-shadow:0 0 0 2px #10201a, 0 2px 4px rgba(0,0,0,.45)}
.lote3d .selo.mat{background:#e8eef0}
.lote3d.s-vago{background:transparent;color:#9fc0b2;box-shadow:inset 0 0 0 1.5px #3f6a59;--ext:1px}
.lote3d.s-terceiro{background:repeating-linear-gradient(135deg, rgba(255,255,255,.14) 0 3px, transparent 3px 8px), var(--terc);
  color:#39413d;box-shadow:0 2px 0 var(--tercs);--ext:2px;cursor:default}
.lote3d:hover:not(.s-terceiro){transform:translateZ(calc(var(--ext) + 7px))}
.lote3d.is-sel{transform:translateZ(calc(var(--ext) + 12px));outline:2px solid #fff;outline-offset:1px;z-index:3}
.chao:not(.is-3d) .lote3d{box-shadow:none;transform:none}
.chao:not(.is-3d) .lote3d.is-sel{outline:2px solid #fff}
.mapa-leg{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:#cfe9de;margin-top:10px}
.mapa-leg i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:4px;vertical-align:-2px}

/* ====== a casa por camadas (o perfil) ====== */
.perfil{display:grid;grid-template-columns:300px 1fr;gap:16px;align-items:start}
@media (max-width:700px){.perfil{grid-template-columns:1fr}}
.casa-palco{height:250px;display:grid;place-items:center;overflow:hidden;border-radius:10px;
  background:linear-gradient(180deg,#eef5f1,#e2ece6)}
.mundo{position:relative;width:150px;height:110px;transform:rotateX(62deg) rotateZ(-38deg) translateZ(-8px)}
.mundo,.mundo *{transform-style:preserve-3d}
.mundo .f{position:absolute}
.terreno{position:absolute;inset:-26px;border-radius:10px;background:linear-gradient(135deg,#dbe6df,#cbd8d0);
  box-shadow:inset 0 0 0 1px #b9c6bf;transform:translateZ(-1px)}
.fantasma{opacity:.22;filter:grayscale(.4)}
.agua{position:absolute;left:0;width:100%;
  background:repeating-linear-gradient(90deg,#c96f4a 0 9px,#b85f3c 9px 11px);
  box-shadow:inset 0 0 0 1px rgba(140,70,40,.6)}
.agua.norte{clip-path:polygon(0 0, 100% 0, 88% 100%, 12% 100%)}
.agua.sul{clip-path:polygon(12% 0, 88% 0, 100% 100%, 0 100%)}
.porta{position:absolute;width:15px;height:24px;bottom:0;background:#6b4e35;border-radius:2px 2px 0 0}
.jan{position:absolute;width:19px;height:13px;top:7px;background:linear-gradient(135deg,#cfeaff,#86c6ee);box-shadow:0 0 0 2px #fff}
.etlist{font-size:13px;columns:2;column-gap:18px}
.etlist div{break-inside:avoid;padding:2px 0}
.etlist .fez{color:var(--verde-claro,#3ee0a6);font-weight:600}
.etlist .nao{color:var(--txt-mut)}
.pf-kpis{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.pf-kpis span{background:var(--borda);border-radius:8px;padding:4px 10px;font-size:13px}
.pf-alerta{background:var(--ambar-fundo);border:1px solid var(--ambar-borda);border-radius:9px;
  padding:.5rem .7rem;margin:.4rem 0;font-size:.84rem;color:#F0DCA6}
.matlinha{background:var(--borda);border-radius:8px;padding:7px 11px;font-size:13px;margin-top:8px}
</style>"""

_TPL_MAPA = r"""{% extends "base" %}{% block conteudo %}""" + _CSS_MAPA + r"""
<div class="om-pag">
<a class="om-volta" href="/painel/obras">← Obras</a>
<div class="om-topo" style="margin-top:.4rem"><div><h2>Mapa das obras</h2>
  <div class="om-sub">A sua planta com os lotes por cima: a altura de cada bloco é o andamento da obra. Um toque abre a casa.</div></div></div>
{% if erro %}<div class="om-erro">{{ erro|e }}</div>{% endif %}

{% if not mapas %}
<div class="om-box">
  <b>Crie a primeira área de obras</b>
  <p class="om-mut" style="margin:.4rem 0 .6rem">A área é o empreendimento — o loteamento, a quadra de casas, o condomínio. Pode ter várias. Depois de criar, suba a planta (PDF ou foto) e risque os lotes por cima.</p>
  <form method="post" action="/painel/obras/mapa/nova" class="om-grid">
    <div><label>Nome</label><input name="nome" placeholder="Santa Marina 2" required></div>
    <div><label>Cidade (opcional)</label><input name="cidade" placeholder="Paço do Lumiar"></div>
    <div><label>&nbsp;</label><button class="om-bt prim">Criar e riscar os lotes</button></div>
  </form>
</div>
{% else %}
<div class="mapa-caixa">
  <div class="mapa-barra">
    <div class="emp-sel">
      {% for x in mapas %}<a class="emp{{ ' on' if v and v.mapa.id == x.id }}" href="/painel/obras/mapa?m={{ x.id }}">{{ x.nome|e }}</a>{% endfor %}
      <a class="emp mais" href="#nova-area">+ nova área</a>
    </div>
    <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
      <button class="mb" id="bt2d" onclick="setVista('2d')">Planta</button>
      <button class="mb on" id="bt3d" onclick="setVista('3d')">3D</button>
      {% if v %}<a class="mb" href="/painel/obras/mapa/{{ v.mapa.id }}/editar">✏️ Riscar os lotes</a>{% endif %}
    </div>
  </div>
  {% if v %}
  <div class="mapa-rolo"><div class="palco"><div class="chao is-3d" id="chao">
    {% if v.mapa.tem_planta %}<img class="planta-img" id="planta" src="/painel/obras/mapa/{{ v.mapa.id }}/planta" alt="">
    {% else %}<div class="planta-fundo"></div>{% endif %}
  </div></div></div>
  <div class="mapa-leg">
    <span><i style="background:var(--f0)"></i>não começou</span><span><i style="background:var(--f1)"></i>até 30%</span>
    <span><i style="background:var(--f2)"></i>até 60%</span><span><i style="background:var(--f3)"></i>mais de 60%</span>
    <span><i style="background:linear-gradient(180deg,#c96f4a 0 30%,var(--pronta) 30%)"></i>pronta</span>
    <span>⚠️ alerta da obra</span><span>🧱 alerta de material</span>
    <span><i style="box-shadow:inset 0 0 0 1.5px #3f6a59"></i>vago</span>
    <span><i style="background:repeating-linear-gradient(135deg,rgba(255,255,255,.4) 0 2px,var(--terc) 2px 5px)"></i>de terceiro</span>
  </div>
  {% if not v.lotes %}<p style="color:#cfe9de;font-size:.86rem;margin:.8rem 0 0">A área ainda não tem lote riscado.
    <a style="color:#d7ebe2" href="/painel/obras/mapa/{{ v.mapa.id }}/editar">Suba a planta e risque os lotes ›</a></p>{% endif %}
  {% endif %}
</div>

<div class="om-box" id="perfilbox" style="display:none">
  <div style="display:flex;justify-content:space-between;gap:.6rem;flex-wrap:wrap;align-items:baseline">
    <b id="pf-tit"></b><a class="om-bt" id="pf-ficha" href="#" style="display:none">Abrir a ficha completa ›</a></div>
  <div class="perfil" style="margin-top:.6rem">
    <div class="casa-palco"><div class="mundo" id="mundo"></div></div>
    <div>
      <div class="om-mut" id="pf-fase"></div>
      <div class="pf-kpis" id="pf-kpis"></div>
      <div class="pf-alerta" id="pf-alerta" style="display:none"></div>
      <div class="etlist" id="pf-etapas"></div>
      <div class="matlinha" id="pf-mat" style="display:none"></div>
    </div>
  </div>
</div>

{% if v %}
<details class="om-box" id="nova-area"><summary>Dados da área · {{ v.mapa.nome|e }}</summary>
  <form method="post" action="/painel/obras/mapa/{{ v.mapa.id }}/dados" class="om-grid" style="margin-top:.5rem">
    <div><label>Nome</label><input name="nome" value="{{ v.mapa.nome|e }}" required></div>
    <div><label>Cidade</label><input name="cidade" value="{{ v.mapa.cidade|e }}"></div>
    <div><label>&nbsp;</label><button class="om-bt prim">Salvar</button></div>
  </form>
  <form method="post" action="/painel/obras/mapa/{{ v.mapa.id }}/planta" enctype="multipart/form-data" class="om-grid" style="margin-top:.4rem">
    <div><label>{{ 'Trocar a planta' if v.mapa.tem_planta else 'Subir a planta (PDF ou foto)' }}</label>
      <input type="file" name="planta" accept="application/pdf,image/*" required></div>
    <div><label>&nbsp;</label><button class="om-bt">Subir</button></div>
  </form>
  <form method="post" action="/painel/obras/mapa/{{ v.mapa.id }}/dados" onsubmit="return confirm('Apagar esta área e os riscos dela? As obras ligadas não são tocadas.')" style="margin-top:.4rem">
    <input type="hidden" name="nome" value="{{ v.mapa.nome|e }}"><input type="hidden" name="apagar" value="1">
    <button class="om-bt">Apagar a área</button></form>
  <hr style="border:none;border-top:1px solid var(--borda);margin:.8rem 0">
  <form method="post" action="/painel/obras/mapa/nova" class="om-grid">
    <div><label>Nova área</label><input name="nome" placeholder="Santa Marina 3"></div>
    <div><label>Cidade</label><input name="cidade"></div>
    <div><label>&nbsp;</label><button class="om-bt">Criar outra área</button></div>
  </form>
</details>
{% endif %}
{% endif %}
</div>

<script>
var DADOS = {{ dados|safe }};
var sel = null;

function montar(){
  if (!DADOS) return;
  var chao = document.getElementById('chao');
  var rolo = document.querySelector('.mapa-rolo');
  var larg = Math.min(920, Math.max(320, (rolo.clientWidth || 920) - 8));
  var k = larg / 1000, pad = 14, vao = 3;
  chao.style.width = (larg + pad*2) + 'px';
  chao.style.height = (Math.round(DADOS.altura * k) + pad*2) + 'px';
  var img = document.getElementById('planta');
  if (img){ img.style.width = larg + 'px'; img.style.height = Math.round(DADOS.altura * k) + 'px'; }
  chao.querySelectorAll('.lote3d').forEach(function(b){ b.remove(); });
  DADOS.lotes.forEach(function(l, i){
    var b = document.createElement('button');
    b.className = 'lote3d s-' + l.st + (sel === i ? ' is-sel' : '');
    b.style.left = (pad + l.x*k + vao/2) + 'px';
    b.style.top = (pad + l.y*k + vao/2) + 'px';
    b.style.width = Math.max(10, l.larg*k - vao) + 'px';
    b.style.height = Math.max(10, l.alt*k - vao) + 'px';
    if (l.st !== 'vago' && l.st !== 'terceiro')
      b.style.setProperty('--ext', (3 + Math.round(l.pct * 0.13)) + 'px');
    var r = l.rotulo || '·';
    b.innerHTML = (l.st === 'terceiro' ? '<span style="font-size:10px;font-weight:600">terceiro</span>'
                   : l.st === 'vago' ? 'Lt ' + r + '<span class="pc">vago</span>'
                   : 'Lt ' + r + '<span class="pc">' + l.pct + '</span>');
    if (l.alerta || l.mat_alerta){
      var sl = document.createElement('span');
      sl.className = 'selos';
      if (l.alerta) sl.innerHTML += '<i class="selo">⚠️</i>';
      if (l.mat_alerta) sl.innerHTML += '<i class="selo mat">🧱</i>';
      b.appendChild(sl);
      b.title = [l.alerta, l.mat_alerta].filter(Boolean).join(' · ');
    }
    if (l.st !== 'terceiro') b.onclick = function(){ sel = i; montar(); perfil(l); };
    chao.appendChild(b);
  });
}
function setVista(v){
  document.getElementById('chao').classList.toggle('is-3d', v === '3d');
  document.getElementById('bt3d').classList.toggle('on', v === '3d');
  document.getElementById('bt2d').classList.toggle('on', v !== '3d');
}

/* a casa por camadas — a receita calibrada do mockup aprovado */
function caixa(x, y, w, d, h, cores, filhosFrente, classeExtra){
  return '<div class="' + (classeExtra || '') + '" style="position:absolute; left:'+x+'px; top:'+y+'px; width:'+w+'px; height:'+d+'px">' +
    '<div class="f" style="width:'+w+'px; height:'+d+'px; background:'+cores[0]+'; transform:translateZ('+h+'px)"></div>' +
    '<div class="f" style="left:0; top:'+d+'px; width:'+w+'px; height:'+h+'px; background:'+cores[1]+'; transform-origin:top; transform:rotateX(90deg)">'+(filhosFrente||'')+'</div>' +
    '<div class="f" style="left:-'+h+'px; top:0; width:'+h+'px; height:'+d+'px; background:'+cores[2]+'; transform-origin:right; transform:rotateY(90deg)"></div>' +
  '</div>';
}
function casaHtml(c){
  var s = '<div class="terreno"></div>';
  if (!c) return s;
  var g = function(tem){ return tem ? '' : 'fantasma'; };
  var corT = c.pint ? '#efe9d8' : '#d9c9b2', corF = c.pint ? '#e2d9bf' : '#cfa985', corE = c.pint ? '#d6cbae' : '#c49a74';
  s += caixa(0, 0, 150, 110, 8, ['#d7dcd9','#9aa39e','#aab3ae'], '', g(c.fund));
  if (!c.par){
    [[14,14],[124,14],[14,84],[124,84]].forEach(function(p){
      s += '<div class="' + g(c.pil) + '" style="position:absolute; left:0; top:0; transform:translateZ(8px)">' +
           caixa(p[0], p[1], 12, 12, 34, ['#cdd3cf','#8f9893','#a0a8a3']) + '</div>';
    });
  }
  if (c.par){
    var frente = c.esq ? '<div class="porta" style="left:22px"></div><div class="jan" style="left:52px"></div><div class="jan" style="left:84px"></div>' : '';
    s += '<div style="position:absolute; left:0; top:0; transform:translateZ(8px)">' +
         caixa(10, 10, 130, 90, 34, [corT, corF, corE], frente) + '</div>';
    s += '<div class="' + g(c.telh) + '" style="position:absolute; left:0; top:-2px; width:150px; height:114px; transform:translateZ(42px)">' +
      '<div class="f agua norte" style="top:0; height:57px; transform-origin:top; transform:rotateX(32deg)"></div>' +
      '<div class="f agua sul" style="top:57px; height:57px; transform-origin:bottom; transform:rotateX(-32deg)"></div>' +
      '<div class="f" style="left:20px; top:55px; width:110px; height:4px; background:#8e4027; transform:translateZ(30px)"></div>' +
    '</div>';
  }
  return s;
}
var NOMES_PECAS = [['fund','fundação'],['pil','estrutura'],['par','paredes'],['telh','telhado'],['esq','esquadrias'],['pint','pintura']];
function esc(t){ var d = document.createElement('i'); d.textContent = t == null ? '' : t; return d.innerHTML; }
function perfil(l){
  var box = document.getElementById('perfilbox');
  box.style.display = '';
  var vago = !l.obra_id;
  var r = l.rotulo || '·';
  document.getElementById('pf-tit').textContent =
    vago ? ('Lote ' + r + ' — vago (sem obra ligada)')
         : ('Lote ' + r + ' · ' + l.obra_nome + ' — ' + l.pct + '% da obra');
  var ficha = document.getElementById('pf-ficha');
  ficha.style.display = vago ? 'none' : '';
  if (!vago) ficha.href = '/painel/obras/' + l.obra_id;
  document.getElementById('mundo').innerHTML = casaHtml(vago ? null : l.casa);
  var fases = [];
  if (!vago) NOMES_PECAS.forEach(function(p){ if (l.casa[p[0]]) fases.push(p[1]); });
  document.getElementById('pf-fase').textContent =
    vago ? 'terreno vazio — risque a casa no editor pra ligar' :
    (fases.length ? 'feito: ' + fases.join(' · ') : 'obra não começou');
  document.getElementById('pf-kpis').innerHTML = vago
    ? '<span>riscado na planta, sem obra ligada</span>'
    : '<span>gasto <b>' + esc(l.gasto) + '</b></span><span>' + esc(l.previsto) + '</span>' +
      (l.m2 ? '<span>' + esc(l.m2) + '</span>' : '') + '<span>' + esc(l.n_etapas) + '</span>';
  var av = document.getElementById('pf-alerta');
  if (l.alerta){ av.style.display = ''; av.textContent = '⚠️ ' + l.alerta; } else av.style.display = 'none';
  document.getElementById('pf-etapas').innerHTML = vago ? '' : l.etapas.map(function(e){
    return '<div class="' + (e[1] ? 'fez' : 'nao') + '">' + (e[1] ? '✓' : '○') + ' ' + esc(e[0]) + '</div>';
  }).join('');
  var mt = document.getElementById('pf-mat');
  if (!vago && (l.mat || l.mat_alerta)){
    mt.style.display = '';
    mt.innerHTML = '🧱 <b>Material na obra:</b> ' + esc(l.mat || 'nada no saldo') +
      (l.mat_alerta ? ' — <b>⚠️ ' + esc(l.mat_alerta) + '</b>' : '');
  } else mt.style.display = 'none';
  if (window.innerWidth < 700) box.scrollIntoView({behavior:'smooth'});
}
montar();
window.addEventListener('resize', montar);
</script>
{% endblock %}"""

_TPL_EDITOR = r"""{% extends "base" %}{% block conteudo %}""" + _CSS_MAPA + r"""
<style>
.ed-chao{position:relative;border-radius:10px;background:#142b23;box-shadow:inset 0 0 0 1px #24453a;
  touch-action:none;user-select:none;margin:0 auto}
.ed-chao img{position:absolute;left:0;top:0;opacity:.5;pointer-events:none;border-radius:10px}
.ed-chao .fundo{position:absolute;inset:0;opacity:.16;pointer-events:none;
  background:repeating-linear-gradient(0deg, transparent 0 34px, #7fd9bd 34px 35px),
             repeating-linear-gradient(90deg, transparent 0 34px, #7fd9bd 34px 35px)}
.risco{position:absolute;border-radius:3px;display:flex;align-items:center;justify-content:center;
  font-weight:800;font-size:11px;color:#fff;box-sizing:border-box}
.risco.s-meu{background:rgba(18,160,122,.55);box-shadow:inset 0 0 0 1.5px #3ec997}
.risco.s-vago{background:transparent;color:#9fc0b2;box-shadow:inset 0 0 0 1.5px #3f6a59}
.risco.s-terceiro{background:repeating-linear-gradient(135deg, rgba(255,255,255,.14) 0 3px, transparent 3px 8px, rgba(174,184,179,.5) 0 3px);
  color:#cfd6d2;box-shadow:inset 0 0 0 1.5px #8a948f}
.risco.is-sel{outline:2px solid #fff;outline-offset:1px;z-index:3}
.risco .alca{position:absolute;right:-6px;bottom:-6px;width:12px;height:12px;border-radius:3px;
  background:#fff;cursor:nwse-resize;display:none}
.risco.is-sel .alca{display:block}
.ghost{position:absolute;border:1.5px dashed #d7ebe2;border-radius:3px;pointer-events:none}
.ed-fer{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:8px}
.ed-fer .mb.on{background:var(--verde);border-color:var(--verde);color:#fff}
.ed-props{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.6rem;align-items:end}
</style>
<div class="om-pag">
<a class="om-volta" href="/painel/obras/mapa?m={{ m.id }}">← Mapa das obras</a>
<div class="om-topo" style="margin-top:.4rem"><div><h2>Riscar os lotes · {{ m.nome|e }}</h2>
  <div class="om-sub">Risque cada lote por cima da planta. A <b>fileira</b> risca a fila inteira de uma vez e divide em N lotes iguais. Nada é salvo até tocar em “Salvar o desenho”.</div></div></div>
{% if erro %}<div class="om-erro">{{ erro|e }}</div>{% endif %}

{% if not m.tem_planta %}
<div class="om-box"><b>Suba a planta primeiro (opcional)</b>
  <p class="om-mut" style="margin:.3rem 0 .5rem">Com a planta de fundo, riscar é só seguir o desenho. Serve o PDF do loteamento ou uma foto. Dá pra riscar sem planta também — o chão fica quadriculado.</p>
  <form method="post" action="/painel/obras/mapa/{{ m.id }}/planta" enctype="multipart/form-data" class="om-grid">
    <div><label>Planta (PDF ou foto)</label><input type="file" name="planta" accept="application/pdf,image/*" required></div>
    <input type="hidden" name="volta" value="editor">
    <div><label>&nbsp;</label><button class="om-bt prim">Subir a planta</button></div>
  </form></div>
{% endif %}

<div class="mapa-caixa">
  <div class="ed-fer">
    <button class="mb on" id="f-riscar" onclick="setModo('riscar')">✏️ Riscar um lote</button>
    <button class="mb" id="f-fileira" onclick="setModo('fileira')">📏 Fileira (divide em N)</button>
    <button class="mb" id="f-escolher" onclick="setModo('escolher')">🖐 Escolher e mover</button>
    <span style="margin-left:auto;color:#cfe9de;font-size:12px" id="ed-dica"></span>
  </div>
  <div class="mapa-rolo"><div class="ed-chao" id="edchao">
    {% if m.tem_planta %}<img id="planta" src="/painel/obras/mapa/{{ m.id }}/planta" alt="">
    {% else %}<div class="fundo"></div>{% endif %}
  </div></div>
</div>

<div class="om-box" id="props" style="display:none">
  <b>Lote escolhido</b>
  <div class="ed-props" style="margin-top:.5rem">
    <div><label>Número / rótulo</label><input id="p-rotulo" maxlength="20"></div>
    <div><label>Situação</label><select id="p-sit">
      <option value="meu">meu</option><option value="vago">vago (meu, sem obra)</option>
      <option value="terceiro">de terceiro (não é meu)</option></select></div>
    <div><label>Casa ligada</label><select id="p-obra"><option value="">— nenhuma —</option></select></div>
    <div><button class="om-bt" onclick="apagarSel()">Apagar este risco</button></div>
  </div>
</div>

<form method="post" action="/painel/obras/mapa/{{ m.id }}/lotes" class="om-box" style="display:flex;gap:.6rem;align-items:center;flex-wrap:wrap">
  <input type="hidden" name="dados" id="dados">
  <button class="om-bt prim" onclick="document.getElementById('dados').value = JSON.stringify(LOTES)">Salvar o desenho</button>
  <span class="om-mut" id="ed-conta"></span>
  <a class="om-bt" href="/painel/obras/mapa?m={{ m.id }}" style="margin-left:auto">Sair sem salvar</a>
</form>
</div>

<script>
var DADOS = {{ dados|safe }};
var LOTES = DADOS.lotes;                 /* [{id?, rotulo, x, y, larg, alt, situacao, obra_id}] na unidade-1000 */
var OBRAS = DADOS.obras;
var modo = LOTES.length ? 'escolher' : 'riscar', sel = null, K = 1, PAD = 0;

function setModo(m){
  modo = m; sel = null; desenhar();
  ['riscar','fileira','escolher'].forEach(function(x){
    document.getElementById('f-' + x).classList.toggle('on', x === m); });
  document.getElementById('ed-dica').textContent =
    m === 'riscar' ? 'arraste na planta pra riscar um lote'
    : m === 'fileira' ? 'arraste a fileira inteira; eu pergunto em quantos lotes dividir'
    : 'toque num risco pra escolher; arraste pra mover; o quadradinho redimensiona';
}
function proximoNumero(){
  var n = 0;
  LOTES.forEach(function(l){ var v = parseInt(l.rotulo, 10); if (!isNaN(v) && v > n) n = v; });
  return String(n + 1);
}
function desenhar(){
  var chao = document.getElementById('edchao');
  var rolo = document.querySelector('.mapa-rolo');
  var larg = Math.min(920, Math.max(320, (rolo.clientWidth || 920) - 8));
  K = larg / 1000;
  chao.style.width = larg + 'px';
  chao.style.height = Math.round(DADOS.altura * K) + 'px';
  var img = document.getElementById('planta');
  if (img){ img.style.width = larg + 'px'; img.style.height = Math.round(DADOS.altura * K) + 'px'; }
  chao.querySelectorAll('.risco').forEach(function(d){ d.remove(); });
  LOTES.forEach(function(l, i){
    var d = document.createElement('div');
    d.className = 'risco s-' + l.situacao + (sel === i ? ' is-sel' : '');
    d.dataset.i = i;
    d.style.left = (l.x * K) + 'px'; d.style.top = (l.y * K) + 'px';
    d.style.width = (l.larg * K) + 'px'; d.style.height = (l.alt * K) + 'px';
    d.textContent = l.rotulo || '·';
    if (sel === i){ var a = document.createElement('i'); a.className = 'alca'; d.appendChild(a); }
    chao.appendChild(d);
  });
  document.getElementById('ed-conta').textContent = LOTES.length + ' lote(s) riscado(s)';
  var p = document.getElementById('props');
  p.style.display = sel === null ? 'none' : '';
  if (sel !== null){
    var l = LOTES[sel];
    document.getElementById('p-rotulo').value = l.rotulo || '';
    document.getElementById('p-sit').value = l.situacao;
    var so = document.getElementById('p-obra');
    so.innerHTML = '<option value="">— nenhuma —</option>' + OBRAS.map(function(o){
      var usada = LOTES.some(function(x, j){ return j !== sel && x.obra_id === o.id; });
      return '<option value="' + o.id + '"' + (l.obra_id === o.id ? ' selected' : '') +
             (usada ? ' disabled' : '') + '>' + o.nome.replace(/</g, '&lt;') + (usada ? ' (já tem lote)' : '') + '</option>';
    }).join('');
  }
}
document.getElementById('p-rotulo').addEventListener('input', function(){
  if (sel !== null){ LOTES[sel].rotulo = this.value; desenhar(); } });
document.getElementById('p-sit').addEventListener('change', function(){
  if (sel !== null){ LOTES[sel].situacao = this.value;
    if (this.value === 'terceiro') LOTES[sel].obra_id = null; desenhar(); } });
document.getElementById('p-obra').addEventListener('change', function(){
  if (sel !== null){ LOTES[sel].obra_id = this.value ? parseInt(this.value, 10) : null;
    if (LOTES[sel].obra_id && LOTES[sel].situacao !== 'meu') LOTES[sel].situacao = 'meu';
    desenhar(); } });
function apagarSel(){ if (sel !== null){ LOTES.splice(sel, 1); sel = null; desenhar(); } }

/* o arrasto: riscar, fileira, mover e redimensionar — tudo em unidade-1000 */
var arr = null;
function pos(ev){
  var r = document.getElementById('edchao').getBoundingClientRect();
  return {x: Math.max(0, Math.min(1000, Math.round((ev.clientX - r.left) / K))),
          y: Math.max(0, Math.min(6000, Math.round((ev.clientY - r.top) / K)))};
}
var chao = document.getElementById('edchao');
chao.addEventListener('pointerdown', function(ev){
  var p = pos(ev);
  var alvo = ev.target.closest('.risco');
  if (modo === 'escolher'){
    if (ev.target.className === 'alca' && sel !== null){
      arr = {tipo: 'alca', p0: p, l0: Object.assign({}, LOTES[sel])};
    } else if (alvo){
      sel = parseInt(alvo.dataset.i, 10);
      arr = {tipo: 'mover', p0: p, l0: Object.assign({}, LOTES[sel])};
      desenhar();
    } else { sel = null; desenhar(); }
  } else {
    arr = {tipo: modo, p0: p};
    var g = document.createElement('div'); g.className = 'ghost'; g.id = 'ghost';
    chao.appendChild(g);
  }
  chao.setPointerCapture(ev.pointerId);
});
chao.addEventListener('pointermove', function(ev){
  if (!arr) return;
  var p = pos(ev);
  if (arr.tipo === 'mover' && sel !== null){
    var l = LOTES[sel];
    l.x = Math.max(0, Math.min(1000 - l.larg, arr.l0.x + p.x - arr.p0.x));
    l.y = Math.max(0, Math.min(6000 - l.alt, arr.l0.y + p.y - arr.p0.y));
    desenhar();
  } else if (arr.tipo === 'alca' && sel !== null){
    var l2 = LOTES[sel];
    l2.larg = Math.max(8, Math.min(1000 - l2.x, arr.l0.larg + p.x - arr.p0.x));
    l2.alt = Math.max(8, Math.min(6000 - l2.y, arr.l0.alt + p.y - arr.p0.y));
    desenhar();
  } else if (arr.tipo === 'riscar' || arr.tipo === 'fileira'){
    var g = document.getElementById('ghost');
    if (g){
      g.style.left = (Math.min(arr.p0.x, p.x) * K) + 'px';
      g.style.top = (Math.min(arr.p0.y, p.y) * K) + 'px';
      g.style.width = (Math.abs(p.x - arr.p0.x) * K) + 'px';
      g.style.height = (Math.abs(p.y - arr.p0.y) * K) + 'px';
    }
  }
});
chao.addEventListener('pointerup', function(ev){
  if (!arr) return;
  var p = pos(ev), a = arr; arr = null;
  var g = document.getElementById('ghost'); if (g) g.remove();
  if (a.tipo !== 'riscar' && a.tipo !== 'fileira') return;
  var x = Math.min(a.p0.x, p.x), y = Math.min(a.p0.y, p.y);
  var w = Math.abs(p.x - a.p0.x), h = Math.abs(p.y - a.p0.y);
  if (w < 8 || h < 8) return;
  if (a.tipo === 'riscar'){
    LOTES.push({rotulo: proximoNumero(), x: x, y: y, larg: w, alt: h, situacao: 'meu', obra_id: null});
    sel = LOTES.length - 1;
  } else {
    var n = parseInt(prompt('Quantos lotes nessa fileira?', '6') || '0', 10);
    if (!n || n < 1 || n > 60) return;
    for (var i = 0; i < n; i++){
      LOTES.push(w >= h
        ? {rotulo: proximoNumero(), x: Math.round(x + i*w/n), y: y,
           larg: Math.round(w/n), alt: h, situacao: 'meu', obra_id: null}
        : {rotulo: proximoNumero(), x: x, y: Math.round(y + i*h/n),
           larg: w, alt: Math.round(h/n), situacao: 'meu', obra_id: null});
    }
    sel = null;
  }
  desenhar();
});
setModo(modo);
desenhar();
window.addEventListener('resize', desenhar);
</script>
{% endblock %}"""

_env.loader.mapping["obras_mapa"] = _TPL_MAPA
_env.loader.mapping["obras_mapa_editor"] = _TPL_EDITOR
