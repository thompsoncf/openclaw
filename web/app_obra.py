"""O app do mestre de obras: /obra — o canteiro no celular.

PR 3 do desenho aprovado pelo dono em 02/10/2026 (docs/mockups/obras_mapa_3d.html,
seção 4). Quatro gestos, com o dedo: FOTO da etapa, MARCAR etapa feita, apontar
MATERIAL e ver o QUADRO da quadra. O mestre marca direto (com desfazer), e o
dono vê tudo em Obras › "Do campo" (finance/obra_campo.py).

NENHUM VALOR EM DINHEIRO (decisão 4 do dono): este módulo não lê custo, não
calcula custo e não mostra custo. As obras vêm com `com_custos=False`, e o
quadro troca "paga" por "feita" — nem o pagamento da empreitada vaza.

FORA DO /painel, DE PROPÓSITO: o gate de web/app.py só guarda /painel e
/membros, e o mestre não tem nada lá. Quem barra aqui é o `_acesso`: sessão,
capacidade `campo` (contas.equipe) e nicho de construção. O mestre vê só as
obras em que é o `mestre_id`; dono e gestor veem todas.

INSTALA NA TELA INICIAL (manifest + service worker), como o Cockpit — e, como
o v4 do Cockpit, o service worker NÃO guarda nada da obra no aparelho: só a
página "sem internet". Foto de casa e etapa de cliente não ficam no celular.
"""
from __future__ import annotations

import json
from html import escape as esc
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from contas import equipe as eq
from db.conexao import get_pool
from finance import obra_campo as oc
from finance import obras as ob
from finance import raio_x_perfil as rxp
from web.portal import conta_logada, nicho_da_conta

router = APIRouter()
_BASE = "/obra"


def _acesso(request: Request):
    """(conta, papel, membro_id) ou (None, redirect)."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    papel = request.session.get("papel", "dono")
    if not eq.caps_do_papel(papel).get("campo"):
        return None, RedirectResponse(eq.destino_barrado(papel), status_code=303)
    if rxp.perfil_por_nicho(nicho_da_conta(conta)) != "obras":
        return None, RedirectResponse("/painel", status_code=303)
    return (conta, papel, request.session.get("membro_id")), None


def _volta(url: str, ok: str = "", erro: str = "") -> RedirectResponse:
    if ok:
        url += ("&" if "?" in url else "?") + "ok=" + quote(ok)
    if erro:
        url += ("&" if "?" in url else "?") + "erro=" + quote(erro)
    return RedirectResponse(url, status_code=303)


# ─────────────────────────────────────────────────────────────── as telas
_CSS = """
:root{--fundo:#0d1714;--card:#15241f;--borda:#24403a;--txt:#e6f1ec;--mut:#8fb0a3;
  --verde:#12b886;--verde2:#3ee0a6;--ambar:#f2a33a}
*{box-sizing:border-box}
body{margin:0;background:var(--fundo);color:var(--txt);font:16px/1.45 system-ui,"Segoe UI",Roboto,sans-serif;
  -webkit-tap-highlight-color:transparent}
.topo{position:sticky;top:0;z-index:5;background:#0a120f;border-bottom:1px solid var(--borda);
  padding:.7rem 1rem;display:flex;align-items:center;justify-content:space-between;gap:.6rem}
.topo b{font-size:1.05rem}.topo a{color:var(--mut);font-size:.85rem;text-decoration:none}
.pag{max-width:560px;margin:0 auto;padding:.9rem 1rem 3rem}
.msg{border-radius:12px;padding:.7rem .85rem;margin:.2rem 0 .9rem;font-size:.95rem}
.msg.ok{background:#123d2f;border:1px solid var(--verde)}.msg.erro{background:#3d1f12;border:1px solid var(--ambar)}
.card{display:block;background:var(--card);border:1px solid var(--borda);border-radius:14px;padding:.85rem .95rem;
  margin-bottom:.7rem;color:inherit;text-decoration:none}
.card .nm{font-weight:700;font-size:1.05rem}.mut{color:var(--mut);font-size:.85rem}
.bar{height:9px;background:var(--borda);border-radius:5px;overflow:hidden;margin:.45rem 0 .25rem}
.bar i{display:block;height:100%;background:var(--verde)}
.gestos{display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin:.9rem 0}
.gesto{display:flex;flex-direction:column;gap:.2rem;background:var(--card);border:1px solid var(--borda);
  border-radius:14px;padding:.9rem .8rem;color:inherit;text-decoration:none;font-weight:700}
.gesto .ic{font-size:1.7rem}.gesto small{font-weight:400;color:var(--mut);font-size:.8rem}
h2{font-size:1.05rem;margin:1.4rem 0 .6rem}
label{display:block;font-size:.78rem;color:var(--mut);margin:.55rem 0 .25rem;text-transform:uppercase;letter-spacing:.04em}
input,select{width:100%;font:inherit;color:var(--txt);background:#0a120f;border:1px solid var(--borda);
  border-radius:10px;padding:.7rem .75rem}
.bt{display:block;width:100%;margin-top:.8rem;padding:.85rem;border-radius:12px;border:0;font:inherit;font-weight:700;
  background:var(--verde);color:#fff;cursor:pointer}
.bt.sec{background:transparent;border:1px solid var(--borda);color:var(--txt);font-weight:600}
.etapa{display:flex;justify-content:space-between;align-items:center;gap:.6rem;padding:.65rem .75rem;
  border:1px solid var(--borda);border-radius:11px;margin-bottom:.45rem;background:var(--card)}
.etapa.feita{border-color:var(--verde);opacity:.75}
.etapa form{margin:0}.etapa button{padding:.5rem .8rem;border-radius:9px;border:0;background:var(--verde);
  color:#fff;font:inherit;font-weight:700;font-size:.85rem}
.acoes{display:flex;gap:.4rem}.acoes label{flex:1;margin:0}
.acoes input{display:none}.acoes span{display:block;text-align:center;padding:.6rem .3rem;border:1px solid var(--borda);
  border-radius:10px;font-size:.88rem;text-transform:none;letter-spacing:0;color:var(--txt)}
.acoes input:checked+span{background:var(--verde);border-color:var(--verde);font-weight:700}
.feito{display:flex;justify-content:space-between;align-items:center;gap:.5rem;padding:.5rem 0;
  border-bottom:1px solid var(--borda);font-size:.88rem}
.feito.desf{opacity:.5;text-decoration:line-through}
.feito button{background:none;border:1px solid var(--borda);color:var(--mut);border-radius:8px;padding:.3rem .55rem;font:inherit;font-size:.78rem}
.quadro{border-collapse:separate;border-spacing:3px;font-size:.78rem}
.quadro th.v{writing-mode:vertical-rl;transform:rotate(180deg);height:6.5rem;vertical-align:bottom;text-align:left;
  color:var(--mut);font-weight:600}
.quadro td.c{width:1.8rem;height:1.6rem;text-align:center;border-radius:5px;font-weight:800}
.quadro td.feita{background:var(--verde);color:#fff}.quadro td.falta{background:var(--borda);color:var(--mut)}
.rolo{overflow-x:auto}
"""


def _pagina(titulo: str, corpo: str, request: Request, voltar: str = "") -> HTMLResponse:
    ok = request.query_params.get("ok") or ""
    erro = request.query_params.get("erro") or ""
    msg = ((f"<div class='msg ok'>✓ {esc(ok)}</div>" if ok else "") +
           (f"<div class='msg erro'>⚠️ {esc(erro)}</div>" if erro else ""))
    esq = f"<a href='{voltar}'>← voltar</a>" if voltar else "<b>🏗️ Minhas obras</b>"
    html = ("<!doctype html><html lang=pt-br><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            "<meta name=theme-color content='#0a120f'>"
            f"<link rel=manifest href='{_BASE}/manifest.webmanifest'>"
            f"<link rel=icon href='{_BASE}/icon.svg'><link rel=apple-touch-icon href='{_BASE}/icon.svg'>"
            f"<title>{esc(titulo)} · Zaq Obra</title><style>{_CSS}</style></head><body>"
            f"<div class=topo>{esq}<a href='/sair'>sair</a></div>"
            f"<div class=pag>{msg}{corpo}</div>"
            f"<script>if('serviceWorker' in navigator)navigator.serviceWorker.register('{_BASE}/sw.js',{{scope:'{_BASE}'}})</script>"
            "</body></html>")
    return HTMLResponse(html)


def _rot(o: dict) -> str:
    lote = o.get("lote") or ""
    return o["nome"] + (f" · Lote {lote}" if lote else "")


def _com_lote(pool, conta_id: int, obras: list[dict]) -> list[dict]:
    try:
        from finance import obra_grupos as og
        mapa = og.por_obra(pool, conta_id)
    except Exception:  # noqa: BLE001
        mapa = {}
    for o in obras:
        g = mapa.get(o["id"]) or {}
        o["lote"], o["grupo_id"] = g.get("lote", ""), g.get("grupo_id")
    return obras


@router.get("/obra", response_class=HTMLResponse)
def inicio(request: Request):
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, papel, membro_id = ac
    pool = get_pool()
    obras = _com_lote(pool, conta[0], oc.obras_do(pool, conta[0], papel, membro_id))
    if not obras:
        corpo = ("<div class=card><div class=nm>Nenhuma obra sua ainda</div>"
                 "<p class=mut>Peça pro dono escolher você como mestre na ficha da casa "
                 "(Obras › a casa › Dados da obra › Mestre de obras).</p></div>")
    else:
        corpo = "".join(
            f"<a class=card href='{_BASE}/{o['id']}'><div class=nm>{esc(_rot(o))}</div>"
            f"<div class=bar><i style='width:{o['pct']}%'></i></div>"
            f"<div class=mut>{o['pct']}% · "
            f"{('próxima: ' + esc(o['proxima_etapa'].lower())) if o.get('proxima_etapa') else 'todas as etapas feitas'}"
            f"</div></a>" for o in obras)
    feitos = oc.recentes(pool, conta[0], membro_id=membro_id if papel == "mestre" else None, limite=8)
    if feitos:
        corpo += "<h2>O que foi feito</h2>" + _lista_feitos(feitos, papel, membro_id, "/obra")
    return _pagina("Minhas obras", corpo, request)


def _lista_feitos(feitos: list[dict], papel: str, membro_id, volta: str) -> str:
    out = []
    for f in feitos:
        pode_desfazer = not f["desfeito"] and (papel != "mestre" or f["membro_id"] == membro_id)
        bt = (f"<form method=post action='{_BASE}/desfazer/{f['id']}'>"
              f"<input type=hidden name=volta value='{esc(volta)}'><button>desfazer</button></form>"
              if pode_desfazer else "")
        quando = f["quando"].strftime("%d/%m %H:%M") if f.get("quando") else ""
        out.append(f"<div class='feito{' desf' if f['desfeito'] else ''}'><div>"
                   f"<b>{esc(f['obra'])}</b> — {esc(f['descricao'])}"
                   f"<div class=mut>{esc(quando)}{' · ' + esc(f['quem']) if papel != 'mestre' else ''}</div>"
                   f"</div>{bt}</div>")
    return "".join(out)


# `:int` — sem ele, /obra/icon.svg e /obra/sw.js cairiam aqui e morreriam no 422
@router.get("/obra/{obra_id:int}", response_class=HTMLResponse)
def obra(request: Request, obra_id: int):
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, papel, membro_id = ac
    pool = get_pool()
    if not oc.pode(pool, conta[0], papel, membro_id, obra_id):
        return _volta("/obra", erro="Essa obra não está com você.")
    o = ob.obter_obra(pool, conta[0], obra_id)
    o = _com_lote(pool, conta[0], [o])[0]
    pendentes = [e for e in o["etapas"] if not e["concluida_em"]]
    feitas = [e for e in o["etapas"] if e["concluida_em"]]
    opc_etapa = "<option value=''>sem etapa</option>" + "".join(
        f"<option value='{esc(e['nome'])}'{' selected' if pendentes and e['id'] == pendentes[0]['id'] else ''}>"
        f"{esc(e['nome'])}</option>" for e in o["etapas"])
    try:
        from finance import obra_material as om
        with pool.connection() as c:
            nomes = [r[1] for r in om._materiais(c, conta[0])]
        linhas = om.quadro_da_obra(pool, conta[0], obra_id)
        saldo = "".join(f"<div class=feito><span>{esc(r['nome'])}</span><b>{esc(om.rotulo(r['saldo'], r['unidade']))}</b></div>"
                        for r in linhas if r["saldo"] != 0)
    except Exception:  # noqa: BLE001 — sem a 484
        nomes, saldo = [], ""
    # fora da f-string: o Python 3.11 do CI não aceita barra dentro da expressão
    opc_mat = "".join('<option value="' + esc(n) + '">' for n in nomes)
    gestos = (f"<div class=gestos>"
              f"<a class=gesto href='#foto'><span class=ic>📷</span>Foto da etapa<small>a prova do que ficou pronto</small></a>"
              f"<a class=gesto href='#etapa'><span class=ic>✅</span>Marcar etapa<small>{len(pendentes)} faltando</small></a>"
              f"<a class=gesto href='#material'><span class=ic>🧱</span>Material<small>usei · levei · chegou</small></a>"
              + (f"<a class=gesto href='{_BASE}/quadra/{o['grupo_id']}'><span class=ic>🗺️</span>Quadro da quadra<small>as casas lado a lado</small></a>"
                 if o.get("grupo_id") else
                 "<a class=gesto href='/obra'><span class=ic>🏗️</span>Minhas obras<small>voltar pra lista</small></a>")
              + "</div>")
    corpo = (f"<div class=card><div class=nm>{esc(_rot(o))}</div>"
             f"<div class=bar><i style='width:{o['pct']}%'></i></div>"
             f"<div class=mut>{o['pct']}% · {len(feitas)} de {len(o['etapas'])} etapas</div></div>"
             + gestos +
             # 📷 a foto: abre a câmera direto no celular
             f"<h2 id=foto>📷 Foto da etapa</h2>"
             f"<form method=post action='{_BASE}/{obra_id}/foto' enctype='multipart/form-data' class=card>"
             f"<label>A foto</label><input type=file name=foto accept='image/*' capture=environment required>"
             f"<label>De qual etapa</label><select name=etapa>{opc_etapa}</select>"
             f"<button class=bt>Guardar a foto</button></form>"
             # ✅ as etapas: um toque marca
             f"<h2 id=etapa>✅ Etapas</h2>"
             + "".join(f"<div class=etapa><span>{esc(e['nome'])}</span>"
                       f"<form method=post action='{_BASE}/{obra_id}/etapa'>"
                       f"<input type=hidden name=etapa_id value='{e['id']}'><button>pronta ✓</button></form></div>"
                       for e in pendentes)
             + "".join(f"<div class='etapa feita'><span>✓ {esc(e['nome'])}</span></div>" for e in feitas)
             # 🧱 o material: só quantidade
             + f"<h2 id=material>🧱 Material</h2>"
             f"<form method=post action='{_BASE}/{obra_id}/material' class=card>"
             f"<div class=acoes>"
             f"<label><input type=radio name=acao value=usei checked><span>usei</span></label>"
             f"<label><input type=radio name=acao value=levei><span>levei do depósito</span></label>"
             f"<label><input type=radio name=acao value=chegou><span>chegou sem nota</span></label></div>"
             f"<label>Material</label><input name=material list=mats required placeholder='cimento'>"
             f"<datalist id=mats>{opc_mat}</datalist>"
             f"<label>Quantidade</label><input name=quantidade inputmode=decimal required placeholder='15'>"
             f"<label>Unidade (se for material novo)</label><input name=unidade placeholder='saco, m³, barra…'>"
             f"<button class=bt>Apontar</button>"
             f"<p class=mut style='margin:.6rem 0 0'>A nota fotografada já entra sozinha — aqui é o que você usou, "
             f"o que trouxe do depósito ou o que chegou sem nota.</p></form>"
             + (f"<div class=card><div class=mut style='margin-bottom:.3rem'>NA OBRA AGORA</div>{saldo}</div>" if saldo else ""))
    feitos = [f for f in oc.recentes(pool, conta[0], membro_id=membro_id if papel == "mestre" else None,
                                     limite=30) if f["obra_id"] == obra_id][:8]
    if feitos:
        corpo += "<h2>O que foi feito aqui</h2>" + _lista_feitos(feitos, papel, membro_id, f"/obra/{obra_id}")
    return _pagina(o["nome"], corpo, request, voltar="/obra")


@router.post("/obra/{obra_id}/etapa")
def marcar(request: Request, obra_id: int, etapa_id: int = Form(...)):
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, papel, membro_id = ac
    pool = get_pool()
    if not oc.pode(pool, conta[0], papel, membro_id, obra_id):
        return _volta("/obra", erro="Essa obra não está com você.")
    try:
        r = oc.marcar_etapa(pool, conta[0], obra_id, etapa_id, membro_id)
    except ValueError as e:
        return _volta(f"/obra/{obra_id}#etapa", erro=str(e))
    return _volta(f"/obra/{obra_id}", ok=r["frase"])


@router.post("/obra/{obra_id}/foto")
def guardar_foto(request: Request, obra_id: int, foto: UploadFile = File(...), etapa: str = Form("")):
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, papel, membro_id = ac
    pool = get_pool()
    if not oc.pode(pool, conta[0], papel, membro_id, obra_id):
        return _volta("/obra", erro="Essa obra não está com você.")
    try:
        r = oc.foto(pool, conta[0], obra_id, foto.file.read(), foto.content_type or "",
                    etapa=etapa or None, membro_id=membro_id)
    except ValueError as e:
        return _volta(f"/obra/{obra_id}#foto", erro=str(e))
    return _volta(f"/obra/{obra_id}", ok=r["frase"])


@router.post("/obra/{obra_id}/material")
def apontar(request: Request, obra_id: int, acao: str = Form("usei"), material: str = Form(""),
            quantidade: str = Form(""), unidade: str = Form("")):
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, papel, membro_id = ac
    pool = get_pool()
    if not oc.pode(pool, conta[0], papel, membro_id, obra_id):
        return _volta("/obra", erro="Essa obra não está com você.")
    try:
        r = oc.material(pool, conta[0], obra_id, acao=acao, material=material,
                        quantidade=quantidade, unidade=unidade, membro_id=membro_id)
    except ValueError as e:
        return _volta(f"/obra/{obra_id}#material", erro=str(e))
    return _volta(f"/obra/{obra_id}", ok=r["frase"])


@router.post("/obra/desfazer/{evento_id}")
def desfazer(request: Request, evento_id: int, volta: str = Form("/obra")):
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, papel, membro_id = ac
    destino = volta if volta.startswith("/obra") else "/obra"
    try:
        txt = oc.desfazer(get_pool(), conta[0], evento_id,
                          membro_id=membro_id if papel == "mestre" else None)
    except ValueError as e:
        return _volta(destino, erro=str(e))
    return _volta(destino, ok=txt)


@router.get("/obra/quadra/{grupo_id}", response_class=HTMLResponse)
def quadra(request: Request, grupo_id: int):
    """As casas lado a lado, etapa por etapa — só as que esta pessoa vê, e sem
    dinheiro: "paga" vira "feita" e "paga e não feita" vira "falta"."""
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, papel, membro_id = ac
    pool = get_pool()
    from finance import obra_grupos as og
    visiveis = {o["id"] for o in oc.obras_do(pool, conta[0], papel, membro_id)}
    q = og.quadro(pool, conta[0], grupo_id)
    casas = [o for o in q["casas"] if o["id"] in visiveis]
    if not casas:
        return _volta("/obra", erro="Nenhuma casa sua nessa quadra.")
    nome = next((g["nome"] for g in og.listar_grupos(pool, conta[0]) if g["id"] == grupo_id), "Quadra")
    cab = "".join(f"<th class=v>{esc(c['nome'])}</th>" for c in q["colunas"])
    linhas = ""
    for o in casas:
        cel = ""
        for col in q["colunas"]:
            st = q["celulas"][o["id"]].get(col["chave"])
            st = {"paga": "feita", "feita": "feita", "adiantada": "falta", "falta": "falta"}.get(st)
            cel += (f"<td class='c {st}'>{'✓' if st == 'feita' else '·'}</td>" if st
                    else "<td class=c>–</td>")
        rot = f"Lote {o['lote']}" if o.get("lote") else o["nome"]
        linhas += (f"<tr><td style='white-space:nowrap;padding-right:.4rem'>"
                   f"<a style='color:inherit' href='{_BASE}/{o['id']}'>{esc(rot)}</a></td>{cel}"
                   f"<td class=mut><b>{o['pct']}</b></td></tr>")
    corpo = (f"<div class=card><div class=nm>🗺️ {esc(nome)}</div>"
             f"<div class=mut>{len(casas)} casa{'s' if len(casas) != 1 else ''} · ✓ feita · · falta</div></div>"
             f"<div class=rolo><table class=quadro><tr><th></th>{cab}<th>%</th></tr>{linhas}</table></div>")
    return _pagina(nome, corpo, request, voltar="/obra")


# ─────────────────────────────────────────────────────────────── instalar
_ICON = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' rx='14' "
         "fill='#0a120f'/><path d='M14 46h36M18 46V30l14-10 14 10v16' fill='none' stroke='#3ee0a6' "
         "stroke-width='4' stroke-linejoin='round'/><path d='M28 46v-9h8v9' fill='none' stroke='#f2a33a' "
         "stroke-width='4'/></svg>")

# só a página "sem internet" fica no aparelho — nada da obra (como o v4 do Cockpit)
_SW = """
const CACHE='obra-v1';
const OFFLINE='<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'+
 '<body style="background:#0d1714;color:#e6f1ec;font:16px system-ui;padding:2rem">'+
 '<h2>Sem internet</h2><p>Assim que o sinal voltar, é só tentar de novo.</p>'+
 '<button onclick="location.reload()" style="padding:.8rem 1rem;border-radius:10px;border:0;background:#12b886;color:#fff;font:inherit">Tentar de novo</button>';
self.addEventListener('install',e=>{self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(
  caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k))))
    .then(()=>self.clients.claim()));});
self.addEventListener('fetch',e=>{
  if(e.request.mode!=='navigate')return;
  e.respondWith(fetch(e.request).catch(()=>new Response(OFFLINE,{headers:{'Content-Type':'text/html; charset=utf-8'}})));
});
"""


@router.get("/obra/icon.svg", include_in_schema=False)
def icone():
    return Response(_ICON, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=604800"})


@router.get("/obra/manifest.webmanifest", include_in_schema=False)
def manifest():
    m = {"name": "Zaq — minhas obras", "short_name": "Obra", "start_url": _BASE, "scope": _BASE,
         "display": "standalone", "background_color": "#0a120f", "theme_color": "#0a120f",
         "description": "Foto, etapa e material da obra, do canteiro.",
         "icons": [{"src": f"{_BASE}/icon.svg", "sizes": "any", "type": "image/svg+xml",
                    "purpose": "any maskable"}]}
    return Response(json.dumps(m), media_type="application/manifest+json",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/obra/sw.js", include_in_schema=False)
def service_worker():
    return Response(_SW, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": _BASE})
