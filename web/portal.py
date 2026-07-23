"""Portal do OpenClaw: cadastro, login e painel (Bloco A+B+C).

Vive dentro do openclaw-web. Regra sagrada: toda pagina logada enxerga
APENAS a conta da sessao (isolamento multi-tenant na camada web).
Senhas: hash scrypt (stdlib) com sal aleatorio - nunca em texto puro.
"""
import hashlib
import json
import logging
import os
import secrets

from fastapi import APIRouter, Request, Form, Body, BackgroundTasks, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from jinja2 import Environment, DictLoader, select_autoescape
from datetime import date as _date

from datetime import date

log = logging.getLogger("zaq.portal")

from db.conexao import get_pool
from contas import contas as ct
from contas.permissoes import pode_financas
from finance.livro_caixa import LivroCaixa
from finance.lista_compras import ListaCompras

router = APIRouter()


# ---------- senha (scrypt, stdlib: zero dependencia extra) ----------

def hash_senha(senha: str) -> str:
    sal = secrets.token_hex(16)
    h = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(sal), n=2**14, r=8, p=1)
    return f"scrypt${sal}${h.hex()}"


def verificar_senha(senha: str, guardado: str | None) -> bool:
    try:
        _alg, sal, hex_h = (guardado or "").split("$")
        h = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(sal), n=2**14, r=8, p=1)
        return secrets.compare_digest(h.hex(), hex_h)
    except Exception:  # noqa: BLE001
        return False


# ---------- helpers ----------

def _normalizar_zap(numero: str) -> str:
    d = "".join(ch for ch in (numero or "") if ch.isdigit())
    if not d.startswith("55"):
        d = "55" + d
    if len(d) == 12:                      # sem o nono digito -> insere
        d = d[:4] + "9" + d[4:]
    return "+" + d


def _papel_logado(request: Request, conta_id: int) -> str:
    """Papel do operador logado no portal. O login do portal e' por conta
    (titular = dono), entao por padrao 'dono'. Centraliza o gate de permissoes
    e ja' deixa pronto um futuro login por membro."""
    return request.session.get("papel", "dono")


def conta_logada(request: Request):
    # Memoizacao por requisicao + gating numa query so: alem dos campos da conta,
    # ja traz o acesso computado em conta[11..15]:
    #   [11]=tem_pj(modulo_pj)  [12]=acesso_pj  [13]=vende_produto  [14]=vende_servico
    #   [15]=tem_cesta   (indices 0..10 = os campos originais, inalterados)
    if hasattr(request.state, "_conta_cache"):
        return request.state._conta_cache
    cid = request.session.get("conta_id")
    if not cid:
        request.state._conta_cache = None
        return None
    pool = get_pool()
    with pool.connection() as c:
        r = c.execute(
            """select co.id, co.tipo, co.nome, co.email, co.plano, co.status,
                      co.vencimento, co.cidade, co.eh_fornecedor, co.fornecedor_slug,
                      co.eh_assinante_cesta,
                      p.tipo_conta, n.slug, co.vende_produto, co.vende_servico,
                      exists(select 1 from conta_modulos cm
                              where cm.conta_id = co.id and cm.modulo = 'pj' and cm.ativo),
                      exists(select 1 from assinaturas a
                              where a.cliente_id = co.id and a.status <> 'cancelada')
                 from contas co
                 left join planos p on p.codigo = co.plano
                 left join nichos n on n.id = co.nicho_id
                where co.id = %s""",
            (cid,),
        ).fetchone()
    if r is None:
        request.state._conta_cache = None
        return None
    _plano_tipo, _nslug = r[11], r[12]
    _vp_ovr, _vs_ovr, _mod_pj_ovr, _cesta_db = r[13], r[14], r[15], r[16]
    _modulo_pj = (_plano_tipo == "pj" and r[5] in ("trial", "ativa")) or bool(_mod_pj_ovr)
    _acesso = _modulo_pj or bool(r[8])
    from finance import nichos as _n
    _prod = _vp_ovr if _vp_ovr is not None else _n.vende_produto(_nslug)
    _serv = _vs_ovr if _vs_ovr is not None else _n.vende_servico(_nslug)
    if not _prod and not _serv:
        _prod = True
    _tem_cesta = bool(r[10]) or bool(_cesta_db)
    row = tuple(r[0:11]) + (_modulo_pj, _acesso,
                            bool(_prod) and _acesso, bool(_serv) and _acesso, _tem_cesta)
    request.state._conta_cache = row
    return row


def _planos():
    pool = get_pool()
    with pool.connection() as c:
        return c.execute(
            """select codigo, nome, tipo_conta, preco_base_centavos,
                      membros_inclusos, preco_assento_centavos
               from planos where ativo order by preco_base_centavos"""
        ).fetchall()


def _limite_membros(conta_row) -> tuple[int, int, bool]:
    """(ativos, inclusos_no_plano, pode_passar_do_limite).

    PF: teto rigido = membros_inclusos. PJ: pode passar (assento extra cobrado).
    """
    pool = get_pool()
    with pool.connection() as c:
        ativos = c.execute(
            "select count(*) from membros where conta_id=%s and ativo", (conta_row[0],)
        ).fetchone()[0]
        plano = c.execute(
            "select membros_inclusos, tipo_conta from planos where codigo=%s",
            (conta_row[4],),
        ).fetchone()
    inclusos = plano[0] if plano else 1
    pode_extra = (conta_row[1] == "pj")
    return ativos, inclusos, pode_extra


def brl(centavos: int) -> str:
    return f"R$ {centavos/100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _n2(v) -> str:
    """Numero em formato BR com 2 casas, SEM 'R$' (o template ja poe o R$).
    Ex.: 2020.5 -> '2.020,50'. Tolerante a None/erro."""
    try:
        return f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "0,00"


# ---------- paginas (templates embutidos: 1 arquivo so') ----------

_BASE = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ titulo }} - Zaq</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>
:root {
  color-scheme: dark;
  --bg:#0e0e0f; --card:#161617; --card-2:#1a1a1c; --borda:#2a2a2b;
  --txt:#ececec; --txt-mut:#a8a8a3;
  --verde:#1d9e75; --verde-hover:#22b485; --verde-claro:#5dcaa5;
}
body{margin:0;min-height:100vh;font-family:system-ui,-apple-system,sans-serif;
 background:var(--bg);color:var(--txt);display:flex;flex-direction:column;align-items:center}
.topo{width:100%;max-width:960px;display:flex;justify-content:space-between;
 align-items:center;padding:1.2rem 1rem;box-sizing:border-box}
.topo a{color:var(--verde-claro);text-decoration:none;margin-left:1rem;white-space:nowrap}
#menu-links{display:flex;align-items:center;flex-wrap:wrap;justify-content:flex-end}
.fin-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:1.2rem 0;align-items:stretch}
@media (max-width:600px){
  #menu-btn{display:inline-flex !important;order:-1}
  .topo .logo{margin-left:auto}
  #menu-links{display:none;position:absolute;top:100%;right:12px;left:12px;z-index:50;margin-top:4px;
    flex-direction:column;align-items:stretch;background:#141416;border:1px solid var(--borda);
    border-radius:12px;padding:6px;box-shadow:0 8px 24px rgba(0,0,0,.4)}
  #menu-links.aberto{display:flex}
  #menu-links a{margin-left:0;padding:.7rem .9rem;border-radius:8px;border-bottom:1px solid #1e1e20}
  #menu-links a:last-child{border-bottom:0}
  .topo{position:relative;padding:1.1rem 1rem 1.3rem}
}
.logo{font-weight:600;color:var(--txt);font-size:1.1rem}
.card{width:100%;max-width:430px;background:var(--card);border:1px solid var(--borda);
 border-radius:14px;padding:2rem;margin:1.5rem 1rem;box-sizing:border-box}
.card.larga{max-width:720px}
h1{font-size:1.35rem;font-weight:500;margin:0 0 1.2rem}
label{display:block;font-size:.85rem;color:var(--txt-mut);margin:.9rem 0 .3rem}
input,select{width:100%;padding:.65rem .8rem;border-radius:8px;border:1px solid #333;
 background:var(--bg);color:var(--txt);box-sizing:border-box;font-size:.95rem}
button{width:100%;margin-top:1.4rem;padding:.75rem;border:0;border-radius:8px;
 background:var(--verde);color:#fff;font-size:1rem;cursor:pointer}
button:hover{background:var(--verde-hover)}
.erro{background:#3a1d1d;border:1px solid #6e2b2b;color:#f0b8b8;border-radius:8px;
 padding:.6rem .8rem;font-size:.88rem;margin-bottom:.6rem}
.ok{background:#15301f;border:1px solid var(--verde);color:#9fe8c9;border-radius:8px;
 padding:.6rem .8rem;font-size:.88rem;margin-bottom:.6rem}
.nowrap{white-space:nowrap}
.mut{color:var(--txt-mut);font-size:.85rem}
.plano-banner{width:100%;max-width:720px;box-sizing:border-box;margin:1rem 1rem 0;
 display:flex;align-items:center;gap:.9rem;flex-wrap:wrap;
 border-radius:12px;padding:.8rem 1rem;font-size:.9rem;line-height:1.4}
.plano-banner .pb-txt{flex:1 1 260px;min-width:0}
.plano-banner .pb-btn{flex:0 0 auto;width:auto;margin:0;padding:.55rem 1.1rem;border-radius:8px;
 font-size:.9rem;font-weight:600;text-decoration:none;white-space:nowrap;color:#fff;background:var(--verde)}
.plano-banner .pb-btn:hover{background:var(--verde-hover)}
.pb-vencido{background:#3a1d1d;border:1px solid #6e2b2b;color:#f0c2c2}
.pb-avencer{background:#332a12;border:1px solid #6e5a22;color:#f0dca6}
.pb-avencer .pb-btn{background:#c99a2e}.pb-avencer .pb-btn:hover{background:#d9a832}
table{width:100%;border-collapse:collapse;margin-top:.8rem}
td,th{padding:.5rem .4rem;border-bottom:1px solid var(--borda);text-align:left;font-size:.92rem}
.tag{display:inline-block;padding:.1rem .55rem;border-radius:999px;font-size:.78rem;
 border:1px solid var(--verde);color:var(--verde-claro)}
.metric{background:var(--bg);border:1px solid var(--borda);border-radius:8px;padding:1rem}
.metric span{display:block;font-size:.8rem;color:var(--txt-mut);margin-bottom:.3rem}
.metric b{font-size:1.4rem;font-weight:500}
.barra{height:8px;background:var(--bg);border-radius:4px;overflow:hidden}
.barra-fill{height:8px;background:var(--verde);border-radius:4px}
.chip{border:1px solid var(--borda);padding:.25rem .6rem;border-radius:999px;font-size:.8rem;color:#ccc}
.membros{display:flex;flex-direction:column;gap:6px;margin-top:.6rem}
.membro-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:.7rem .8rem;background:var(--bg);border:1px solid var(--borda);border-radius:10px}
.membro-id{display:flex;align-items:center;gap:10px;flex:1 1 180px;min-width:0}
.avatar{width:34px;height:34px;border-radius:50%;background:#1d3a30;color:var(--verde-claro);display:flex;align-items:center;justify-content:center;font-weight:600;flex-shrink:0}
.membro-nome{font-size:.95rem}
.membro-papel{font-size:.78rem;color:var(--txt-mut)}
.membro-contato{flex:1 1 200px;font-size:.84rem;color:#c5c5c0;min-width:0}
.membro-contato .conv code{background:var(--card);border:1px solid var(--borda);border-radius:6px;padding:.1rem .4rem;font-size:.82rem;color:var(--verde-claro)}
.membro-acoes{display:flex;gap:6px;flex:0 0 auto}
.membro-acoes form{margin:0}
.membro-acoes button{margin:0;padding:.35rem .7rem;font-size:.78rem;width:auto}
.btn-conv{background:#1d6e9e}.btn-conv:hover{background:#2480b5}
.btn-off{background:#6e2b2b}.btn-off:hover{background:#8a3636}
.btn-on{background:var(--verde)}

.dep{border:1px solid var(--borda);border-radius:8px;margin-bottom:8px;overflow:hidden}
.dep-cab{display:flex;justify-content:space-between;align-items:center;padding:.7rem .9rem;background:var(--card);cursor:pointer;font-size:.92rem}
.dep-cab:hover{background:#1c1c1d}
.seta{color:var(--verde-claro);margin-right:.3rem}
.dep-corpo{display:none;padding:.7rem .9rem;flex-wrap:wrap;gap:8px}
.dep.aberto .dep-corpo{display:flex}
.subdia{border-top:1px solid #232324}
.subdia-cab{display:flex;justify-content:space-between;align-items:center;padding:.55rem .2rem;cursor:pointer;font-size:.88rem}
.subdia-cab:hover{color:#fff}
.seta2{color:var(--verde-claro);margin-right:.3rem;font-size:.8rem}
.subdia-corpo{display:none;flex-wrap:wrap;gap:8px;padding:.2rem 0 .7rem}
.subdia.aberto .subdia-corpo{display:flex}
.conv-links{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.lk-tg,.lk-copy,.lk-wpp{padding:.35rem .6rem;border-radius:6px;font-size:.75rem;border:1px solid #243049;text-decoration:none;display:inline-block;line-height:1.2}
.lk-tg{background:#2AABEE;color:#fff;border-color:#2AABEE}
.lk-copy{background:#1a2233;color:#e7ecf3;cursor:pointer;border-color:var(--borda)}
.lk-wpp{background:#25D366;color:#fff;border-color:#25D366}
.conv-invite{margin:.3rem 0;font-size:.82rem}
.conv-links{display:flex;gap:4px;flex-wrap:wrap;margin-top:.3rem}
/* Trilho de abas/chips — mesmo padrão do grupo "Ver lançamentos de" (pessoal/
   empresa): fundo --card, cantos 9px, itens 6px. align-items:center e o margin:0
   do .aba evitam a "barriga" que o margin-top do button base causava. */
.abas{display:inline-flex;align-items:center;flex-wrap:wrap;gap:2px;background:var(--card);padding:3px;border-radius:9px;border:1px solid var(--borda);margin:.6rem 0 .9rem}
.aba{width:auto;margin:0;display:inline-flex;align-items:center;line-height:1;background:transparent;color:#b4b2a9;border:none;padding:.35rem .8rem;border-radius:6px;font-size:.8rem;cursor:pointer;transition:background .18s,color .18s}
.aba:hover{color:var(--txt)}
.aba.on,.aba.ativa{background:var(--verde);color:#fff;font-weight:600}
.dica-toque{font-size:.78rem;color:#7a7a78;margin:0 0 .6rem}
.dephead{font-size:.72rem;font-weight:600;color:#cfcfca;margin:.6rem 0 .3rem .2rem}
.litem{display:flex;align-items:center;gap:.6rem;padding:.7rem .5rem .7rem .6rem;margin-bottom:.4rem;background:var(--card-2);border-radius:10px;transition:transform .15s,background .2s,opacity .35s,max-height .35s;position:relative;overflow:hidden}
.litem .toque{display:flex;align-items:center;gap:.7rem;flex:1;cursor:pointer}
.litem:active{transform:scale(.985)}
.litem .bol{width:26px;height:26px;border:2px solid var(--verde-claro);border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;color:#fff;font-size:15px;transition:background .2s,border-color .2s}
.litem .nome{flex:1;color:var(--txt);font-size:15px;transition:color .2s;word-break:break-word}
.litem .rem{width:34px;height:34px;flex-shrink:0;display:flex;align-items:center;justify-content:center;color:#7a4a4a;font-size:1.1rem;border-radius:8px;cursor:pointer;border:0;background:transparent}
.litem .rem:hover{background:rgba(180,60,60,.15);color:#c66}
.litem.done{background:#15241d}
.litem.done .bol{background:var(--verde);border-color:var(--verde)}
.litem.done .nome{color:#7a8a82;text-decoration:line-through}
.litem.flash-v::after{content:'';position:absolute;inset:0;background:var(--verde);opacity:.35;animation:cfade .5s ease-out forwards;pointer-events:none}
.litem.flash-r::after{content:'';position:absolute;inset:0;background:#a33;opacity:.3;animation:cfade .4s ease-out forwards;pointer-events:none}
@keyframes cfade{to{opacity:0}}
.litem.saindo{opacity:0;transform:translateX(40px);max-height:0;padding-top:0;padding-bottom:0;margin-bottom:0}
.cart-head{margin:1rem 0 .3rem;font-size:.88rem;color:var(--verde-claro);cursor:pointer;user-select:none;font-weight:500}
.btn-finalizar{width:100%;margin-top:.8rem;background:var(--verde);color:#fff;border:none;padding:.7rem;border-radius:10px;font-size:15px;font-weight:500;cursor:pointer;transition:background .2s}
.btn-finalizar:hover{background:var(--verde-hover)}
.btn-finalizar:active{transform:scale(.99)}
.hist-dia{border-bottom:1px solid var(--borda)}
.hist-head{display:flex;align-items:center;justify-content:space-between;padding:.6rem .2rem;cursor:pointer;color:var(--txt);font-size:14px;user-select:none}
.hist-head:hover{background:var(--card-2);border-radius:8px}
.hist-itens{padding:.2rem .2rem .6rem 1rem;font-size:13px;color:var(--txt-mut);line-height:1.7}
.wa-suporte{position:fixed;bottom:20px;right:20px;width:56px;height:56px;background:#25d366;border-radius:50%;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 12px rgba(0,0,0,.35);z-index:9999;transition:transform .2s,box-shadow .2s;text-decoration:none}
.wa-suporte:hover{transform:scale(1.08);box-shadow:0 6px 18px rgba(37,211,102,.45)}
.wa-suporte:active{transform:scale(.96)}
.wa-tooltip{position:absolute;right:68px;background:var(--card-2);color:var(--txt);padding:.45rem .8rem;border-radius:6px;font-size:.85rem;white-space:nowrap;opacity:0;pointer-events:none;transition:opacity .2s;border:1px solid var(--borda)}
.wa-suporte:hover .wa-tooltip{opacity:1}
@media (max-width:600px){.wa-suporte{bottom:16px;right:16px;width:52px;height:52px}.wa-tooltip{display:none}.fin-cards{grid-template-columns:1fr}.fin-cards>div:first-child{display:grid !important;grid-template-columns:1fr 1fr;gap:.6rem}.fin-cards>div:first-child>div:last-child{margin-top:0 !important;padding-top:0 !important;border-top:0 !important;border-left:1px solid #1e1e20;padding-left:.6rem !important}}

#navprog{position:fixed;top:0;left:0;height:3px;width:0;background:var(--verde-claro);z-index:100;transition:width .35s ease;opacity:0}
#navprog.run{width:92%;opacity:1;transition:width 9s cubic-bezier(.1,.7,.2,1)}
.nav-ic{width:20px;height:20px;flex-shrink:0;stroke:currentColor;fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.side,.btmnav,.mais-sheet,.mais-bg{display:none}
.side-grp{font-size:.66rem;text-transform:uppercase;letter-spacing:.5px;color:#6a6a66;padding:.7rem .6rem .25rem}
.nav-i{display:flex;align-items:center;gap:.65rem;padding:.55rem .6rem;border-radius:8px;color:var(--txt-mut);text-decoration:none;font-size:.9rem;margin:0}
.nav-i:hover{background:var(--card-2);color:var(--txt)}
.nav-i.on{background:rgba(29,158,117,.16);color:var(--verde-claro)}
.topo-mob{display:none}
@media(min-width:768px){
  body{padding-left:216px}
  .topo-mob{display:none !important}
  .side{display:flex;flex-direction:column;position:fixed;left:0;top:0;bottom:0;width:216px;background:var(--card);border-right:1px solid var(--borda);padding:1rem .7rem;box-sizing:border-box;overflow-y:auto;z-index:40}
  .side-logo{font-weight:600;color:var(--txt);font-size:1.1rem;padding:.2rem .6rem 1rem;display:flex;align-items:center;gap:.5rem}
}
@media(max-width:767px){
  body{padding-bottom:66px}
  .topo-mob{display:flex}
  .wa-suporte{bottom:80px !important}
  .btmnav{display:flex;position:fixed;left:0;right:0;bottom:0;height:60px;background:var(--card);border-top:1px solid var(--borda);z-index:60}
  .btmnav a,.btmnav button{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;color:var(--txt-mut);text-decoration:none;background:none;border:0;font-size:.6rem;cursor:pointer;margin:0;padding:0;width:auto}
  .btmnav .on{color:var(--verde-claro)}
  .btmnav .nav-ic{width:22px;height:22px}
  .mais-bg{display:block;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:70;opacity:0;pointer-events:none;transition:opacity .25s}
  .mais-bg.open{opacity:1;pointer-events:auto}
  .mais-sheet{display:block;position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--borda);border-radius:16px 16px 0 0;padding:.4rem 1rem 1.4rem;z-index:71;transform:translateY(100%);transition:transform .25s;max-height:80vh;overflow-y:auto}
  .mais-sheet.open{transform:translateY(0)}
  .mais-grab{width:34px;height:4px;background:var(--borda);border-radius:2px;margin:.4rem auto .5rem}
}
</style>{% if embed %}<style>body{padding-left:0 !important}.side,.topo-mob,.wa-suporte{display:none !important}</style>{% endif %}</head><body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs><symbol id="ic-caixa" viewBox="0 0 24 24"><path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6"/></symbol><symbol id="ic-produtos" viewBox="0 0 24 24"><path d="M3 8l9-5 9 5v8l-9 5-9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/></symbol><symbol id="ic-clientes" viewBox="0 0 24 24"><circle cx="9" cy="8" r="3"/><path d="M3.5 20c0-3.3 2.5-5.5 5.5-5.5s5.5 2.2 5.5 5.5"/><path d="M16 6a3 3 0 010 6"/></symbol><symbol id="ic-financeiro" viewBox="0 0 24 24"><path d="M4 4v16h16"/><path d="M8 15l3-4 3 2 4-6"/></symbol><symbol id="ic-mais" viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16"/></symbol><symbol id="ic-abastecimento" viewBox="0 0 24 24"><path d="M3 6h11v9H3zM14 9h4l3 3v3h-7z"/><circle cx="7" cy="18" r="1.6"/><circle cx="17" cy="18" r="1.6"/></symbol><symbol id="ic-empresa" viewBox="0 0 24 24"><path d="M4 9l1.2-4h13.6L20 9M5 9v10h14V9M4 9h16M10 19v-5h4v5"/></symbol><symbol id="ic-fornecedor" viewBox="0 0 24 24"><path d="M12 21v-8M12 13c0-3 2-5.5 5.5-5.5C17.5 11 15.5 13 12 13zM12 15c0-2.5-1.6-4.5-4.5-4.5C7.5 13 9 15 12 15z"/></symbol><symbol id="ic-compras" viewBox="0 0 24 24"><circle cx="9" cy="20" r="1.5"/><circle cx="17" cy="20" r="1.5"/><path d="M2 4h2.2l2.3 11h11l1.8-8H6"/></symbol><symbol id="ic-cesta" viewBox="0 0 24 24"><path d="M5 9h14l-1.4 10H6.4zM9 9l1.2-5M15 9l-1.2-5"/></symbol><symbol id="ic-painel" viewBox="0 0 24 24"><path d="M4 4h7v7H4zM13 4h7v4h-7zM13 11h7v9h-7zM4 14h7v6H4z"/></symbol><symbol id="ic-sair" viewBox="0 0 24 24"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9"/></symbol><symbol id="ic-prospeccao" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></symbol></defs></svg>
<div id="navprog"></div>
{% macro navi(sec, href, ic, label) -%}
<a href="{{ href }}" class="nav-i{% if secao_ativa==sec and sec %} on{% endif %}" onclick="navTap(this)"><svg class="nav-ic"><use href="#ic-{{ ic }}"/></svg><span>{{ label }}</span></a>
{%- endmacro %}
{% macro tabi(sec, href, ic, label) -%}
<a href="{{ href }}" class="{% if secao_ativa==sec and sec %}on{% endif %}" onclick="navTap(this)"><svg class="nav-ic"><use href="#ic-{{ ic }}"/></svg><span>{{ label }}</span></a>
{%- endmacro %}
{% if logado %}
{% set _tem_app = conta and conta[4] and conta[4] != 'zaq_cesta' %}
{% set _tem_cesta = (conta and conta[10]) or tem_cesta %}
{% set _forn = conta and conta[8] %}
<div class="topo topo-mob"><span class="logo" style="display:inline-flex;align-items:center;gap:7px"><svg width="20" height="20" viewBox="0 0 64 64" fill="none"><path d="M16 18 H44 L18 46 H46" stroke="#3ee0a6" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M47 10 L49 16 L55 18 L49 20 L47 26 L45 20 L39 18 L45 16 Z" fill="#3ee0a6"/></svg>zaq</span></div>
<nav class="side">
  <div class="side-logo"><span class="logo" style="display:inline-flex;align-items:center;gap:7px"><svg width="20" height="20" viewBox="0 0 64 64" fill="none"><path d="M16 18 H44 L18 46 H46" stroke="#3ee0a6" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M47 10 L49 16 L55 18 L49 20 L47 26 L45 20 L39 18 L45 16 Z" fill="#3ee0a6"/></svg>zaq</span></div>
  {% set _dono = (papel == 'dono') %}
  {% if _dono and (vende_produto or _tem_app) %}<div class="side-grp">Principal</div>{% endif %}
  {% if _dono and vende_produto %}{{ navi('caixa','/painel/pdv','caixa','Caixa') }}{{ navi('produtos','/painel/produtos','produtos','Produtos') }}{{ navi('clientes','/painel/clientes','clientes','Clientes') }}{% endif %}
  {% if _dono and _tem_app %}{{ navi('financeiro','/painel/financeiro','financeiro','Financeiro') }}{% endif %}
  {% if caps.vendas or (tem_pj and caps.financeiro) or caps.gerir or (_dono and (vende_produto or _forn)) %}<div class="side-grp">{{ 'Loja' if _dono else 'Minha área' }}</div>{% endif %}
  {% if _dono and vende_produto %}{{ navi('abastecimento','/painel/produtos/abastecimento','abastecimento','Abastecimento') }}{% endif %}
  {% if vende_servico and caps.vendas %}{{ navi('servicos','/painel/servicos','financeiro','Serviços') }}{% endif %}
  {% if tem_pj and caps.vendas %}{{ navi('prospeccao','/painel/prospeccao','prospeccao','Prospecção') }}{% endif %}
  {% if tem_pj and caps.financeiro %}{{ navi('empresa','/painel/empresa','empresa','Empresa') }}{% endif %}
  {% if caps.gerir %}{{ navi('equipe','/painel/equipe','clientes','Equipe') }}{% endif %}
  {% if _dono and _forn %}{{ navi('fornecedor','/painel/fornecedor','fornecedor','Fornecedor') }}{% endif %}
  {% if _dono and (_tem_app or _tem_cesta) %}<div class="side-grp">Pessoal</div>{% endif %}
  {% if _dono and _tem_app %}{{ navi('painel','/painel','painel','Painel') }}{{ navi('compras','/painel/compras','compras','Lista de compras') }}{% endif %}
  {% if _dono and _tem_cesta %}{{ navi('assinaturas','/painel/assinaturas','cesta','Assinaturas') }}{{ navi('pedidos','/painel/meus-pedidos','compras','Meus pedidos') }}{% endif %}
  {% if n_contextos > 1 %}{{ navi('trocar','/trocar','fornecedor','Trocar empresa') }}{% endif %}
  <div style="flex:1"></div>
  {{ navi('','/sair','sair','Sair') }}
</nav>
<nav class="btmnav">
  {% if _dono and vende_produto %}{{ tabi('caixa','/painel/pdv','caixa','Caixa') }}{{ tabi('produtos','/painel/produtos','produtos','Produtos') }}{{ tabi('clientes','/painel/clientes','clientes','Clientes') }}{% endif %}
  {% if _dono and _tem_app %}{{ tabi('financeiro','/painel/financeiro','financeiro','Financeiro') }}{% endif %}
  {% if _dono and not vende_produto and _tem_app %}{{ tabi('painel','/painel','painel','Painel') }}{% endif %}
  {% if not _dono and vende_servico and caps.vendas %}{{ tabi('servicos','/painel/servicos','financeiro','Serviços') }}{% endif %}
  {% if not _dono and tem_pj and caps.vendas %}{{ tabi('prospeccao','/painel/prospeccao','prospeccao','Prospecção') }}{% endif %}
  {% if not _dono and tem_pj and caps.financeiro %}{{ tabi('empresa','/painel/empresa','empresa','Empresa') }}{% endif %}
  <button type="button" onclick="maisToggle(true)"><svg class="nav-ic"><use href="#ic-mais"/></svg><span>Mais</span></button>
</nav>
<div class="mais-bg" onclick="maisToggle(false)"></div>
<div class="mais-sheet" id="mais-sheet">
  <div class="mais-grab"></div>
  {% if caps.vendas or (tem_pj and caps.financeiro) or caps.gerir or (_dono and (vende_produto or _forn)) %}<div class="side-grp">{{ 'Loja' if _dono else 'Minha área' }}</div>{% endif %}
  {% if _dono and vende_produto %}{{ navi('abastecimento','/painel/produtos/abastecimento','abastecimento','Abastecimento') }}{% endif %}
  {% if vende_servico and caps.vendas %}{{ navi('servicos','/painel/servicos','financeiro','Serviços') }}{% endif %}
  {% if tem_pj and caps.vendas %}{{ navi('prospeccao','/painel/prospeccao','prospeccao','Prospecção') }}{% endif %}
  {% if tem_pj and caps.financeiro %}{{ navi('empresa','/painel/empresa','empresa','Empresa') }}{% endif %}
  {% if caps.gerir %}{{ navi('equipe','/painel/equipe','clientes','Equipe') }}{% endif %}
  {% if _dono and _forn %}{{ navi('fornecedor','/painel/fornecedor','fornecedor','Fornecedor') }}{% endif %}
  {% if _dono and (_tem_app or _tem_cesta) %}<div class="side-grp">Pessoal</div>{% endif %}
  {% if _dono and _tem_app %}{{ navi('painel','/painel','painel','Painel') }}{{ navi('compras','/painel/compras','compras','Lista de compras') }}{% endif %}
  {% if _dono and _tem_cesta %}{{ navi('assinaturas','/painel/assinaturas','cesta','Assinaturas') }}{{ navi('pedidos','/painel/meus-pedidos','compras','Meus pedidos') }}{% endif %}
  {% if n_contextos > 1 %}{{ navi('trocar','/trocar','fornecedor','Trocar empresa') }}{% endif %}
  {{ navi('','/sair','sair','Sair') }}
</div>
<script>
function navTap(el){var p=el.closest('nav');if(p){var A=p.querySelectorAll('a');for(var i=0;i<A.length;i++)A[i].classList.remove('on');}el.classList.add('on');var b=document.getElementById('navprog');if(b)b.classList.add('run');}
function maisToggle(v){var sh=document.getElementById('mais-sheet'),bg=document.querySelector('.mais-bg');if(sh)sh.classList.toggle('open',v);if(bg)bg.classList.toggle('open',v);}
</script>
{% else %}
<div class="topo"><span class="logo" style="display:inline-flex;align-items:center;gap:7px"><svg width="20" height="20" viewBox="0 0 64 64" fill="none"><path d="M16 18 H44 L18 46 H46" stroke="#3ee0a6" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M47 10 L49 16 L55 18 L49 20 L47 26 L45 20 L39 18 L45 16 Z" fill="#3ee0a6"/></svg>zaq</span><span id="menu-links"><a href="/login">Entrar</a><a href="/cadastro">Criar conta</a></span></div>
{% endif %}
{% if logado and plano_aviso %}
<div class="plano-banner {% if plano_aviso.nivel == 'vencido' %}pb-vencido{% else %}pb-avencer{% endif %}">
  <div class="pb-txt">
    {% if plano_aviso.nivel == 'vencido' %}
      {% if plano_aviso.status == 'suspensa' %}<b>Seu acesso está suspenso.</b>
      {% elif plano_aviso.status == 'cancelada' %}<b>Seu plano está cancelado.</b>
      {% elif plano_aviso.venc_fmt %}<b>Seu plano venceu em {{ plano_aviso.venc_fmt }}.</b>
      {% else %}<b>Seu plano está vencido.</b>{% endif %}
      {% if plano_aviso.cortado %} As mensagens pelo WhatsApp (SAC) estão pausadas. Regularize o pagamento pra voltar a usar — e pra não perder o acesso ao painel também.
      {% else %} Enquanto o beta grátis está ligado você continua usando normalmente (SAC e painel) — mas assim que o beta acabar, sem pagamento o acesso é cortado. Pague pra já deixar em dia.{% endif %}
    {% else %}
      <b>Seu plano vence em {{ plano_aviso.dias }} dia{% if plano_aviso.dias != 1 %}s{% endif %}{% if plano_aviso.venc_fmt %} ({{ plano_aviso.venc_fmt }}){% endif %}.</b>
      Renove pra não ficar sem o SAC nem o painel.
    {% endif %}
  </div>
  <a href="/painel/pagar" class="pb-btn">Pagar agora</a>
</div>
{% endif %}
{% block conteudo %}{% endblock %}
<a href="https://wa.me/5586981885930?text={% if conta %}Oi%20Thompson%21%20Estou%20no%20Zaq%20%28conta%20%23{{ conta[0] }}%29%20e%20preciso%20de%20ajuda%20com%3A%20{% else %}Oi%20Thompson%21%20Estou%20no%20Zaq%20e%20preciso%20de%20ajuda%20com%3A%20{% endif %}"
   target="_blank" rel="noopener" class="wa-suporte" aria-label="Falar com Thompson no WhatsApp">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="28" height="28" fill="#fff" aria-hidden="true">
    <path d="M.057 24l1.687-6.163a11.867 11.867 0 01-1.587-5.946C.16 5.335 5.495 0 12.05 0a11.817 11.817 0 018.413 3.488 11.824 11.824 0 013.48 8.414c-.003 6.557-5.338 11.892-11.893 11.892a11.9 11.9 0 01-5.688-1.448L.057 24zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884a9.86 9.86 0 001.51 5.26l-.999 3.648 3.978-1.607zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51l-.57-.01c-.198 0-.52.074-.792.372s-1.04 1.016-1.04 2.479 1.065 2.876 1.213 3.074c.149.198 2.095 3.2 5.076 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413z"/>
  </svg>
  <span class="wa-tooltip">Falar com Thompson</span>
</a>
</body></html>"""

_CADASTRO = """{% extends "base" %}{% block conteudo %}
<div class="card"><h1>Criar sua conta</h1>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
<form method="post" action="/cadastro">
<label style="margin-bottom:.3rem;display:block">Escolha seu plano</label>
<div class="mut" style="font-size:.8rem;margin-bottom:.6rem">Tudo grátis durante o beta — experimente sem compromisso.</div>
<div style="display:grid;gap:.5rem;margin-bottom:1rem">
{% for p in planos %}{% if p[0] != 'zaq_cesta' %}
<label style="display:block;background:{% if p[2]=='pj' %}#14251c{% else %}var(--card){% endif %};border:{% if p[2]=='pj' %}2px solid var(--verde){% else %}1px solid #232325{% endif %};border-radius:10px;padding:.7rem .9rem;cursor:pointer">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <span style="color:#f4f4f4;font-weight:600;font-size:.95rem"><input type="radio" name="plano" value="{{ p[0] }}" {% if p[2]=='pj' %}checked{% endif %} style="width:auto;margin-right:.5rem">{% if p[2]=='pj' %}🏢 {% endif %}{{ p[1] }}</span>
    <span style="background:#15301f;color:var(--verde-claro);font-size:.62rem;padding:.1rem .5rem;border-radius:9px">{% if beta_gratis %}Grátis (beta){% else %}{{ brl(p[3]) }}/mês{% endif %}</span>
  </div>
  <div class="mut" style="font-size:.74rem;margin-top:.35rem;margin-left:1.4rem">
    {% if p[2]=='pj' %}Contas a pagar/receber, funcionários e folha, DRE e relatório pro contador. Inclui {{ p[4] }} membros.{% elif p[4]>1 %}As contas da casa, até {{ p[4] }} pessoas no mesmo caixa.{% else %}Suas contas pessoais — cupom por foto, gastos no WhatsApp.{% endif %}
  </div>
</label>
{% endif %}{% endfor %}
</div>

<div style="background:var(--card);border:1px dashed #3a3a3d;border-radius:10px;padding:.7rem .9rem;margin-bottom:1rem">
  <div style="color:#b4b2a9;font-size:.82rem;font-weight:600;margin-bottom:.4rem">Quer vender também? (nossa equipe ativa e te ajuda)</div>
  <label style="display:flex;align-items:center;gap:.5rem;cursor:pointer">
    <input type="checkbox" name="interesse" value="fornecedor" style="width:auto">
    <span style="color:var(--txt);font-size:.82rem">👨‍🌾 Tenho interesse no <b>módulo Fornecedor</b> (loja / marketplace)</span>
  </label>
  <div class="mut" style="font-size:.7rem;margin-top:.2rem;margin-left:1.6rem">Ao marcar, avisamos seu interesse pra equipe. Você não é cobrado por isso.</div>
</div>
<label>Seu nome</label><input name="nome" value="{{ pre_nome or '' }}" required maxlength="80">
<label>E-mail</label><input name="email" type="email" value="{{ pre_email or '' }}" required maxlength="120">
<label>Senha</label><input name="senha" type="password" required minlength="8" maxlength="72">
<label>CPF ou CNPJ <span class="mut">(opcional agora)</span></label><input name="documento" maxlength="20">
<label>Seu WhatsApp (com DDD)</label><input name="whatsapp" value="{{ pre_whatsapp or '' }}" required placeholder="86 98888-7777" maxlength="20">
<label>Seu CEP <span class="mut">(pra comparar preços perto de você)</span></label>
<input name="cep" required placeholder="64000-000" maxlength="9" inputmode="numeric">
<button>Começar meu teste grátis de 7 dias</button>
<p class="mut">Sem cartão agora. Coletamos só o necessário (LGPD).</p>
</form></div>{% endblock %}"""

_LOGIN = """{% extends "base" %}{% block conteudo %}
<div class="card"><h1>Entrar</h1>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
<form method="post" action="/login">
<label>E-mail</label><input name="email" type="email" required>
<label>Senha</label><input name="senha" type="password" required>
<button>Entrar</button></form>
<p style="text-align:center;margin-top:.8rem">
  <a href="/esqueci-senha" style="color:var(--verde-claro);font-size:.88rem;text-decoration:none">Esqueci minha senha</a>
</p>
</div>{% endblock %}"""

_BEMVINDO = """{% extends "base" %}{% block conteudo %}
<div style="max-width:420px;margin:2rem auto;text-align:center">
  <div style="width:56px;height:56px;border-radius:50%;background:#11402e;display:flex;align-items:center;justify-content:center;margin:0 auto 1rem;font-size:28px">✅</div>
  <h1 style="margin:0 0 .4rem">Bem-vindo, {{ nome_pessoa }}!</h1>
  <p class="mut" style="margin:0 0 1.5rem">Sua conta está pronta. Falta só conectar seu Telegram pra começar.</p>
  {% if ja_conectado %}
  <div class="card"><p>Seu Telegram já está conectado! 🎉</p>
  <a class="btn" href="/painel">Ir pro painel →</a></div>
  {% else %}
  <div class="card" style="text-align:left">
    <p class="mut" style="margin:0 0 .85rem">Conecte seu Telegram pra conversar com seu assistente — registrar gastos, ver saldo, montar a lista de compras.</p>
    <a href="https://t.me/{{ bot }}?start={{ codigo|urlencode }}" target="_blank" rel="noopener"
       style="display:flex;align-items:center;justify-content:center;gap:8px;padding:.8rem;border-radius:8px;text-decoration:none;background:#2AABEE;color:#fff;font-weight:600;margin-bottom:8px">
       📨 Conectar meu Telegram</a>
    {% if whatsapp_bot_num %}
    <a href="https://wa.me/{{ whatsapp_bot_num }}?text={{ codigo|urlencode }}" target="_blank" rel="noopener"
       style="display:flex;align-items:center;justify-content:center;gap:8px;padding:.8rem;border-radius:8px;text-decoration:none;background:#25D366;color:#fff;font-weight:600">
       🟢 Conectar meu WhatsApp</a>
    {% else %}
    <span title="WhatsApp não configurado"
       style="display:flex;align-items:center;justify-content:center;gap:8px;padding:.8rem;border-radius:8px;background:#11201a;color:#5a6b62;cursor:not-allowed">
       🟢 WhatsApp (não disponível)</span>
    {% endif %}
    <p class="mut" style="font-size:.8rem;margin:.85rem 0 0">Use seu código <code>{{ codigo }}</code> em qualquer canal (Telegram ou WhatsApp) — é só enviar.</p>
  </div>
  <a href="/painel" class="mut" style="font-size:.85rem;display:inline-block;margin-top:1rem">Pular e ir pro painel →</a>
  {% endif %}
</div>
{% endblock %}"""

_PAINEL = """{% extends "base" %}{% block conteudo %}
<style>
.dash-eyebrow{font-size:.72rem;text-transform:uppercase;letter-spacing:.09em;color:var(--txt-mut);font-weight:650;margin:0 0 12px}
.dash-div{height:1px;background:var(--borda);margin:18px 0}
.dash-kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}
.kpi{background:var(--bg);border:1px solid var(--borda);border-radius:12px;padding:13px 14px;display:flex;flex-direction:column;gap:5px;min-width:0}
.kpi .rot{font-size:.73rem;color:var(--txt-mut);font-weight:600}
.kpi .val{font-size:1.22rem;font-weight:680;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.kpi .val.pos{color:var(--verde-claro)}
.kpi .val.neg{color:#e07a6b}
.kpi .peq{font-size:.72rem;color:var(--txt-mut)}
.kpi .chip{display:inline-flex;align-items:center;gap:4px;font-size:.7rem;font-weight:650;background:#3a2a12;color:#fab219;border-radius:999px;padding:.12rem .5rem;width:fit-content}
.dash-grid{display:grid;grid-template-columns:1fr 1fr;gap:26px}
.dash-grid.solo{grid-template-columns:1fr}
.dash-grid>div{min-width:0}
.sub-h{font-size:.82rem;font-weight:650;margin:0 0 14px;color:var(--txt)}
.sub-h span{color:var(--txt-mut);font-weight:500}
.rd-row{display:flex;flex-direction:column;gap:5px;margin-bottom:13px}
.rd-top{display:flex;justify-content:space-between;align-items:baseline;font-size:.85rem}
.rd-top .rd-nome{color:var(--txt-mut)}
.rd-top .rd-val{font-weight:680;font-variant-numeric:tabular-nums}
.rd-bar{height:9px;border-radius:5px;background:var(--bg);overflow:hidden}
.rd-bar>span{display:block;height:100%;border-radius:5px}
.rd-res{display:flex;justify-content:space-between;align-items:baseline;padding-top:11px;border-top:1px solid var(--borda);margin-top:2px}
.rd-res .rl{font-size:.78rem;color:var(--txt-mut)}
.rd-res .rv{font-size:1.1rem;font-weight:700;font-variant-numeric:tabular-nums}
.rd-res .rv.pos{color:var(--verde-claro)}
.rd-res .rv.neg{color:#e07a6b}
.cats{margin-top:15px;display:grid;grid-template-columns:1fr 1fr;gap:16px}
.cats h4{margin:0 0 7px;font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--txt-mut)}
.cats .ci{display:flex;justify-content:space-between;gap:8px;font-size:.81rem;padding:3px 0}
.cats .ci .cn{color:var(--txt);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cats .ci .cv{color:var(--txt-mut);font-variant-numeric:tabular-nums;flex:none}
.swatch{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px;vertical-align:middle}
.funil{display:flex;gap:20px;align-items:center}
.donut{width:150px;height:150px;flex:none;border-radius:50%;position:relative}
.donut::after{content:"";position:absolute;inset:26px;border-radius:50%;background:var(--card);display:block}
.donut .mid{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}
.donut .mid .n{font-size:1.45rem;font-weight:720;line-height:1}
.donut .mid .l{font-size:.62rem;color:var(--txt-mut);text-transform:uppercase;letter-spacing:.06em;margin-top:2px}
.legenda{display:flex;flex-direction:column;gap:7px;flex:1;min-width:0}
.leg-row{display:flex;align-items:center;gap:9px;font-size:.83rem}
.leg-row .dot{width:10px;height:10px;border-radius:3px;flex:none}
.leg-row .leg-nome{flex:1;min-width:0;color:var(--txt);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.leg-row .leg-n{font-variant-numeric:tabular-nums;color:var(--txt-mut);font-weight:600}
.resumo{display:flex;gap:20px;flex-wrap:wrap;margin-top:14px;padding-top:14px;border-top:1px solid var(--borda)}
.resumo .r{display:flex;flex-direction:column;gap:2px}
.resumo .r .rv{font-size:1.02rem;font-weight:680;font-variant-numeric:tabular-nums}
.resumo .r .rv.win{color:var(--verde-claro)}
.resumo .r .rl{font-size:.72rem;color:var(--txt-mut)}
.funil-vazio{display:flex;flex-direction:column;gap:8px;align-items:flex-start;color:var(--txt-mut);font-size:.88rem}
.fluxo-head{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:6px}
.fluxo-head .fv{font-size:.8rem;color:var(--txt-mut)}
.fluxo-head .fv b{color:var(--verde-claro);font-variant-numeric:tabular-nums}
.fluxo-svg{width:100%;height:118px;display:block}
.xlabels{display:flex;justify-content:space-between;font-size:.7rem;color:var(--txt-mut);margin-top:4px}
.acc{padding:0;overflow:hidden}
.acc>summary{padding:16px 20px;cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center;font-size:1.02rem;font-weight:620;color:var(--txt)}
.acc>summary::-webkit-details-marker{display:none}
.acc>summary .sum-meta{font-size:.8rem;color:var(--txt-mut);font-weight:500;margin-left:.4rem}
.acc>summary .chev{color:var(--txt-mut);transition:transform .2s;font-size:.85rem}
.acc[open]>summary .chev{transform:rotate(180deg)}
.acc-body{padding:0 20px 18px}
.acc-body .card{border:0;background:transparent;padding:0;margin:0 0 1.2rem;max-width:none}
@media(max-width:700px){
.dash-kpis{grid-template-columns:repeat(2,1fr)}
.dash-grid{grid-template-columns:1fr;gap:22px}
.cats{grid-template-columns:1fr}
}
</style>
<div class="card larga">
  <h1 style="margin:0">Olá, {{ conta[2] }}! <span class="tag">{{ conta[5] }}</span></h1>
  <p class="mut" style="margin-top:.6rem">Plano: <b>{{ conta[4] or '-' }}</b>{% if conta[6] %} · válido até <b>{{ conta[6].strftime('%d/%m/%Y') }}</b>{% endif %} · tipo: <b>{{ conta[1]|upper }}</b> <a href="/painel/ativar-app" id="trocar-plano" style="color:var(--verde-claro);text-decoration:none;margin-left:.3rem">trocar plano &gt;</a></p>
  {% if conta[5] == 'trial' %}<form method="post" action="/assinar" style="margin:0"><button style="width:auto;margin:.6rem 0 0;padding:.55rem 1.1rem">💳 Assinar plano</button></form>{% endif %}
</div>

{% if dash %}
<div class="card larga">
  <p class="dash-eyebrow">Visão do negócio · {{ dash.dre.mes }}/{{ dash.dre.ano }}</p>
  <div class="dash-kpis">
    <div class="kpi"><span class="rot">A receber · 30d</span><span class="val pos">{{ dash.res.a_receber_centavos|brl }}</span><span class="peq">{{ dash.res.n_receber }} título(s) aberto(s)</span></div>
    <div class="kpi"><span class="rot">A pagar · 30d</span><span class="val">{{ dash.res.a_pagar_centavos|brl }}</span>{% if dash.res.atrasados_pagar_centavos %}<span class="chip">⚠ {{ dash.res.atrasados_pagar_centavos|brl }} atrasado</span>{% else %}<span class="peq">{{ dash.res.n_pagar }} título(s)</span>{% endif %}</div>
    <div class="kpi"><span class="rot">Saldo projetado</span><span class="val">{{ dash.fluxo.saldo_projetado_centavos|brl }}</span><span class="peq">em 4 semanas</span></div>
    <div class="kpi"><span class="rot">MRR</span><span class="val pos">{{ dash.mrr_centavos|brl }}</span><span class="peq">recorrente / mês</span></div>
    <div class="kpi"><span class="rot">Resultado do mês</span><span class="val {{ 'pos' if dash.dre.resultado_centavos >= 0 else 'neg' }}">{{ dash.dre.resultado_centavos|brl }}</span><span class="peq">margem {{ dash.dre.margem_pct }}%</span></div>
  </div>

  <div class="dash-div"></div>

  <div class="dash-grid{% if not dash.tem_funil %} solo{% endif %}">
    <div>
      <h3 class="sub-h">Entradas × saídas <span>· mês atual</span></h3>
      {% set maxrd = [dash.dre.receitas_centavos, dash.dre.despesas_centavos, 1]|max %}
      <div class="rd-row">
        <div class="rd-top"><span class="rd-nome"><span class="swatch" style="background:var(--verde-claro)"></span>Receitas</span><span class="rd-val" style="color:var(--verde-claro)">{{ dash.dre.receitas_centavos|brl }}</span></div>
        <div class="rd-bar"><span style="width:{{ (100*dash.dre.receitas_centavos/maxrd)|round(1) }}%;background:var(--verde-claro)"></span></div>
      </div>
      <div class="rd-row">
        <div class="rd-top"><span class="rd-nome"><span class="swatch" style="background:#e07a6b"></span>Despesas</span><span class="rd-val" style="color:#e07a6b">{{ dash.dre.despesas_centavos|brl }}</span></div>
        <div class="rd-bar"><span style="width:{{ (100*dash.dre.despesas_centavos/maxrd)|round(1) }}%;background:#e07a6b"></span></div>
      </div>
      <div class="rd-res"><span class="rl">Resultado do mês</span><span class="rv {{ 'pos' if dash.dre.resultado_centavos >= 0 else 'neg' }}">{{ dash.dre.resultado_centavos|brl }}</span></div>
      {% if dash.dre.top_receitas or dash.dre.top_despesas %}
      <div class="cats">
        <div><h4>Top receitas</h4>
          {% for cat, v in dash.dre.top_receitas[:3] %}<div class="ci"><span class="cn">{{ cat or 'Outros' }}</span><span class="cv">{{ v|brl }}</span></div>{% else %}<div class="ci mut">—</div>{% endfor %}
        </div>
        <div><h4>Top despesas</h4>
          {% for cat, v in dash.dre.top_despesas[:3] %}<div class="ci"><span class="cn">{{ cat or 'Outros' }}</span><span class="cv">{{ v|brl }}</span></div>{% else %}<div class="ci mut">—</div>{% endfor %}
        </div>
      </div>
      {% endif %}
      {% if dash.dre.a_definir_n %}<p class="mut" style="font-size:.76rem;margin:.7rem 0 0">{{ dash.dre.a_definir_n }} lançamento(s) a classificar ({{ dash.dre.a_definir_centavos|brl }}) fora do resultado.</p>{% endif %}
    </div>

    {% if dash.tem_funil %}
    <div>
      <h3 class="sub-h">Funil de vendas <span>· propostas por status</span></h3>
      {% if dash.funil_total %}
      <div class="funil">
        <div class="donut" role="img" aria-label="{{ dash.funil_total }} propostas por status" style="background:{{ dash.funil_gradiente }}">
          <div class="mid"><span class="n">{{ dash.funil_total }}</span><span class="l">propostas</span></div>
        </div>
        <div class="legenda">
          {% for f in dash.funil_legenda %}<div class="leg-row"><span class="dot" style="background:{{ f.cor }}"></span><span class="leg-nome{% if f.mut %} mut{% endif %}">{{ f.rotulo }}</span><span class="leg-n">{{ f.n }}</span></div>{% endfor %}
        </div>
      </div>
      <div class="resumo">
        <div class="r"><span class="rv win">{{ dash.funil_conversao }}%</span><span class="rl">conversão</span></div>
        <div class="r"><span class="rv">{{ dash.funil_previsto|brl }}</span><span class="rl">valor previsto</span></div>
        <div class="r"><span class="rv">{{ dash.funil_recorrente|brl }}</span><span class="rl">novo recorrente/mês</span></div>
      </div>
      {% else %}
      <div class="funil-vazio">
        <div>Nenhuma proposta ainda.</div>
        <a href="/painel/servicos" style="color:var(--verde-claro);text-decoration:none;font-weight:600">Criar minha primeira proposta →</a>
      </div>
      {% endif %}
    </div>
    {% endif %}
  </div>

  <div class="dash-div"></div>

  <div>
    <div class="fluxo-head">
      <h3 class="sub-h" style="margin:0">Fluxo projetado <span>· próximas 4 semanas</span></h3>
      <span class="fv">saldo hoje {{ dash.fluxo.saldo_atual_centavos|brl }} → <b>{{ dash.fluxo.saldo_projetado_centavos|brl }}</b></span>
    </div>
    <svg class="fluxo-svg" viewBox="0 0 600 120" preserveAspectRatio="none" role="img" aria-label="Saldo projetado nas próximas 4 semanas">
      <defs><linearGradient id="fluxoGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3987e5" stop-opacity="0.28"/><stop offset="1" stop-color="#3987e5" stop-opacity="0"/></linearGradient></defs>
      <line x1="0" y1="30" x2="600" y2="30" stroke="var(--borda)" stroke-width="1"/>
      <line x1="0" y1="65" x2="600" y2="65" stroke="var(--borda)" stroke-width="1"/>
      <line x1="0" y1="100" x2="600" y2="100" stroke="var(--borda)" stroke-width="1"/>
      <path d="{{ dash.fluxo_area }}" fill="url(#fluxoGrad)"/>
      <polyline points="{{ dash.fluxo_poly }}" fill="none" stroke="#3987e5" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="{{ dash.fluxo_end_x|round(1) }}" cy="{{ dash.fluxo_end_y|round(1) }}" r="4.5" fill="#3987e5" stroke="var(--card)" stroke-width="2"/>
    </svg>
    <div class="xlabels"><span>hoje</span><span>sem 1</span><span>sem 2</span><span>sem 3</span><span>sem 4</span></div>
  </div>
</div>
{% endif %}

<details class="card larga acc">
  <summary>Meus dados e endereço <span class="chev">▾</span></summary>
  <div class="acc-body">
    {% include "bloco_conta" %}
  </div>
</details>

<details class="card larga acc"{% if aviso or erro %} open{% endif %}>
  <summary><span>Pessoas da conta<span class="sum-meta">· {{ membros|length }} pessoa(s)</span></span> <span class="chev">▾</span></summary>
  <div class="acc-body">
  {% if erro %}<div class="erro" style="margin-top:.4rem">{{ erro }}</div>{% endif %}
  {% if aviso %}<div class="ok" style="margin-top:.4rem">{{ aviso }}</div>{% endif %}
  <div class="membros" style="margin-top:.6rem">
  {% for m in membros %}
  <div class="membro-row">
    <div class="membro-id">
      <div class="avatar">{{ (m[0] or '?')[0]|upper }}</div>
      <div><div class="membro-nome">{{ m[0] or '-' }}</div>
      <div class="membro-papel"><span class="tag">{{ m[1] }}</span> {{ '' if m[3] else '· desativado' }}</div></div>
    </div>
    <div class="membro-contato">
      {% if m[2] or m[6] %}
        {% if m[6] %}<div class="conv mut">✅ Telegram conectado</div>{% endif %}
        {% if m[2] %}<div class="conv mut">✅ WhatsApp conectado</div>{% endif %}
        {% if m[5] and not m[6] %}<div class="mut" style="font-size:.78rem;margin-top:.2rem">🔑 Conectar Telegram também: <code>{{ m[5] }}</code></div>{% endif %}
      {% elif m[5] %}
        <div class="conv-invite">
          <div>🔑 <code>{{ m[5] }}</code></div>
          <div class="conv-links">
            <a class="lk-tg" href="https://t.me/clawaladdin_bot?start={{ m[5]|urlencode }}" target="_blank" rel="noopener">📨 Telegram</a>
            <button type="button" class="lk-copy" onclick="copiarConvite(this, 'https://t.me/clawaladdin_bot?start={{ m[5]|urlencode }}')">🔗 Copiar</button>
            {% if whatsapp_bot_num %}
            <a class="lk-wpp" href="https://wa.me/{{ whatsapp_bot_num }}?text={{ m[5]|urlencode }}" target="_blank" rel="noopener">🟢 WhatsApp</a>
            <button type="button" class="lk-copy" onclick="copiarConvite(this, 'https://wa.me/{{ whatsapp_bot_num }}?text={{ m[5]|urlencode }}')">🔗 Copiar</button>
            {% endif %}
          </div>
        </div>
      {% else %}<span class="mut">sem contato</span>{% endif %}
    </div>
    <div class="membro-acoes">
      <form method="post" action="/membros/reconectar"><input type="hidden" name="membro_id" value="{{ m[4] }}"><button class="btn-conv">↻ Reconectar</button></form>
      {% if m[1] != 'dono' %}
        {% if m[3] %}<form method="post" action="/membros/desativar"><input type="hidden" name="membro_id" value="{{ m[4] }}"><button class="btn-off">desativar</button></form>
        {% else %}<form method="post" action="/membros/reativar"><input type="hidden" name="membro_id" value="{{ m[4] }}"><button class="btn-on">reativar</button></form>{% endif %}
      {% else %}<span class="mut" style="font-size:.85rem;align-self:center">titular</span>{% endif %}
    </div>
  </div>
  {% endfor %}
  </div>
  <div style="margin-top:1rem"><button type="button" onclick="addPessoaToggle()" id="add-btn" style="width:auto;margin:0;background:var(--verde);color:#fff;border:0;border-radius:999px;padding:.42rem 1rem;font-size:.82rem;cursor:pointer">＋ Adicionar pessoa</button></div>
  <div id="add-pessoa-body"{% if not aviso %} style="display:none"{% endif %}>
    <div style="border-top:1px solid #212122;margin-top:1rem;padding-top:1rem">
    {% if pode_adicionar %}
      <div class="mut" style="font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;margin-bottom:.6rem">Nova pessoa</div>
      <form method="post" action="/membros/adicionar">
        <label>Nome</label><input name="nome" required maxlength="80">
        <div style="display:flex;gap:.6rem">
          <div style="flex:1"><label>WhatsApp <span class="mut">— opcional</span></label><input name="whatsapp" maxlength="20" placeholder="86 98888-7777"></div>
          <div style="flex:1"><label>Acesso</label><select name="papel"><option value="membro">Membro</option><option value="restrito">Restrito (só lista)</option></select></div>
        </div>
        <button style="margin-top:1rem">Adicionar à conta</button>
      </form>
      <p class="mut" style="margin-top:.7rem;font-size:.8rem">Ao adicionar, você recebe um código de convite pro Telegram (ou a pessoa já usa pelo WhatsApp cadastrado).</p>
      {% if extra_pago %}<p class="mut" style="font-size:.8rem">Seu plano inclui {{ inclusos }} pessoas; assento extra é cobrado.</p>{% endif %}
    {% else %}
      <p class="mut" style="font-size:.85rem">Seu plano ({{ conta[4] }}) permite {{ inclusos }} pessoa(s) e você já usa {{ ativos }}. Faça upgrade pra adicionar mais.</p>
    {% endif %}
    </div>
  </div>
  </div>
</details>
<script>window.addPessoaToggle=function(){var b=document.getElementById('add-pessoa-body');if(b)b.style.display=(b.style.display==='none'?'block':'none');};</script>
{% endblock %}"""

_FORNECEDOR = """{% extends "base" %}{% block conteudo %}
<div class="card larga"><h2>👨‍🌾 Fornecedor</h2>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}

<!-- MENU DE CARDS (visível ao entrar) -->
<div id="forn-menu" class="forn-cards">
  <div class="forn-grupo">Operação do dia</div>
  <a href="/painel/fornecedor/pedidos" class="forn-card" style="text-decoration:none">
    <span class="fc-tile">📋</span>
    <span class="fc-txt"><span class="fc-tit">Pedidos</span><span class="fc-leg">As cestas das assinaturas — separar, embalar e entregar.</span></span>
  </a>
  <button type="button" class="forn-card" onclick="fornAbrir('cestas')">
    <span class="fc-tile">🧺</span>
    <span class="fc-txt"><span class="fc-tit">Cestas</span><span class="fc-leg">Os tamanhos de cesta que seus clientes podem assinar.</span></span>
  </button>
  <div class="forn-grupo">Gestão</div>
  <a href="/painel/fornecedor/financeiro" class="forn-card" style="text-decoration:none">
    <span class="fc-tile amber">💰</span>
    <span class="fc-txt"><span class="fc-tit">Financeiro</span><span class="fc-leg">Quanto você ganhou, os repasses e as comissões.</span></span>
  </a>
</div>

<!-- SEÇÃO: Meus dados -->


<!-- SEÇÃO: Cestas -->
<div id="forn-cestas" class="forn-secao" style="display:none">
  <button type="button" class="forn-voltar" onclick="fornVoltar()">← voltar</button>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
    <h3 style="margin:0">🧺 Tamanhos de cesta</h3>
  </div>
  <p class="mut">Defina os tamanhos que seus clientes podem assinar. O preço é FIXO — o que vai dentro varia conforme a estação e o estoque.</p>

  <!-- lista dos tamanhos -->
  {% if tamanhos %}
  {% for t in tamanhos %}
  <div style="background:#1c1c1f;border:1px solid var(--borda);border-radius:8px;padding:1rem;margin-bottom:.6rem">
    <strong>{{ t.nome }}</strong> · <strong>R$ {{ (t.preco_centavos/100)|n2 }}</strong>
    <div class="mut" style="font-size:.85rem;margin-top:.3rem">
      {{ t.qtd_frutas }} frutas · {{ t.qtd_legumes }} legumes · {{ t.qtd_verduras }} verduras · {{ t.qtd_temperos }} temperos ({{ t.total_porcoes }} porções)
      {% if t.descricao %}<br>{{ t.descricao }}{% endif %}
    </div>
  </div>
  {% endfor %}
  {% else %}
  <p class="mut">Nenhum tamanho ainda. Crie o primeiro (ex: Pequena, Média, Grande).</p>
  {% endif %}

  <!-- form criar tamanho -->
  <div style="background:var(--borda);border-radius:6px;padding:1rem;margin-top:1rem">
    <h4 style="margin-top:0">Novo tamanho</h4>
    <form method="post" action="/painel/fornecedor/cestas/criar">
      <label>Nome</label>
      <input name="nome" placeholder="Pequena / Média / Grande" required style="width:100%">
      <label>Preço fixo (R$)</label>
      <input name="preco" placeholder="90,00" required style="width:100%">
      <label>Porções de frutas</label>
      <input name="qtd_frutas" type="number" value="0" style="width:100%">
      <label>Porções de legumes</label>
      <input name="qtd_legumes" type="number" value="0" style="width:100%">
      <label>Porções de verduras</label>
      <input name="qtd_verduras" type="number" value="0" style="width:100%">
      <label>Porções de temperos</label>
      <input name="qtd_temperos" type="number" value="0" style="width:100%">
      <label>Descrição (opcional)</label>
      <input name="descricao" placeholder="ideal pra 2 pessoas por semana" style="width:100%">
      <button style="background:var(--verde);color:#fff;padding:.5rem 1rem;border:0;border-radius:6px;cursor:pointer;margin-top:.8rem;width:100%;font-weight:500">Criar tamanho</button>
    </form>
  </div>
</div>

</div>

<!-- SEÇÃO: Pedidos -->
<!-- SEÇÃO: Financeiro -->
<div id="forn-financeiro" class="forn-secao" style="display:none">
  <button type="button" class="forn-voltar" onclick="fornVoltar()">← voltar</button>
  <h3>Financeiro</h3>
  <p class="mut">Em breve: seus ganhos, repasses e comissões.</p>
</div>

</div>

<style>
.forn-cards{display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:.7rem; margin-top:1rem}
.forn-card{display:flex; flex-direction:row; align-items:flex-start; gap:.75rem; text-align:left;
  background:#1c1c1f; border:1px solid var(--borda); border-radius:12px; padding:1rem; cursor:pointer; transition:border-color .15s; color:inherit; font:inherit}
.forn-card:hover{border-color:var(--verde)}
.fc-ic{font-size:24px; margin-bottom:.4rem; line-height:1}
.fc-tit{font-size:.95rem; font-weight:500; color:#f0f0ee; margin-bottom:.2rem}
.fc-leg{font-size:.8rem; color:var(--txt-mut); line-height:1.4}
.fc-txt{display:flex; flex-direction:column}
.fc-tile{width:42px; height:42px; border-radius:11px; background:#13251d; color:var(--verde-claro); display:flex; align-items:center; justify-content:center; font-size:20px; flex:none}
.fc-tile.amber{background:#2a2417; color:#e0b877}
.fc-tile.blue{background:#16202e; color:#7ab0e8}
.forn-grupo{grid-column:1/-1; font-size:11px; color:#6f6f6a; text-transform:uppercase; letter-spacing:.05em; margin:.7rem 0 -.05rem}
.forn-grupo:first-child{margin-top:.2rem}
.forn-secao{margin-top:1rem}
.forn-voltar{background:transparent; border:none; color:var(--verde-claro); cursor:pointer; font-size:.9rem; padding:0 0 .8rem 0; text-decoration:none}
.forn-voltar:hover{color:#7ad4b4}
</style>

<script>
function fornAbrir(secao){
  document.getElementById('forn-menu').style.display = 'none';
  ['cestas','pedidos','financeiro'].forEach(function(s){
    document.getElementById('forn-'+s).style.display = (s===secao ? 'block' : 'none');
  });
}
function fornVoltar(){
  document.getElementById('forn-menu').style.display = 'grid';
  ['cestas','pedidos','financeiro'].forEach(function(s){
    document.getElementById('forn-'+s).style.display = 'none';
  });
}
// COMPRAS
</script>
{% endblock %}"""

_SENHA = """{% extends "base" %}{% block conteudo %}
<div class="card"><h1>Alterar senha</h1>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if ok %}<div class="ok">{{ ok }}</div>{% endif %}
<form method="post" action="/senha">
<label>Senha atual</label><input name="atual" type="password" required>
<label>Nova senha</label><input name="nova" type="password" required minlength="8" maxlength="72">
<button>Salvar nova senha</button></form>
<p class="mut" style="margin-top:1rem"><a href="/painel" style="color:var(--verde-claro)">Voltar ao painel</a></p>
</div>{% endblock %}"""

_DASH = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
<div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.5rem">
<h1 style="margin:0">Financeiro</h1>
<form method="get" action="/painel/financeiro" style="margin:0; display:flex; gap:.5rem; align-items:center">
<select name="mes" onchange="this.form.submit()">
{% for v,rotulo in meses %}<option value="{{ v }}" {% if v==mes_sel %}selected{% endif %}>{{ rotulo }}</option>{% endfor %}
</select>
{% if pessoas|length > 1 %}<select name="membro" onchange="this.form.submit()">
<option value="">Todos</option>
{% for mid,nome in pessoas %}<option value="{{ mid }}" {% if mid==membro_sel %}selected{% endif %}>{{ nome }}</option>{% endfor %}
</select>{% endif %}
</form></div>
{% if eh_pj %}
<div style="margin:.8rem 0 .2rem">
<div style="color:#888780;font-size:.7rem;text-transform:uppercase;letter-spacing:.5px;margin-bottom:.35rem">Ver lançamentos de</div>
<div style="display:inline-flex;background:var(--card);border:1px solid var(--borda);border-radius:9px;padding:3px;gap:2px;flex-wrap:wrap">
<a href="/painel/financeiro?mes={{ mes_sel }}{% if membro_sel %}&membro={{ membro_sel }}{% endif %}" style="text-decoration:none;font-size:.8rem;padding:.35rem .8rem;border-radius:6px;{% if not natureza_sel %}background:var(--verde);color:#fff;font-weight:600{% else %}color:#b4b2a9{% endif %}">Todos</a>
<a href="/painel/financeiro?mes={{ mes_sel }}{% if membro_sel %}&membro={{ membro_sel }}{% endif %}&natureza=pessoal" style="text-decoration:none;font-size:.8rem;padding:.35rem .8rem;border-radius:6px;{% if natureza_sel=='pessoal' %}background:#f0c05a;color:#1a1409;font-weight:600{% else %}color:#b4b2a9{% endif %}">👤 Pessoal</a>
<a href="/painel/financeiro?mes={{ mes_sel }}{% if membro_sel %}&membro={{ membro_sel }}{% endif %}&natureza=empresa" style="text-decoration:none;font-size:.8rem;padding:.35rem .8rem;border-radius:6px;{% if natureza_sel=='empresa' %}background:var(--verde);color:#fff;font-weight:600{% else %}color:#b4b2a9{% endif %}">🏢 Empresa</a>
<a href="/painel/financeiro?mes={{ mes_sel }}{% if membro_sel %}&membro={{ membro_sel }}{% endif %}&natureza=a_definir" style="text-decoration:none;font-size:.8rem;padding:.35rem .8rem;border-radius:6px;display:inline-flex;align-items:center;gap:.35rem;{% if natureza_sel=='a_definir' %}background:#3a2c1d;color:#f0c05a;font-weight:600{% else %}color:#f0c05a{% endif %}">⏳ A definir{% if n_a_definir %} <span style="background:#3a2c1d;color:#f0c05a;font-size:.62rem;padding:1px 6px;border-radius:8px">{{ n_a_definir }}</span>{% endif %}</a>
</div>
</div>
{% endif %}
{% if eh_pj and n_a_definir %}<div style="background:#2b2416;border:1px solid #f0c05a44;border-radius:8px;padding:.55rem .9rem;margin:.4rem 0 0;color:#f0c05a;font-size:.83rem;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem"><span>🏢 <b>{{ n_a_definir }}</b> lançamento(s) sem classificar (pessoal ou empresa).</span> <button type="button" onclick="abrirClassificador()" style="background:#f0c05a;color:#1a1409;border:0;border-radius:6px;padding:.35rem .9rem;font-size:.8rem;font-weight:600;cursor:pointer">classificar em lote</button></div>{% endif %}

{% if eh_pj %}<div id="modal-classif" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:999;align-items:center;justify-content:center;padding:1rem">
<div style="background:var(--bg);border:1px solid var(--borda);border-radius:12px;max-width:560px;width:100%;max-height:85vh;display:flex;flex-direction:column;padding:1.1rem 1.2rem">
<div style="font-size:1rem;font-weight:600;color:#f4f4f4">Classificar lançamentos a definir</div>
<div style="font-size:.75rem;color:#888780;margin:.2rem 0 .7rem">Escolha pessoal ou empresa em cada um. Os que deixar em branco continuam "a definir".</div>
<div style="display:flex;gap:.4rem;align-items:center;margin-bottom:.7rem;flex-wrap:wrap">
<span style="color:#888780;font-size:.7rem">atalhos:</span>
<button type="button" onclick="classifTodos('pessoal')" style="font-size:.7rem;color:#f0c05a;background:#3a2c1d;border:0;padding:.25rem .7rem;border-radius:6px;cursor:pointer">todos pessoal</button>
<button type="button" onclick="classifTodos('empresa')" style="font-size:.7rem;color:var(--verde-claro);background:#1d3a2e;border:0;padding:.25rem .7rem;border-radius:6px;cursor:pointer">todos 🏢 empresa</button>
<button type="button" onclick="classifTodos('')" style="font-size:.7rem;color:#b4b2a9;background:#1c1c1e;border:0;padding:.25rem .7rem;border-radius:6px;cursor:pointer">limpar</button>
</div>
<div id="classif-lista" style="background:#131316;border-radius:9px;overflow-y:auto;flex:1;margin-bottom:.8rem"></div>
<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem">
<span id="classif-contador" style="color:#888780;font-size:.75rem"></span>
<span style="display:flex;gap:.5rem">
<button type="button" onclick="fecharClassificador()" style="border:1px solid #444;background:transparent;color:#b4b2a9;font-size:.8rem;padding:.4rem .9rem;border-radius:6px;cursor:pointer">Cancelar</button>
<button type="button" id="classif-salvar" onclick="salvarClassificacao()" style="background:var(--verde);color:#fff;font-size:.8rem;font-weight:600;padding:.4rem 1rem;border:0;border-radius:6px;cursor:pointer">Salvar</button>
</span>
</div>
</div></div>{% endif %}

<div class="fin-cards">
<div class="metric" style="display:flex;flex-direction:column;justify-content:space-between"><div><span>Saldo anterior</span><b style="white-space:nowrap;color:{% if resumo.anterior < 0 %}#e07a5f{% else %}var(--verde-claro){% endif %}">{{ brl(resumo.anterior) }}</b>{% if eh_pj and natureza_sel in ['empresa','pessoal','a_definir'] and resumo.anterior == 0 %}<small style="display:block;color:#6a7178;font-size:.62rem;margin-top:.3rem;line-height:1.3">sem histórico de {{ {'empresa':'empresa','pessoal':'pessoal','a_definir':'lançamentos a definir'}[natureza_sel] }} antes deste mês</small>{% endif %}</div>
<div style="margin-top:1rem;padding-top:.9rem;border-top:1px solid #1e1e20"><span>= {% if eh_pj and natureza_sel %}Resultado{% else %}Saldo{% endif %} {% if q_search %}da busca{% else %}do mês{% endif %}</span><b style="white-space:nowrap;color:var(--verde-claro)">{{ brl(resumo.saldo) }}</b></div></div>
<div class="metric"><span>+ Receitas {% if q_search %}da busca{% else %}do mês{% endif %}{% if eh_pj and natureza_sel %} · {{ natureza_sel|replace('a_definir','a definir') }}{% endif %}</span><b class="nowrap">{{ brl(resumo.receitas) }}</b>{% if eh_pj and not natureza_sel and quebra %}<div style="margin-top:.5rem;padding-top:.45rem;border-top:1px solid #1e1e20;font-size:.68rem;line-height:1.85">
<div style="display:flex;justify-content:space-between;gap:.4rem;color:#7a9a8a"><span class="nowrap">🏢 Empresa</span><span class="nowrap">{{ (quebra.receitas.empresa/100)|n2 }}</span></div>
<div style="display:flex;justify-content:space-between;gap:.4rem;color:#7a7a75"><span class="nowrap">👤 Pessoal</span><span class="nowrap">{{ (quebra.receitas.pessoal/100)|n2 }}</span></div>
<div style="display:flex;justify-content:space-between;gap:.4rem;color:#7a7a75"><span class="nowrap">⏳ A definir</span><span class="nowrap">{{ (quebra.receitas.a_definir/100)|n2 }}</span></div>
</div>{% endif %}</div>
<div class="metric"><span>− Despesas {% if q_search %}da busca{% else %}do mês{% endif %}{% if eh_pj and natureza_sel %} · {{ natureza_sel|replace('a_definir','a definir') }}{% endif %}</span><b class="nowrap">{{ brl(resumo.despesas) }}</b>{% if eh_pj and not natureza_sel and quebra %}<div style="margin-top:.5rem;padding-top:.45rem;border-top:1px solid #1e1e20;font-size:.68rem;line-height:1.85">
<div style="display:flex;justify-content:space-between;gap:.4rem;color:#9a8a7a"><span class="nowrap">🏢 Empresa</span><span class="nowrap">{{ (quebra.despesas.empresa/100)|n2 }}</span></div>
<div style="display:flex;justify-content:space-between;gap:.4rem;color:#7a7a75"><span class="nowrap">👤 Pessoal</span><span class="nowrap">{{ (quebra.despesas.pessoal/100)|n2 }}</span></div>
<div style="display:flex;justify-content:space-between;gap:.4rem;{% if quebra.despesas.a_definir %}color:#f0c05a{% else %}color:#7a7a75{% endif %}"><span class="nowrap">⏳ A definir{% if quebra.despesas.a_definir %} ⚠{% endif %}</span><span class="nowrap">{{ (quebra.despesas.a_definir/100)|n2 }}</span></div>
</div>{% endif %}</div>
</div>

<h1 style="font-size:1.05rem">Despesas por categoria</h1>
{% if categorias %}{% for cat,val in categorias %}
<div class="cat-linha" onclick="abrirCat(this)" data-cat="{{ cat }}" data-tipo="despesa" style="cursor:pointer">
  <div style="display:flex; justify-content:space-between; font-size:.9rem; margin:.4rem 0 .2rem">
    <span><span class="seta">▸</span> {{ cat }}</span><b>{{ brl(val) }}</b>
  </div>
  <div class="barra"><div class="barra-fill" style="width:{{ (val*100//maior_cat) if maior_cat else 0 }}%"></div></div>
</div>
<div class="cat-lancamentos" style="display:none; padding:.2rem 0 .6rem 1.2rem"></div>
{% endfor %}{% else %}<p class="mut">Sem despesas neste mês.</p>{% endif %}

<h1 style="font-size:1.05rem; margin-top:1.6rem">Receitas por categoria</h1>
{% if receitas_cat %}{% for cat,val in receitas_cat %}
<div class="cat-linha" onclick="abrirCat(this)" data-cat="{{ cat }}" data-tipo="receita" style="cursor:pointer">
  <div style="display:flex; justify-content:space-between; font-size:.9rem; margin:.4rem 0 .2rem">
    <span><span class="seta">▸</span> {{ cat }}</span><b>{{ brl(val) }}</b>
  </div>
  <div class="barra"><div class="barra-fill" style="width:{{ (val*100//maior_rec) if maior_rec else 0 }}%; background:var(--verde-claro)"></div></div>
</div>
<div class="cat-lancamentos" style="display:none; padding:.2rem 0 .6rem 1.2rem"></div>
{% endfor %}{% else %}<p class="mut">Sem receitas neste mês.</p>{% endif %}

{% if not q_search and prev_cartao and prev_cartao.pontos %}
<div class="card" style="margin-top:1.4rem;border:1px solid #2a3a33">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:.6rem">
    <strong style="font-size:.95rem">💳 Previsão da fatura do cartão</strong>
    <b style="color:#f0c05a;white-space:nowrap">{{ prev_cartao.total_centavos|brl }}</b>
  </div>
  <div class="mut" style="font-size:.72rem;margin:.15rem 0 .6rem">parcelas que ainda vão cair nos próximos meses</div>
  <table style="width:100%;font-size:.85rem">
    {% for p in prev_cartao.pontos %}<tr style="border-top:1px solid #232325">
      <td style="padding:.3rem 0">{{ ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez'][p.competencia.month-1] }}/{{ p.competencia.year }}
        <span class="mut" style="font-size:.72rem">· {{ p.parcelas }} parcela{{ 's' if p.parcelas != 1 else '' }}</span></td>
      <td style="text-align:right;font-weight:600;color:#f0b8b8">{{ p.total_centavos|brl }}</td></tr>{% endfor %}
  </table>
</div>
{% endif %}
<h1 style="font-size:1.05rem; margin-top:1.6rem">Lançamentos</h1>
<form method="get" action="/painel/financeiro" style="margin:.5rem 0 1rem">
<input type="search" name="q" value="{{ q_search or '' }}" placeholder="🔎 Buscar lançamento..."
       style="width:100%;padding:.6rem .8rem;border:1px solid #2a3a33;border-radius:8px;background:var(--card);color:var(--txt);font-size:.95rem">
{% if q_search %}<input type="hidden" name="mes" value="{{ mes_sel }}">{% endif %}
</form>
{% if q_search %}
<p style="margin:.5rem 0; color:#8a8a85">{{ n_resultados }} {{ 'resultado' if n_resultados == 1 else 'resultados' }} para "{{ q_search }}"
{% if n_resultados > 0 %}<a href="/painel/financeiro?mes={{ mes_sel }}" style="color:var(--verde-claro);text-decoration:none"> — limpar busca</a>{% endif %}
</p>
{% endif %}
<div class="abas" {% if q_search %}style="display:none"{% endif %}>
<button type="button" class="aba ativa" data-f="todos" onclick="filtrarTipo(this)">Todos</button>
<button type="button" class="aba" data-f="despesa" onclick="filtrarTipo(this)">Despesas</button>
<button type="button" class="aba" data-f="receita" onclick="filtrarTipo(this)">Receitas</button>
</div>
{% if q_search %}
<table style="width:100%;border-collapse:collapse;margin:1rem 0">
<tbody>
{% if n_resultados == 0 %}
<tr><td colspan="5" style="padding:1.2rem;text-align:center;color:#8a8a85">Nenhum lançamento encontrado pra "{{ q_search }}"</td></tr>
{% else %}
{% for l in lancamentos %}
<tr data-tipo="{{ l.tipo }}" style="border-bottom:1px solid var(--borda)">
<td style="padding:.7rem .8rem;font-size:.9rem;white-space:nowrap">{{ l.data.strftime('%d/%m/%Y') }}</td>
<td style="padding:.7rem .8rem">{{ l.descricao }}{% if l.origem=='foto' %} 📷{% endif %}{% if l.forma_pagamento %}<span style="font-size:.62rem;background:#242426;color:#9aa39a;padding:1px 7px;border-radius:8px;margin-left:.3rem;white-space:nowrap">💳 {{ forma_pag_label(l.forma_pagamento) }}</span>{% endif %}</td>
<td style="padding:.7rem .8rem;font-size:.85rem;color:var(--txt-mut)">{{ l.categoria }}</td>
<td style="padding:.7rem .8rem;text-align:right;font-weight:500;color:{{ 'var(--verde-claro)' if l.tipo=='receita' else '#f0b8b8' }}">
{{ '+' if l.tipo=='receita' else '−' }} {{ brl(l.valor).replace('R$ ','') }}</td>
<td style="padding:.7rem .4rem;text-align:right">
<button type="button" class="lanc-rm" data-id="{{ l.id }}" onclick="apagarLanc(this)" title="apagar lançamento" style="margin:0;padding:.15rem .45rem;width:auto;background:transparent;color:#8a3636;border:0;cursor:pointer;font-size:.95rem">✕</button>
</td>
</tr>
{% endfor %}
{% endif %}
</tbody>
</table>
{% else %}
<div id="lista-dias">
{% for dia in dias %}
<div class="dep" data-tipos="{% for it in dia.itens %}{{ it.tipo }} {% endfor %}">
<div class="dep-cab" onclick="abrirDep(this)">
<span><span class="seta">▸</span> {{ dia.data.strftime('%d/%m') }}
<span class="mut">· {{ dia.itens|length }} {{ 'lançamento' if dia.itens|length == 1 else 'lançamentos' }}</span></span>
<b style="color:{{ 'var(--verde-claro)' if dia.saldo >= 0 else '#f0b8b8' }}">{{ '+' if dia.saldo >= 0 else '−' }} {{ brl(dia.saldo|abs).replace('R$ ','') }}</b>
</div>
<div class="dep-corpo" style="flex-direction:column; gap:0">
<table style="margin:0">
{% for l in dia.itens %}<tr data-tipo="{{ l.tipo }}" data-cat="{{ canon(l.categoria, l.tipo) }}" data-desc="{{ l.descricao }}" data-valor="{{ brl(l.valor) }}">
<td>{{ l.descricao }}{% if l.origem=='foto' %} 📷{% endif %}
{% if l.forma_pagamento %}<span class="fpag-tag" style="font-size:.62rem;background:#242426;color:#9aa39a;padding:1px 7px;border-radius:8px;margin-left:.3rem;white-space:nowrap">💳 {{ forma_pag_label(l.forma_pagamento) }}</span>{% endif %}
{% if eh_pj %}{% if l.natureza=='empresa' %}<span class="nat-tag" style="font-size:.62rem;background:#1d3a2e;color:var(--verde-claro);padding:1px 7px;border-radius:8px;margin-left:.3rem">🏢 empresa</span>{% elif l.natureza=='pessoal' %}<span class="nat-tag" style="font-size:.62rem;background:#3a2c1d;color:#f0c05a;padding:1px 7px;border-radius:8px;margin-left:.3rem">👤 pessoal</span>{% else %}<span style="display:inline-flex;gap:.25rem;margin-left:.3rem"><button type="button" onclick="marcarNat({{ l.id }},'pessoal',this)" style="font-size:.6rem;padding:1px 6px;background:#3a2c1d;color:#f0c05a;border:0;border-radius:7px;cursor:pointer">pessoal?</button><button type="button" onclick="marcarNat({{ l.id }},'empresa',this)" style="font-size:.6rem;padding:1px 6px;background:#1d3a2e;color:var(--verde-claro);border:0;border-radius:7px;cursor:pointer">empresa?</button></span>{% endif %}{% endif %}</td>
<td class="nowrap">
<span style="display:inline-flex;align-items:center;gap:.4rem">
<select class="cat-edit" data-id="{{ l.id }}" data-orig="{{ canon(l.categoria, l.tipo) }}" onchange="catMudou(this)"
   style="background:var(--bg);border:1px solid #2a3a33;border-radius:6px;color:var(--txt);font-size:.78rem;padding:.2rem .45rem;max-width:130px">
{% for c in categorias_de(l.tipo) %}<option value="{{ c }}" style="background:var(--card);color:var(--txt)" {% if canon(l.categoria, l.tipo)==c %}selected{% endif %}>{{ c }}</option>{% endfor %}
</select>
<button type="button" class="cat-ok" onclick="salvarCat(this)" style="display:none;padding:.24rem .65rem;width:auto;font-size:.72rem;font-weight:600;background:var(--verde);color:#fff;border:0;border-radius:6px;cursor:pointer;line-height:1.1">OK</button>
</span>
</td>
{% if pessoas|length > 1 %}<td class="mut">{{ l.quem }}</td>{% endif %}
<td style="text-align:right; font-weight:500; color:{{ 'var(--verde-claro)' if l.tipo=='receita' else '#f0b8b8' }}">
{{ '+' if l.tipo=='receita' else '−' }} {{ brl(l.valor).replace('R$ ','') }}</td>
<td style="text-align:right"><button type="button" class="lanc-rm" data-id="{{ l.id }}" onclick="apagarLanc(this)" title="apagar lançamento" style="margin:0;padding:.15rem .45rem;width:auto;background:transparent;color:#8a3636;border:0;cursor:pointer;font-size:.95rem">✕</button></td></tr>{% endfor %}
</table>
</div>
</div>
{% else %}<p class="mut">Nenhum lançamento neste período.</p>{% endfor %}
</div>
{% endif %}
<p id="lanc-vazio" class="mut" style="display:none">Nenhum lançamento desse tipo neste período.</p>

<h1 style="font-size:1.05rem; margin-top:1.6rem">Raio-x do consumo por departamento</h1>
{% if raiox %}{% for dep, dados in raiox.items() %}
<div class="dep">
<div class="dep-cab" onclick="abrirDep(this)">
<span><span class="seta">▸</span> {{ dep }}</span>
<b>{{ brl(dados.total) }}</b>
</div>
<div class="dep-corpo" style="flex-direction:column; gap:0; padding-top:0">
{% for d in dados.dias %}
<div class="subdia">
<div class="subdia-cab" onclick="abrirSub(event, this)">
<span><span class="seta2">▸</span> {{ d.data.strftime('%d/%m/%Y') }}
<span class="mut">· {{ d.itens|length }} {{ 'item' if d.itens|length == 1 else 'itens' }}</span></span>
<span class="mut">{{ brl(d.subtotal) }}</span>
</div>
<div class="subdia-corpo">
{% for it in d.itens %}<span class="chip">{{ it.descricao }} · {{ brl(it.valor) }}</span>{% endfor %}
</div>
</div>
{% endfor %}
</div>
</div>{% endfor %}
{% else %}<p class="mut">Os itens aparecem aqui quando você fotografa um cupom de mercado.</p>{% endif %}
</div>

<script>
function filtrarTipo(btn){
  document.querySelectorAll('.aba').forEach(function(a){a.classList.remove('ativa')});
  btn.classList.add('ativa');
  var f = btn.dataset.f, visiveis = 0;
  document.querySelectorAll('#lista-dias .dep').forEach(function(dia){
    var linhas = dia.querySelectorAll('tr[data-tipo]'), comTipo = 0;
    linhas.forEach(function(tr){
      var ok = (f === 'todos' || tr.dataset.tipo === f);
      tr.style.display = ok ? '' : 'none';
      if (ok) comTipo++;
    });
    dia.style.display = comTipo ? '' : 'none';
    if (comTipo) visiveis++;
  });
  document.getElementById('lanc-vazio').style.display = visiveis ? 'none' : 'block';
}
function abrirSub(ev, cab){
  ev.stopPropagation();                 // nao fecha o departamento
  var sub = cab.parentElement;
  sub.classList.toggle('aberto');
  cab.querySelector('.seta2').textContent = sub.classList.contains('aberto') ? '▾' : '▸';
}
function abrirDep(cab){
  var dep = cab.parentElement;
  dep.classList.toggle('aberto');
  cab.querySelector('.seta').textContent = dep.classList.contains('aberto') ? '▾' : '▸';
}
function abrirCat(el){
  var box = el.nextElementSibling;            // .cat-lancamentos
  var seta = el.querySelector('.seta');
  var jaAberto = box.style.display !== 'none';
  // fecha todas
  document.querySelectorAll('.cat-lancamentos').forEach(function(b){ b.style.display='none'; });
  document.querySelectorAll('.cat-linha .seta').forEach(function(s){ s.textContent='▸'; });
  if(jaAberto){ return; }
  var cat = el.getAttribute('data-cat');       // categoria canônica
  var tipo = el.getAttribute('data-tipo') || 'despesa';  // tipo: despesa ou receita
  // pega TODAS as linhas de lançamento da página com a mesma categoria e tipo
  var linhas = document.querySelectorAll('#lista-dias tr[data-cat="'+cat+'"][data-tipo="'+tipo+'"]');
  var html = '';
  linhas.forEach(function(tr){
    html += '<div style="display:flex;justify-content:space-between;font-size:.85rem;margin:.25rem 0">'
          + '<span>'+ tr.getAttribute('data-desc') +'</span>'
          + '<span class="mut">'+ tr.getAttribute('data-valor') +'</span></div>';
  });
  box.innerHTML = html || '<span class="mut" style="font-size:.85rem">Sem lançamentos detalhados deste mês.</span>';
  box.style.display = 'block';
  seta.textContent = '▾';
}
function catMudou(sel){
  // mostra o botao OK so' quando o valor muda em relacao ao salvo
  var btn = sel.parentElement.querySelector('.cat-ok');
  btn.style.display = (sel.value !== sel.getAttribute('data-orig')) ? 'inline-block' : 'none';
}
var _classif = {};  // id -> 'pessoal'|'empresa'|''
function abrirClassificador(){
  var mes = new URLSearchParams(location.search).get('mes') || '';
  var membro = new URLSearchParams(location.search).get('membro') || '';
  fetch('/painel/lancamentos-a-definir?mes='+encodeURIComponent(mes)+'&membro='+encodeURIComponent(membro))
   .then(r=>r.json()).then(d=>{
     if(!d.ok) return;
     _classif = {};
     var lista = document.getElementById('classif-lista');
     lista.innerHTML = d.itens.map(function(it){
       _classif[it.id]='';
       var val = (it.valor/100).toLocaleString('pt-BR',{minimumFractionDigits:2});
       var sinal = it.tipo==='receita' ? '+' : '−';
       var desc = it.descricao ? (' · '+it.descricao) : '';
       return '<div style="display:flex;align-items:center;gap:.5rem;padding:.5rem .7rem;border-bottom:1px solid #1e1e20">'+
         '<span style="flex:1;color:var(--txt);font-size:.78rem">'+it.data+' · '+it.categoria+desc+' <span style="color:#888780">'+sinal+' R$ '+val+'</span></span>'+
         '<span style="display:inline-flex;background:var(--bg);border:1px solid var(--borda);border-radius:7px;padding:2px">'+
         '<button type="button" data-id="'+it.id+'" data-nat="pessoal" onclick="classifUm(this)" style="font-size:.68rem;padding:.25rem .7rem;border:0;border-radius:5px;background:transparent;color:#888780;cursor:pointer">pessoal</button>'+
         '<button type="button" data-id="'+it.id+'" data-nat="empresa" onclick="classifUm(this)" style="font-size:.68rem;padding:.25rem .7rem;border:0;border-radius:5px;background:transparent;color:#888780;cursor:pointer">empresa</button>'+
         '</span></div>';
     }).join('');
     _classifRefresh();
     document.getElementById('modal-classif').style.display='flex';
   });
}
function classifUm(btn){
  var id=btn.getAttribute('data-id'), nat=btn.getAttribute('data-nat');
  _classif[id] = (_classif[id]===nat) ? '' : nat;  // clicar de novo desmarca
  _classifPinta();
}
function classifTodos(nat){
  Object.keys(_classif).forEach(function(id){ _classif[id]=nat; });
  _classifPinta();
}
function _classifPinta(){
  document.querySelectorAll('#classif-lista button[data-id]').forEach(function(b){
    var id=b.getAttribute('data-id'), nat=b.getAttribute('data-nat');
    var on = _classif[id]===nat;
    if(on && nat==='pessoal'){ b.style.background='#f0c05a'; b.style.color='#1a1409'; b.style.fontWeight='600'; }
    else if(on && nat==='empresa'){ b.style.background='var(--verde)'; b.style.color='#fff'; b.style.fontWeight='600'; }
    else { b.style.background='transparent'; b.style.color='#888780'; b.style.fontWeight='400'; }
  });
  _classifRefresh();
}
function _classifRefresh(){
  var tot=Object.keys(_classif).length;
  var feitos=Object.keys(_classif).filter(function(k){return _classif[k];}).length;
  document.getElementById('classif-contador').textContent = feitos+' classificado(s) · '+(tot-feitos)+' em branco (fica a definir)';
  document.getElementById('classif-salvar').textContent = feitos>0 ? ('Salvar '+feitos+' classificação(ões)') : 'Salvar';
}
function fecharClassificador(){ document.getElementById('modal-classif').style.display='none'; }
function salvarClassificacao(){
  var mapa={};
  Object.keys(_classif).forEach(function(id){ if(_classif[id]) mapa[id]=_classif[id]; });
  if(!Object.keys(mapa).length){ fecharClassificador(); return; }
  fetch('/painel/lancamentos-classificar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mapa:mapa})})
   .then(r=>r.json()).then(d=>{ if(d.ok){ location.reload(); } });
}
function marcarNat(id, nat, btn){
  fetch('/painel/lancamento/natureza', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:'lancamento_id='+id+'&natureza='+nat})
    .then(r=>r.json()).then(d=>{ if(d.ok){ location.reload(); } });
}
function salvarCat(btn){
  var sel = btn.parentElement.querySelector('.cat-edit');
  var fd = new FormData();
  fd.append('lancamento_id', sel.getAttribute('data-id'));
  fd.append('categoria', sel.value);
  btn.disabled = true; btn.textContent = '...';
  fetch('/painel/lancamento/categoria', {method:'POST', body: fd})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(d.ok){
        // atualiza no lugar, SEM recarregar a pagina
        sel.setAttribute('data-orig', sel.value);
        var tr = sel.closest('tr'); if(tr){ tr.setAttribute('data-cat', sel.value); }
        sel.style.borderColor = 'var(--verde-claro)';
        btn.textContent = '✓';
        setTimeout(function(){
          btn.style.display = 'none'; btn.disabled = false;
          btn.textContent = 'OK'; sel.style.borderColor = '#2a3a33';
        }, 1100);
      } else {
        btn.disabled = false; btn.textContent = 'OK'; sel.style.borderColor = '#f0b8b8';
      }
    })
    .catch(function(){ btn.disabled = false; btn.textContent = 'OK'; sel.style.borderColor = '#f0b8b8'; });
}
function apagarLanc(btn){
  if(!confirm('Apagar este lançamento? Essa ação não pode ser desfeita.')) return;
  var fd = new FormData();
  fd.append('lancamento_id', btn.getAttribute('data-id'));
  btn.disabled = true;
  fetch('/painel/lancamento/apagar', {method:'POST', body: fd})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(d.ok){ var tr = btn.closest('tr'); if(tr){ tr.remove(); } }
      else { btn.disabled = false; alert('Não consegui apagar.'); }
    })
    .catch(function(){ btn.disabled = false; alert('Erro ao apagar.'); });
}
function copiarConvite(btn, url){
  navigator.clipboard.writeText(url).then(function(){
    var txt = btn.textContent;
    btn.textContent = '✅ Copiado!';
    setTimeout(function(){ btn.textContent = txt; }, 1500);
  }).catch(function(){ window.prompt('Copie o link:', url); });
}
</script>
{% endblock %}"""

_COMPRAS = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
<div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.5rem">
<div style="display:flex; gap:.5rem; align-items:center">
<h1 style="margin:0" id="titulo-abas">Lista de compras</h1>
</div>
<div style="display:flex; gap:.5rem">
{% if papel != 'dono' %}
<button id="btn-avisar" onclick="avisarTerminei()" style="margin:0;padding:.4rem .8rem;background:#2AABEE;color:#fff;font-size:.82rem;width:auto;border-radius:8px;border:0;cursor:pointer">✅ Avisar que terminei</button>
<span id="aviso-msg" class="mut" style="font-size:.85rem;margin-left:.5rem"></span>
{% endif %}
<button id="btn-apagar" style="margin:0;padding:.4rem .8rem;background:var(--borda);font-size:.82rem;width:auto;display:none;color:#d98a8a">Apagar lista</button>
</div>
</div>

<form id="form-add" style="display:flex; gap:.5rem; margin:1rem 0">
<input id="inp-add" placeholder="Adicionar item (ex: arroz, café...)" required maxlength="80" style="flex:1" autocomplete="off">
<button style="margin:0; width:auto; padding:.65rem 1.2rem">Adicionar</button>
</form>

<div class="abas">
  <button id="aba-lista" class="aba on" onclick="setAba('lista')" style="margin:0">Lista</button>
  <button id="aba-hist" class="aba" onclick="setAba('historico')" style="margin:0">Histórico</button>
</div>

<button id="btn-comparar" onclick="carregarPrecos()" style="margin:.5rem 0;padding:.4rem 1rem;background:#6366f1;color:#fff;border:0;border-radius:8px;cursor:pointer;font-size:.9rem">📊 Comparar preços</button>

<div id="comparador" style="margin:1rem 0"></div>

<div id="toggle-visao" style="display:none; gap:.5rem; margin:.5rem 0">
  <button id="bt-geral" onclick="setVisao('geral')"
    style="margin:0;padding:.4rem .9rem;border-radius:8px;border:0;cursor:pointer;background:var(--verde);color:#fff">Geral</button>
  <button id="bt-pessoa" onclick="setVisao('pessoa')"
    style="margin:0;padding:.4rem .9rem;border-radius:8px;border:1px solid var(--borda);cursor:pointer;background:#1a2233;color:#e7ecf3">Por pessoa</button>
</div>

<div id="lista-itens"></div>
<div id="btn-finalizar-box"></div>
<p id="resumo-lista" class="mut" style="margin-top:1rem"></p>
</div>

<script>
var DEPARTAMENTOS = [
  ["Hortifruti", ["alface","tomate","cebola","batata","banana","maca","maçã","laranja","limao","limão",
    "cenoura","alho","mamao","mamã","manga","uva","melancia","abacaxi","abacate","pera","morango",
    "couve","brocolis","brócolis","pimentao","pimentã","pepino","abobora","abóbora","chuchu","mandioca",
    "verdura","legume","fruta","salsa","cebolinha","coentro","rucula","rúcula","espinafre","beterraba",
    "manjericao","manjericã","gengibre","milho","vagem","quiabo","berinjela"]],
  ["Açougue", ["carne","frango","boi","porco","linguica","linguiça","bacon","bife","costela","file","filé",
    "coxa","sobrecoxa","peito de frango","picanha","alcatra","patinho","moida","moída","peixe","tilapia",
    "tilá","camarao","camarã","salsicha","peito","pernil","cupim","fraldinha","maminha","acem","acém"]],
  ["Laticínios", ["leite","queijo","iogurte","manteiga","margarina","requeijao","requeijã","creme de leite",
    "leite condensado","nata","mussarela","muçarela","mozzarela","prato","minas","coalho","ricota",
    "cream cheese","danone","ovos","ovo","parmesao","parmesã"]],
  ["Padaria", ["pao","pã","paes","pã","bolo","torrada","biscoito","bolacha","croissant","baguete",
    "rosca","frances","francês","forma","bisnaga","panettone","pao de queijo","pã de queijo"]],
  ["Bebidas", ["agua","água","refrigerante","suco","cerveja","vinho","cafe","café","cha","chá","achocolatado",
    "energetico","energético","coca","guarana","guaraná","fanta","sprite","whisky","vodka","gin","leite de coco",
    "isotonico","isotônico","gatorade","red bull","tonica","tônica","champagne","espumante"]],
  ["Limpeza", ["detergente","sabao","sabã","amaciante","desinfetante","agua sanitaria","água sanitária","candida",
    "câ","alvejante","limpa","multiuso","esponja","saco de lixo","vassoura","rodo","pano","cloro","veja",
    "ype","ypê","omo","comfort","pinho","desengordurante","lustra","cera"]],
  ["Higiene", ["sabonete","shampoo","xampu","condicionador","pasta de dente","creme dental","escova",
    "papel higienico","papel higiênico","absorvente","fralda","desodorante","cotonete","algodao","algodã",
    "lenço","lenco","aparelho de barbear","gilete","hidratante","fio dental","enxaguante","listerine"]],
  ["Mercearia", ["arroz","feijao","feijã","macarrao","macarrã","oleo","óleo","azeite","sal","acucar","açúcar",
    "farinha","molho","extrato","atum","sardinha","milho","ervilha","seleta","fermento","amido","tempero",
    "vinagre","cafe","café","achocolatado","cereal","aveia","granola","mel","geleia","amendoim","castanha",
    "lentilha","grao","grã","trigo","fuba","fubá","tapioca","leite em po","leite em pó","sopa","catchup",
    "ketchup","mostarda","maionese","shoyu","caldo","macarrao instantaneo","miojo","nescau","leite ninho",
    "biscoito","bolacha","chocolate","bala","doce","pipoca","salgadinho","chips","gelatina","pudim"]],
  ["Congelados", ["congelado","sorvete","polpa","hamburguer","hambúrguer","nuggets","pizza","lasanha",
    "pao de queijo congelado","empanado","petit pois","ervilha congelada"]],
];
var ORDEM_DEP = ["Hortifruti","Açougue","Laticínios","Padaria","Congelados","Mercearia","Bebidas","Limpeza","Higiene","Outros"];

function departamentoDe(nome){ var n=(nome||"").toLowerCase(); for(var d=0;d<DEPARTAMENTOS.length;d++){ var palavras=DEPARTAMENTOS[d][1]; for(var p=0;p<palavras.length;p++){ if(n.indexOf(palavras[p])!==-1) return DEPARTAMENTOS[d][0]; }} return "Outros"; }

(function(){
  var listaEl = document.getElementById('lista-itens');
  var resumoEl = document.getElementById('resumo-lista');
  var compEl = document.getElementById('comparador');
  var btnApagar = document.getElementById('btn-apagar');
  var ajustes = {};
  var pendentesAtuais = [];
  var _ultimoDados = null;
  var _aba = 'lista';

  function esc(s){ var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

  var _visao = 'geral';
  var _itensCache = [];

  function setVisao(v){
    _visao = v;
    document.getElementById('bt-geral').style.background = v==='geral' ? 'var(--verde)' : '#1a2233';
    document.getElementById('bt-geral').style.color = '#fff';
    document.getElementById('bt-pessoa').style.background = v==='pessoa' ? 'var(--verde)' : '#1a2233';
    document.getElementById('bt-pessoa').style.color = '#fff';
    renderItens(_itensCache);
  }

  function ligarBotoesLinha(){
    Array.prototype.forEach.call(listaEl.querySelectorAll('.mk'), function(b){
      b.onclick = function(){ acao({acao:'marcar', item_id:+b.getAttribute('data-id'), comprado:+b.getAttribute('data-c')}); };
    });
    Array.prototype.forEach.call(listaEl.querySelectorAll('.rm'), function(b){
      b.onclick = function(){ acao({acao:'remover', item_id:+b.getAttribute('data-id')}); };
    });
  }

  function acao(payload){
    return fetch('/painel/compras/api', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    }).then(function(r){ return r.json(); }).then(function(d){ render(d); return d; });
  }

  function setAba(a){
    _aba = a;
    document.getElementById('aba-lista').className = 'aba' + (a==='lista'?' on':'');
    document.getElementById('aba-hist').className = 'aba' + (a==='historico'?' on':'');
    if(a==='lista'){ render(_ultimoDados || {itens:[]}); }
    else { carregarHistorico(); }
  }

  function renderItens(itens){
    var pendentes = itens.filter(function(i){return !i.comprado;});
    var comprados = itens.filter(function(i){return i.comprado;});

    if(!pendentes.length && !comprados.length){
      listaEl.innerHTML = '<p class="mut">A lista está vazia. Adicione itens acima — ou peça pelo WhatsApp/Telegram: <i>"acabou o arroz, bota na lista"</i>.</p>';
      return;
    }

    var dica = pendentes.length ? '<div class="dica-toque">👆 toque no item = comprei · toque no ✕ = tirar da lista</div>' : '';

    var grupos={};
    pendentes.forEach(function(i){ var d=departamentoDe(i.descricao); (grupos[d]=grupos[d]||[]).push(i); });
    var html = dica;
    ORDEM_DEP.forEach(function(dep){
      if(!grupos[dep]) return;
      html += '<div class="dephead">'+dep+'</div>';
      ordenarAlf(grupos[dep]).forEach(function(i){ html += linhaItemAtivo(i); });
    });
    if(!pendentes.length) html += '<div class="mut" style="text-align:center;padding:1rem">Tudo no carrinho! 🛒</div>';

    if(comprados.length){
      html += '<div class="cart-head" onclick="toggleCarrinho()"><span id="cart-arrow">▸</span> No carrinho ('+comprados.length+')</div>';
      html += '<div id="cart-body" style="display:none">';
      ordenarAlf(comprados).forEach(function(i){ html += linhaItemComprado(i); });
      html += '</div>';
      html += '<button class="btn-finalizar" onclick="finalizarCompra('+comprados.length+')">✓ Finalizar compra ('+comprados.length+' itens)</button>';
    }
    listaEl.innerHTML = html;
  }

  function ordenarAlf(itens){
    return itens.slice().sort(function(a,b){ return (a.descricao||'').localeCompare((b.descricao||''),'pt',{sensitivity:'base'}); });
  }

  function linhaItemAtivo(i){
    var preco = i.preco ? '<span class="mut"> · ~'+i.preco+'</span>' : '';
    var quem = (_visao==='geral' && i.quem) ? '<span class="mut" style="font-size:.78rem"> · '+esc(i.quem)+'</span>' : '';
    return '<div class="litem" data-id="'+i.id+'">'
         + '<div class="toque" onclick="marcarItem('+i.id+')">'
         + '<span class="bol"></span>'
         + '<span class="nome">'+esc(i.descricao)+preco+quem+'</span></div>'
         + '<button class="rem" onclick="removerItem(event,'+i.id+')" title="tirar da lista">✕</button>'
         + '</div>';
  }

  function linhaItemComprado(i){
    return '<div style="display:flex;align-items:center;gap:.6rem;padding:.45rem .6rem;opacity:.7">'
         + '<span style="color:var(--verde);flex-shrink:0">✓</span>'
         + '<span style="color:#7a8a82;text-decoration:line-through;font-size:14px;flex:1">'+esc(i.descricao)+'</span>'
         + '<button onclick="desmarcarItem('+i.id+')" title="voltar pra lista" '
         + 'style="flex-shrink:0;width:32px;height:32px;display:flex;align-items:center;justify-content:center;'
         + 'background:transparent;border:0;color:var(--verde-claro);cursor:pointer;font-size:1rem;border-radius:8px">↩</button>'
         + '</div>';
  }

  function marcarItem(id){
    var el = listaEl.querySelector('.litem[data-id="'+id+'"]');
    if(el){ el.classList.add('flash-v','done'); el.querySelector('.bol').textContent='✓';
            setTimeout(function(){ el.classList.add('saindo'); },280); }
    setTimeout(function(){ acao({acao:'marcar', item_id:id, comprado:1}); }, 600);
  }

  function removerItem(ev,id){
    ev.stopPropagation();
    var el = listaEl.querySelector('.litem[data-id="'+id+'"]');
    if(el){ el.classList.add('flash-r');
            setTimeout(function(){ el.classList.add('saindo'); },180); }
    setTimeout(function(){ acao({acao:'remover', item_id:id}); }, 480);
  }

  function desmarcarItem(id){ acao({acao:'marcar', item_id:id, comprado:0}); }

  function toggleCarrinho(){
    var b=document.getElementById('cart-body'), a=document.getElementById('cart-arrow');
    if(!b) return; var ab=b.style.display!=='none';
    b.style.display=ab?'none':'block'; a.textContent=ab?'▸':'▾';
  }

  function finalizarCompra(n){
    if(!confirm('Finalizar a compra? Os '+n+' itens comprados vão pro histórico. Os que faltam continuam na lista.')) return;
    acao({acao:'finalizar'}).then(function(){
      resumoEl.textContent = 'Compra finalizada! ✓';
      setTimeout(function(){ resumoEl.textContent = ''; }, 3000);
    });
  }

  function carregarHistorico(){
    listaEl.innerHTML = '<p class="mut">Carregando histórico...</p>';
    fetch('/painel/compras/historico').then(function(r){return r.json();}).then(function(d){
      var h = d.historico || [];
      if(!h.length){ listaEl.innerHTML = '<p class="mut">Nenhuma compra finalizada ainda. Quando você finalizar uma compra, ela aparece aqui.</p>'; return; }
      var html = '';
      h.forEach(function(c, idx){
        var itensTxt = (c.itens||[]).join(' · ');
        html += '<div class="hist-dia">'
              + '<div class="hist-head" onclick="toggleDia('+idx+')">'
              + '<span><span id="harr'+idx+'">▸</span> '+esc(c.data)+'</span>'
              + '<span class="mut" style="font-size:.8rem">'+c.total_itens+' itens</span></div>'
              + '<div id="hday'+idx+'" class="hist-itens" style="display:none">'+esc(itensTxt)+'</div>'
              + '</div>';
      });
      listaEl.innerHTML = html;
    });
  }

  function toggleDia(idx){
    var b=document.getElementById('hday'+idx), a=document.getElementById('harr'+idx);
    if(!b) return; var ab=b.style.display!=='none';
    b.style.display=ab?'none':'block'; a.textContent=ab?'▸':'▾';
  }

  function render(d){
    _ultimoDados = d;
    if(_aba !== 'lista') return;

    var itens = d.itens || [];
    _itensCache = itens;
    pendentesAtuais = itens.filter(function(i){return !i.comprado;}).map(function(i){return i.descricao;});
    // limpa ajustes orfaos (item removido/comprado nao deve manter ajuste)
    Object.keys(ajustes).forEach(function(k){
      if (pendentesAtuais.indexOf(k) === -1) delete ajustes[k];
    });
    if (!itens.length){
      listaEl.innerHTML = '<p class="mut">A lista está vazia. Adicione itens acima — ou peça pelo WhatsApp/Telegram: <i>"acabou o arroz, bota na lista"</i>.</p>';
      resumoEl.textContent = '';
      btnApagar.style.display = 'none';
      compEl.innerHTML = '';
      return;
    }
    renderItens(itens);
    resumoEl.textContent = d.pendentes + ' item(ns) na lista';
    btnApagar.style.display = d.pendentes ? '' : 'none';
  }

  document.getElementById('form-add').onsubmit = function(e){
    e.preventDefault();
    var inp = document.getElementById('inp-add');
    var v = inp.value.trim();
    if (!v) return;
    inp.value = '';                 // limpa na hora, sutil
    // NAO zera os ajustes ja' feitos - so' remove ajuste de item que nao existe
    // mais (limpeza acontece no render, comparando com pendentesAtuais)
    acao({acao:'add', descricao:v});
  };
  btnApagar.onclick = function(){
    if (!confirm('Apagar a lista inteira? Essa acao nao pode ser desfeita.')) return;
    acao({acao:'apagar_tudo'});
  };

  var prog = null;
  function barraProgresso(){
    var n = pendentesAtuais.length || 1, i = 0;
    compEl.innerHTML = '<div style="margin:.4rem 0"><div class="mut" style="font-size:.82rem" id="prog-txt">Buscando os melhores preços perto de você...</div>'
      + '<div style="height:6px;background:#1d1d1f;border-radius:4px;margin-top:.4rem;overflow:hidden">'
      + '<div id="prog-bar" style="height:100%;width:8%;background:var(--verde-claro);transition:width .4s"></div></div></div>';
    var bar = document.getElementById('prog-bar'), txt = document.getElementById('prog-txt');
    if (prog) clearInterval(prog);
    prog = setInterval(function(){
      i = Math.min(i+1, n);
      if (bar) bar.style.width = Math.min(8 + (i/n)*84, 92) + '%';
      if (txt) txt.textContent = 'Buscando preços... ' + Math.min(i, n) + ' de ' + n + ' itens';
    }, 500);
  }

  var reqToken = 0;
  function carregarPrecos(){
    if (!pendentesAtuais.length){ compEl.innerHTML=''; if(prog) clearInterval(prog); return; }
    barraProgresso();
    var meuToken = ++reqToken;   // so' a chamada mais recente pode escrever
    var params = Object.keys(ajustes).map(function(it){
      return 'ajuste=' + encodeURIComponent(it + '||' + ajustes[it]);
    }).join('&');
    fetch('/painel/compras/precos' + (params ? ('?'+params) : ''))
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (meuToken !== reqToken) return;   // chegou atrasada: ignora
        if (prog) clearInterval(prog);
        var grupos = d.grupos || {}, nomes = Object.keys(grupos), html = '';
        if (!nomes.length){
          html = '<p class="mut" style="font-size:.82rem">📈 Ainda não tenho preços suficientes pra comparar essa lista aqui na sua região.</p>';
        } else {
          nomes.forEach(function(grupo){
            var linhas = grupos[grupo];
            html += '<div style="background:var(--bg);border:1px solid var(--borda);border-radius:10px;padding:1rem;margin:1rem 0">';
            html += '<div style="font-weight:600;margin-bottom:.6rem">'+grupo+' — onde sua cesta sai mais barata</div>';
            linhas.forEach(function(m, i){
              var falta = m.faltando ? (' · faltam '+m.faltando) : ' · completa';
              var endTxt = m.endereco || '';
              if (m.data) endTxt += (endTxt ? ' · ' : '') + 'preço de ' + m.data;
              var end = endTxt ? ('<div class="mut" style="font-size:.72rem">'+esc(endTxt)+'</div>') : '';
              html += '<div style="padding:.45rem 0;'+(i<linhas.length-1?'border-bottom:1px solid #1d1d1f':'')+'">';
              html += '<div style="display:flex;justify-content:space-between">';
              html += '<div>'+(i+1)+'. <b>'+esc(m.mercado)+'</b> <span class="mut" style="font-size:.76rem">· '+m.cobertos+' itens'+falta+'</span>'+end+'</div>';
              html += '<b style="color:var(--verde-claro)">'+m.total+'</b></div>';
              if (m.produtos && m.produtos.length){
                html += '<div style="margin:.3rem 0 0 1.1rem">';
                m.produtos.forEach(function(pr){
                  var quando = (pr.dias === 0 ? 'hoje' : (pr.dias === 1 ? 'ontem' : (pr.dias != null ? 'há '+pr.dias+'d' : '')));
                  var selo = '🧾 cupom' + (quando ? ' · '+quando : '');
                  html += '<div style="font-size:.72rem;display:flex;justify-content:space-between;gap:.5rem">'
                        + '<span>'+esc(pr.descricao)+' <span style="color:var(--verde);font-size:.64rem">'+selo+'</span></span>'
                        + '<span class="mut">'+pr.preco+'</span></div>';
                });
                html += '</div>';
              }
              html += '</div>';
            });
            html += '</div>';
          });
          var fonte = d.fonte === 'sefaz'
            ? 'Preços oficiais de nota fiscal (SEFAZ), atualizados há pouco.'
            : 'Total e ranking contam só os seus cupons (preço real) — melhora a cada compra.';
          html += '<p class="mut" style="font-size:.72rem">'+fonte+'</p>';
        }
        var sc = d.sem_cupom || [];
        if (sc.length){
          html += '<div style="margin-top:.8rem;border:1px solid #2c4459;border-radius:10px;padding:.8rem;background:rgba(111,168,220,.05)">';
          html += '<div style="font-size:.74rem;color:#6fa8dc;margin-bottom:.5rem">📦 Ainda sem cupom · referência do catálogo</div>';
          sc.forEach(function(s){
            html += '<div style="font-size:.72rem;display:flex;justify-content:space-between;gap:.5rem;padding:.2rem 0">'
                  + '<span>'+esc(s.descricao)+' <span style="color:#6fa8dc;font-size:.64rem">📦 '+esc(s.mercado)+'</span></span>'
                  + '<span style="color:#6fa8dc">~ '+s.preco+'</span></div>';
          });
          html += '<div class="mut" style="font-size:.66rem;margin-top:.4rem">Ninguém escaneou cupom desses ainda. Preço online de referência – <b>não entra no total</b>. Quando chegar um cupom, ele assume.</div>';
          html += '</div>';
        }
        html += '<div style="margin-top:.6rem"><div class="mut" style="font-size:.78rem;margin-bottom:.3rem">Não bateu? Ajuste o produto certo:</div><div style="display:flex;flex-wrap:wrap;gap:6px">';
        pendentesAtuais.forEach(function(it){
          var marca = ajustes[it] ? ' ✓' : '';
          html += '<button type="button" class="aj" data-item="'+encodeURIComponent(it)+'" style="margin:0;padding:.25rem .6rem;background:var(--borda);font-size:.74rem;width:auto">'+esc(it)+marca+'</button>';
        });
        html += '</div><div id="aj-opcoes"></div></div>';
        compEl.innerHTML = html;
        Array.prototype.forEach.call(compEl.querySelectorAll('.aj'), function(b){
          b.onclick = function(){ abrirOpcoes(decodeURIComponent(b.getAttribute('data-item'))); };
        });
      }).catch(function(){
        if (prog) clearInterval(prog);
        compEl.innerHTML = '<p class="mut" style="font-size:.8rem">Não consegui buscar os preços agora. Tente recarregar.</p>';
      });
  }

  function abrirOpcoes(item, gtin, produto, tudo){
    var cx = document.getElementById('aj-opcoes');
    cx.innerHTML = '<div class="mut" style="font-size:.78rem;margin-top:.5rem">🔄 Buscando menor preço de "'+esc(item)+'"...</div>';
    var url = '/painel/compras/opcoes?item='+encodeURIComponent(item)
            + (gtin ? ('&gtin='+encodeURIComponent(gtin)) : '')
            + (produto ? ('&produto='+encodeURIComponent(produto)) : '')
            + (tudo ? '&tudo=1' : '');
    fetch(url).then(function(r){ return r.json(); }).then(function(d){
      var ops = d.opcoes || [];
      if (!ops.length){ cx.innerHTML = '<div class="mut" style="font-size:.76rem;margin-top:.5rem">Ainda não tenho preço desse item aqui na sua região.</div>'; return; }
      var html = '<div style="background:var(--bg);border:1px solid var(--borda);border-radius:10px;padding:.8rem;margin-top:.5rem">';

      if (d.nivel === 'lojas'){
        html += '<div style="font-size:.8rem;margin-bottom:.5rem">📍 <b>'+esc(d.produto||item)+'</b> — onde tá mais barato</div>';
        var temCatalogo = false;
        ops.forEach(function(o){
          var preco = 'R$ ' + (o.preco_centavos/100).toFixed(2).replace('.', ',');
          var cupom = (o.fonte === 'cupom');
          var cor = cupom ? 'var(--verde)' : '#6fa8dc';
          var quando = (o.dias === 0 ? 'hoje' : (o.dias === 1 ? 'ontem' : 'há ' + o.dias + 'd'));
          var selo = cupom ? ('🧾 cupom · ' + quando) : '📦 catálogo · preço do site';
          if (!cupom) temCatalogo = true;
          html += '<div style="display:flex;justify-content:space-between;align-items:center;padding:.4rem 0;border-top:1px solid #1f1f20">'
                + '<div style="font-size:.78rem"><div>'+esc(o.mercado)+'</div>'
                + '<div style="font-size:.66rem;margin-top:.12rem;color:'+cor+'">'+selo+'</div></div>'
                + '<div style="font-weight:600;font-size:.86rem;color:'+cor+'">'+preco+'</div></div>';
        });
        if (temCatalogo)
          html += '<div class="mut" style="font-size:.64rem;margin-top:.5rem;line-height:1.5;border-top:1px solid #1f1f20;padding-top:.4rem">'
                + '📦 <b>preço do site</b> (online), não de loja física. '
                + '<b>atacarejo</b> = preço de quantidade · <b>varejo</b> = unidade.</div>';
        html += '<div style="display:flex;gap:.5rem;align-items:center;margin-top:.6rem">'
              + '<button type="button" class="usar-cesta" style="flex:1;background:var(--verde);color:#06281e;border:0;border-radius:8px;padding:.5rem;font-size:.74rem;font-weight:600;cursor:pointer">✓ usar este na cesta</button>'
              + '<button type="button" class="voltar-prod" style="font-size:.72rem;background:transparent;color:#8b97a6;border:0;cursor:pointer;padding:.2rem .4rem">← outros</button>'
              + '</div>';
        html += '</div>';
        cx.innerHTML = html;
        var vb = cx.querySelector('.voltar-prod'); if (vb) vb.onclick = function(){ abrirOpcoes(item); };
        var ub = cx.querySelector('.usar-cesta');
        if (ub) ub.onclick = function(){
          ajustes[item] = d.produto;
          if (typeof carregarPrecos === 'function') carregarPrecos();
          cx.innerHTML = '<div class="mut" style="font-size:.74rem;margin-top:.5rem">✓ Cesta ajustada pra <b>'+esc(d.produto)+'</b>. Recalculando…</div>';
        };
        return;
      }

      var ocultos = d.ocultos || 0;
      html += '<div style="font-size:.8rem;margin-bottom:.4rem">Qual "'+esc(item)+'"? Toque pra ver as lojas</div>';
      if (ocultos > 0)
        html += '<div class="mut" style="font-size:.66rem;margin-bottom:.5rem">Mostrando só produtos de "'+esc(item)+'".</div>';
      ops.forEach(function(o){
        var mn = (o.preco_min_centavos/100).toFixed(2).replace('.', ',');
        var mx = (o.preco_max_centavos/100).toFixed(2).replace('.', ',');
        var faixa = (o.preco_min_centavos === o.preco_max_centavos) ? ('R$ '+mn) : ('R$ '+mn+'–'+mx);
        var nl = o.n_lojas + (o.n_lojas === 1 ? ' loja' : ' lojas');
        var ingr = (o.nucleo === false);
        var badge = ingr ? ' <span style="font-size:.58rem;font-family:monospace;text-transform:uppercase;letter-spacing:.04em;color:#5B6675;border:1px solid #262D38;border-radius:4px;padding:0 4px">ingrediente</span>' : '';
        html += '<button type="button" class="prod" data-gtin="'+(o.gtin||'')+'" data-desc="'+encodeURIComponent(o.descricao)+'" '
              + 'style="display:flex;justify-content:space-between;align-items:center;gap:.5rem;width:100%;text-align:left;margin:.15rem 0;padding:.45rem .6rem;background:var(--card);border:0;border-radius:8px;cursor:pointer;color:#e8edf2'+(ingr?';opacity:.62':'')+'">'
              + '<span style="font-size:.76rem">'+esc(o.descricao)+badge+'<br><span class="mut" style="font-size:.66rem">'+nl+'</span></span>'
              + '<span style="font-weight:600;font-size:.78rem;color:'+(ingr?'#8b97a6':'var(--verde-claro)')+';white-space:nowrap">'+faixa+'</span></button>';
      });
      if (ocultos > 0)
        html += '<button type="button" class="ver-tudo" style="width:100%;background:transparent;border:1px dashed #262D38;color:#8b97a6;border-radius:8px;padding:.5rem;font-size:.72rem;cursor:pointer;margin-top:.3rem">+ ver tudo que tem "'+esc(item)+'" ('+ocultos+')</button>';
      html += '</div>';
      cx.innerHTML = html;
      Array.prototype.forEach.call(cx.querySelectorAll('.prod'), function(b){
        b.onclick = function(){
          var g = b.getAttribute('data-gtin');
          var dsc = decodeURIComponent(b.getAttribute('data-desc'));
          if (g) abrirOpcoes(item, g, null);
          else   abrirOpcoes(item, null, dsc);
        };
      });
      var vt = cx.querySelector('.ver-tudo');
      if (vt) vt.onclick = function(){ abrirOpcoes(item, null, null, true); };
    }).catch(function(){ cx.innerHTML=''; });
  }

  acao({acao:'noop'});

  // Exportar funcoes pra escopo global
  window.setVisao = setVisao;
  window.renderItens = renderItens;
  window.ligarBotoesLinha = ligarBotoesLinha;
  window.carregarPrecos = carregarPrecos;
  window.setAba = setAba;
  window.toggleCarrinho = toggleCarrinho;
  window.finalizarCompra = finalizarCompra;
  window.carregarHistorico = carregarHistorico;
  window.toggleDia = toggleDia;
  window.marcarItem = marcarItem;
  window.removerItem = removerItem;
  window.desmarcarItem = desmarcarItem;
})();

function avisarTerminei(){
  var btn = document.getElementById('btn-avisar');
  btn.disabled = true;
  fetch('/painel/compras/avisar', {method:'POST'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      document.getElementById('aviso-msg').textContent = d.msg || 'Avisado!';
      setTimeout(function(){ btn.disabled = false; }, 3000);
    })
    .catch(function(){ btn.disabled = false; });
}
</script>
{% endblock %}"""

_COMPRA_REVISAR = """{% extends "base" %}{% block conteudo %}
<div class="card larga"><a href="/painel/produtos/abastecimento" style="color:var(--verde-claro);display:inline-block;margin-bottom:1rem">← voltar</a>
<h2 style="margin-top:0">🛒 Compra #{{ compra.id }}</h2>
<p class="mut">Origem: <strong>{{ compra.origem_nome or '-' }}</strong> · {{ compra.data }} · status: <strong>{{ compra.status }}</strong></p>
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if compra.status == 'rascunho' %}
<div style="background:#1c1c1f;border:1px solid var(--borda);border-radius:8px;padding:1.2rem;margin:1.5rem 0">
<h4 style="margin-top:0">Adicionar item</h4>
{% if produtos %}
<form method="post" action="/painel/produtos/abastecimento/{{ compra.id }}/item" style="display:grid;gap:1rem">
<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">
<div><label>Produto</label><select name="produto_id" required style="width:100%"><option value="">Selecione um produto</option>
{% for p in produtos %}<option value="{{ p.id }}">{{ p.nome }} ({{ p.unidade }})</option>{% endfor %}
</select></div>
<div><label>Quantidade</label><input name="quantidade" type="number" step="0.001" required placeholder="50"></div>
</div>
<div><label>Custo unitário (R$)</label><input name="custo_unit" placeholder="4,00" required></div>
<button style="background:var(--verde);color:#fff;padding:.6rem 1rem;border:0;border-radius:6px;cursor:pointer;width:100%;font-weight:500">Adicionar item</button>
</form>
{% else %}
<p class="mut">Você ainda não tem produtos no catálogo. <a href="/painel/produtos/abastecimento" style="color:var(--verde-claro)">Crie um produto</a> primeiro.</p>
{% endif %}
</div>
{% endif %}
<h4>Itens da compra</h4>
{% if itens %}
<table style="width:100%;border-collapse:collapse;font-size:.9rem;margin-bottom:1.5rem">
<tr style="border-bottom:2px solid var(--borda);color:#888"><th style="text-align:left;padding:.6rem;font-weight:500">Produto</th><th style="text-align:center;padding:.6rem;font-weight:500">Qtd</th><th style="text-align:right;padding:.6rem;font-weight:500">Custo un.</th><th style="text-align:right;padding:.6rem;font-weight:500">Subtotal</th></tr>
{% for i in itens %}
<tr style="border-top:1px solid var(--borda)"><td style="padding:.6rem">{{ i.produto_nome or i.descricao }}</td><td style="text-align:center;padding:.6rem">{{ i.quantidade }} {{ i.unidade }}</td><td style="text-align:right;padding:.6rem">R$ {{ (i.custo_unit_centavos/100)|n2 }}</td><td style="text-align:right;padding:.6rem;font-weight:500">R$ {{ (i.quantidade * i.custo_unit_centavos/100)|n2 }}</td></tr>
{% endfor %}
<tr style="border-top:2px solid var(--borda);background:var(--borda)"><td colspan="3" style="text-align:right;padding:.6rem;font-weight:600">Total:</td><td style="text-align:right;padding:.6rem;font-weight:600">R$ {{ (compra.total_centavos/100)|n2 }}</td></tr>
</table>
{% if compra.status == 'rascunho' %}
<form method="post" action="/painel/produtos/abastecimento/{{ compra.id }}/confirmar">
<button style="background:var(--verde);color:#fff;padding:.7rem 1rem;border:0;border-radius:8px;cursor:pointer;width:100%;font-weight:600;font-size:1rem;margin-bottom:1rem">✓ Confirmar compra (dar entrada no estoque)</button>
</form>
<p class="mut" style="font-size:.85rem;margin:0">Ao confirmar, os itens dão entrada no estoque e o custo médio é recalculado. Não dá pra editar depois.</p>
{% else %}
<div style="background:#2a3a2a;border:1px solid var(--verde);color:var(--verde-claro);padding:.8rem;border-radius:6px;text-align:center">✓ Compra confirmada em {{ compra.data }}</div>
{% endif %}
{% else %}
<p class="mut" style="background:var(--borda);padding:1rem;border-radius:6px">Nenhum item ainda. Adicione acima.</p>
{% endif %}
</div>
{% endblock %}"""

_LOJA = """<!doctype html>
<html lang="pt-br"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ fornecedor.nome }} · Zaq</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  html,body{margin:0;padding:0}
  body{background:#eef0ec;font-family:'Poppins',system-ui,-apple-system,sans-serif;color:#23301f;-webkit-font-smoothing:antialiased}
  *{box-sizing:border-box}
  .noscroll{scrollbar-width:none;-ms-overflow-style:none}
  .noscroll::-webkit-scrollbar{display:none}
  .sz-body{display:grid;grid-template-columns:186px 1fr 312px}
  .sz-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:13px}
  .sz-shelf{display:flex;gap:12px;overflow-x:auto;padding-bottom:6px}
  .sz-shelfcard{flex:0 0 152px;background:#fff;border:1px solid #e8ece5;border-radius:14px;overflow:hidden;box-shadow:0 1px 3px rgba(20,40,15,.05)}
  .sz-foto{background:#eef3ea;background-size:cover;background-position:center;display:flex;align-items:center;justify-content:center}
  .sz-chips{display:none}
  .sz-tab{display:none}
  .sz-tab.on{display:block}
  .qadd{position:absolute;bottom:6px;right:6px;width:32px;height:32px;border-radius:50%;background:#2f7d32;color:#fff;border:0;cursor:pointer;font-size:19px;line-height:1;box-shadow:0 2px 6px rgba(0,0,0,.22)}
  .sz-cart{position:sticky;top:12px}
  @media (max-width:900px){
    .sz-body{grid-template-columns:1fr}
    .sz-nav{display:none !important}
    .sz-cart{position:sticky;top:auto;bottom:0;background:#fff;border:0;border-top:2px solid #31772f !important;border-radius:14px 14px 0 0 !important;box-shadow:0 -10px 26px rgba(0,0,0,.12);max-height:56vh;overflow-y:auto;z-index:30}
    .sz-chips{display:flex !important}
    .sz-vitrine{border-right:0 !important;padding:16px 6px !important}
  }
  @media (max-width:520px){ .sz-grid{grid-template-columns:1fr 1fr} }
  .sz-cover{height:170px;background-position:center;background-size:cover}
  .sz-logo{width:92px;height:92px;border-radius:22px;font-size:34px}
  .sz-idrow{margin-top:-40px}
  @media (max-width:640px){
    .sz-cover{height:118px}
    .sz-logo{width:70px;height:70px;border-radius:16px;font-size:24px}
    .sz-idrow{margin-top:-28px;gap:12px}
    .sz-idrow .sz-nome{font-size:19px !important}
  }
</style>
</head><body>

{% macro card_full(p) %}
{% set eff = p.preco_promo_centavos if (p.em_promo and p.preco_promo_centavos) else p.preco_venda_centavos %}
<div class="prodcard" data-pid="{{ p.id }}" data-nome="{{ p.nome }}" data-preco="{{ eff }}" data-unidade="{{ p.unidade }}" style="background:#fff;border:1px solid #e8ece5;border-radius:15px;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 1px 3px rgba(20,40,15,.05)">
  <div style="position:relative">
    <div class="sz-foto" style="height:118px;{% if p.foto_url %}background-image:url('{{ p.foto_url }}'){% endif %}">{% if not p.foto_url %}<span style="font-size:34px;opacity:.5">🥬</span>{% endif %}</div>
    {% if p.em_promo and p.preco_promo_centavos %}<span style="position:absolute;top:8px;left:8px;background:#f48b22;color:#fff;font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px">OFERTA</span>{% endif %}
  </div>
  <div style="padding:12px;display:flex;flex-direction:column;flex:1">
    <div style="font-size:14px;color:#2f7d32;font-weight:600;line-height:1.25">{{ p.nome }}</div>
    {% if p.descricao_curta %}<div style="font-size:11px;color:#8a938a;margin:2px 0 0">{{ p.descricao_curta }}</div>{% endif %}
    <div style="margin-top:auto;padding-top:12px">
      <div style="white-space:nowrap">
        {% if p.em_promo and p.preco_promo_centavos %}<span style="font-size:11px;color:#a3aca0;text-decoration:line-through;margin-right:4px">R$ {{ (p.preco_venda_centavos/100)|n2 }}</span>{% endif %}
        <span style="font-size:15px;color:#2f7d32;font-weight:700">R$ {{ (eff/100)|n2 }}</span><span style="font-size:11px;color:#8a938a;font-weight:400">/{{ p.unidade }}</span>
      </div>
      {% if fornecedor.desconto_pix_pct %}<div style="font-size:11px;color:#31772f;margin-top:2px">no Pix <strong>R$ {{ (eff*(1-(fornecedor.desconto_pix_pct or 0)/100)/100)|n2 }}</strong></div>{% endif %}
      <div style="display:flex;align-items:center;justify-content:space-between;gap:4px;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:9px;padding:3px;margin-top:10px">
          <button onclick="szDec({{ p.id }},'{{ p.unidade }}')" style="width:28px;height:28px;border-radius:7px;background:#e6e9e2;color:#3a463a;border:0;font-size:17px;cursor:pointer;line-height:1;flex-shrink:0">−</button>
          <span id="sel-{{ p.id }}" style="font-size:13px;color:#23301f;font-weight:600;text-align:center">{% if p.unidade in ['unidade','duzia','maco','bandeja','pacote'] %}1{% else %}0,5{% endif %}</span>
          <button onclick="szInc({{ p.id }},'{{ p.unidade }}')" style="width:28px;height:28px;border-radius:7px;background:#e6e9e2;color:#3a463a;border:0;font-size:17px;cursor:pointer;line-height:1;flex-shrink:0">+</button>
      </div>
      <button onclick="szAdd({{ p.id }},'{{ p.unidade }}')" style="display:flex;align-items:center;justify-content:center;gap:7px;width:100%;margin-top:7px;height:36px;border-radius:9px;background:#2f7d32;color:#fff;border:0;cursor:pointer;font-size:12.5px;font-weight:700;font-family:inherit"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="21" r="1"></circle><circle cx="20" cy="21" r="1"></circle><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"></path></svg>Adicionar</button>
      <div data-incart="{{ p.id }}" style="display:none;font-size:10.5px;color:#31772f;font-weight:600;margin-top:6px">✓ <span></span> no carrinho</div>
    </div>
  </div>
</div>
{% endmacro %}

{% macro card_shelf(p) %}
{% set eff = p.preco_promo_centavos if (p.em_promo and p.preco_promo_centavos) else p.preco_venda_centavos %}
<div class="prodcard sz-shelfcard" data-pid="{{ p.id }}" data-nome="{{ p.nome }}" data-preco="{{ eff }}" data-unidade="{{ p.unidade }}">
  <div style="position:relative">
    <div class="sz-foto" style="height:96px;{% if p.foto_url %}background-image:url('{{ p.foto_url }}'){% endif %}">{% if not p.foto_url %}<span style="font-size:28px;opacity:.5">🥬</span>{% endif %}</div>
    {% if p.em_promo and p.preco_promo_centavos %}<span style="position:absolute;top:6px;left:6px;background:#f48b22;color:#fff;font-size:9px;font-weight:700;padding:2px 7px;border-radius:20px">OFERTA</span>{% endif %}
    <button class="qadd" onclick="szQuick({{ p.id }},'{{ p.unidade }}')" title="Adicionar">+</button>
  </div>
  <div style="padding:9px 10px">
    <div style="font-size:12.5px;color:#2f7d32;font-weight:600;line-height:1.2">{{ p.nome }}</div>
    <div style="font-size:13px;color:#2f7d32;font-weight:700;margin-top:4px">R$ {{ (eff/100)|n2 }}<span style="font-size:10px;color:#8a938a;font-weight:400">/{{ p.unidade }}</span></div>
    {% if fornecedor.desconto_pix_pct %}<div style="font-size:10px;color:#31772f">no Pix R$ {{ (eff*(1-(fornecedor.desconto_pix_pct or 0)/100)/100)|n2 }}</div>{% endif %}
    <div data-incart="{{ p.id }}" style="display:none;font-size:9.5px;color:#31772f;font-weight:600;margin-top:2px">✓ <span></span></div>
  </div>
</div>
{% endmacro %}

<div style="max-width:1180px;margin:0 auto;background:#fff;min-height:100vh;box-shadow:0 0 40px rgba(0,0,0,.06)">

  <div style="padding:12px 22px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #e7eae5">
    <span style="display:flex;align-items:center;gap:8px">
      <svg width="22" height="22" viewBox="0 0 64 64" fill="none"><path d="M16 18 H44 L18 46 H46" stroke="#0f7d5c" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"></path><path d="M47 10 L49 16 L55 18 L49 20 L47 26 L45 20 L39 18 L45 16 Z" fill="#f48b22"></path></svg>
      <span style="color:#1a2417;font-weight:700;font-size:17px;letter-spacing:-.6px">zaq</span>
    </span>
    {% if logado %}<a href="/painel" style="color:#31772f;font-size:13px;font-weight:500;text-decoration:none">Meu painel</a>
    {% else %}<a href="/login" style="color:#31772f;font-size:13px;font-weight:500;text-decoration:none">Entrar · Criar conta</a>{% endif %}
  </div>

  <div style="position:relative">
    <div class="sz-cover" style="background:{% if fornecedor.banner_url %}url('{{ fornecedor.banner_url }}') center/cover{% elif fornecedor.banner_cor %}{{ fornecedor.banner_cor }}{% else %}linear-gradient(120deg,#3a7d3a,#245c24){% endif %}"></div>
    <span style="position:absolute;top:14px;right:16px;background:rgba(0,0,0,.45);color:#eafae6;font-size:12px;font-weight:600;padding:6px 13px;border-radius:20px;display:flex;align-items:center;gap:7px;backdrop-filter:blur(4px)">
      <span style="width:8px;height:8px;border-radius:50%;background:#8ff0a0"></span>Aberto agora
    </span>
  </div>

  <div style="padding:0 22px">
    <div class="sz-idrow" style="display:flex;align-items:flex-end;gap:16px;position:relative">
      <div class="sz-logo" style="box-shadow:0 6px 18px rgba(0,0,0,.18);border:4px solid #fff;flex-shrink:0;background:{% if fornecedor.logo_url %}url('{{ fornecedor.logo_url }}') center/cover{% else %}#eef6ea{% endif %};display:flex;align-items:center;justify-content:center;font-weight:700;color:#31772f">{% if not fornecedor.logo_url %}{{ fornecedor.nome[0]|upper }}{% endif %}</div>
      <div style="padding-bottom:6px">
        <div style="display:flex;align-items:center;gap:9px;flex-wrap:wrap">
          <span class="sz-nome" style="font-size:24px;font-weight:700;color:#1a2417;letter-spacing:-.3px">{{ fornecedor.nome }}</span>
          {% if fornecedor.verificado %}<span style="display:inline-flex;align-items:center;gap:4px;background:#eef6ea;color:#31772f;font-size:11px;font-weight:700;padding:3px 9px;border-radius:20px">✓ Produtor verificado</span>{% endif %}
        </div>
        {% if fornecedor.bio %}<div style="font-size:13px;color:#6b7669;margin-top:5px">{{ fornecedor.bio }}</div>{% endif %}
      </div>
    </div>

    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:16px">
      <span style="display:inline-flex;align-items:center;gap:6px;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:20px;padding:7px 13px;font-size:12px;color:#3a463a;font-weight:500">🕐 40–60 min</span>
      <span style="display:inline-flex;align-items:center;gap:6px;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:20px;padding:7px 13px;font-size:12px;color:#3a463a;font-weight:500">Entrega R$ {{ "%.0f"|format((fornecedor.taxa_entrega_centavos or 0)/100) }}</span>
      <span style="display:inline-flex;align-items:center;gap:6px;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:20px;padding:7px 13px;font-size:12px;color:#3a463a;font-weight:500">Mín. R$ {{ "%.0f"|format((fornecedor.pedido_minimo_centavos or 0)/100) }}</span>
      {% if fornecedor.endereco %}<span style="display:inline-flex;align-items:center;gap:6px;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:20px;padding:7px 13px;font-size:12px;color:#3a463a;font-weight:500">📍 {{ fornecedor.endereco }}</span>{% endif %}
      <span style="display:inline-flex;align-items:center;gap:6px;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:20px;padding:7px 13px;font-size:12px;color:#3a463a;font-weight:500">💳 Pix · Cartão · Dinheiro</span>
    </div>
  </div>

  <div style="display:flex;gap:4px;padding:22px 22px 0;border-bottom:1px solid #e7eae5;overflow-x:auto" class="noscroll">
    <button class="tabbtn on" data-tab="comprar" onclick="szTab('comprar')" style="font-size:14px;font-weight:600;padding:12px 20px;border:none;border-bottom:2px solid #2f7d32;background:none;color:#2f7d32;cursor:pointer;white-space:nowrap;font-family:inherit">Comprar</button>
    <button class="tabbtn" data-tab="assinar" onclick="szTab('assinar')" style="font-size:14px;font-weight:600;padding:12px 20px;border:none;border-bottom:2px solid transparent;background:none;color:#8a938a;cursor:pointer;white-space:nowrap;font-family:inherit">Assinar cesta</button>
    <button class="tabbtn" data-tab="promocoes" onclick="szTab('promocoes')" style="font-size:14px;font-weight:600;padding:12px 20px;border:none;border-bottom:2px solid transparent;background:none;color:#8a938a;cursor:pointer;white-space:nowrap;font-family:inherit">🏷️ Promoções</button>
    <button class="tabbtn" data-tab="pedidos" onclick="szTab('pedidos')" style="font-size:14px;font-weight:600;padding:12px 20px;border:none;border-bottom:2px solid transparent;background:none;color:#8a938a;cursor:pointer;white-space:nowrap;font-family:inherit">📦 Meus pedidos</button>
  </div>

  <!-- ============ COMPRAR ============ -->
  <div class="sz-tab on" data-tab="comprar">
    <div class="sz-body">
      <nav class="sz-nav" style="position:sticky;top:12px;align-self:start;border-right:1px solid #e7eae5;padding:22px 16px 22px 22px">
        <div style="font-size:11px;color:#31772f;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px">Categorias</div>
        <button onclick="szCat('tudo',this)" class="catbtn on" style="display:flex;align-items:center;gap:10px;width:100%;text-align:left;font-size:13.5px;color:#3a463a;background:#eef6ea;border:0;border-radius:9px;padding:9px 11px;cursor:pointer;font-family:inherit;margin-bottom:3px">🧺 Tudo</button>
        {% for cat, itens in secoes %}<button onclick="szCat('{{ cat }}',this)" class="catbtn" style="display:flex;align-items:center;gap:10px;width:100%;text-align:left;font-size:13.5px;color:#3a463a;background:none;border:0;border-radius:9px;padding:9px 11px;cursor:pointer;font-family:inherit;margin-bottom:3px">🥕 {{ cat|capitalize }}</button>{% endfor %}
      </nav>

      <div class="sz-vitrine" style="padding:22px 24px;border-right:1px solid #e7eae5;min-width:0">
        <div style="position:relative;display:flex;align-items:center;margin-bottom:18px">
          <svg style="position:absolute;left:14px;pointer-events:none" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#9aa398" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.3-4.3"></path></svg>
          <input type="text" oninput="szBusca(this.value)" placeholder="Buscar produto" style="width:100%;padding:11px 14px 11px 40px;background:#f4f6f2;border:1px solid #e2e7dd;color:#23301f;border-radius:11px;font-size:13.5px;font-family:inherit;outline:none">
        </div>

        <div class="sz-chips noscroll" style="gap:7px;margin-bottom:16px;overflow-x:auto">
          <button onclick="szCat('tudo',this)" style="font-size:12px;padding:7px 15px;border-radius:20px;background:#eef6ea;color:#31772f;border:1px solid #cfe6c6;white-space:nowrap;cursor:pointer;font-family:inherit;flex-shrink:0">Tudo</button>
          {% for cat, itens in secoes %}<button onclick="szCat('{{ cat }}',this)" style="font-size:12px;padding:7px 15px;border-radius:20px;background:#f4f6f2;color:#3a463a;border:1px solid #e2e7dd;white-space:nowrap;cursor:pointer;font-family:inherit;flex-shrink:0">{{ cat|capitalize }}</button>{% endfor %}
        </div>

        {% if sazonais %}
        <div class="szsec" data-cat="_sazonal" style="margin-bottom:26px">
          <div style="font-size:14px;font-weight:700;color:#1a2417;margin-bottom:12px">🌱 Tá na época</div>
          <div class="sz-shelf noscroll">
            {% for p in sazonais %}{{ card_shelf(p) }}{% endfor %}
          </div>
        </div>
        {% endif %}

        {% if mais_vendidos %}
        <div class="szsec" data-cat="_hot" style="margin-bottom:26px">
          <div style="font-size:14px;font-weight:700;color:#1a2417;margin-bottom:12px">🔥 Mais vendidos</div>
          <div class="sz-shelf noscroll">
            {% for p in mais_vendidos %}{{ card_shelf(p) }}{% endfor %}
          </div>
        </div>
        {% endif %}

        {% for cat, itens in secoes %}
        <div class="szsec" data-cat="{{ cat }}" style="margin-bottom:30px">
          <div style="font-size:13px;color:#31772f;font-weight:600;margin:4px 0 14px;display:flex;align-items:center;gap:8px">🥬 {{ cat|capitalize }}</div>
          <div class="sz-grid">
            {% for p in itens %}{{ card_full(p) }}{% endfor %}
          </div>
        </div>
        {% endfor %}

        {% if fornecedor.sobre %}
        <div style="background:#f7f9f5;border:1px solid #e7eae5;border-radius:14px;padding:16px 18px;margin-top:8px">
          <div style="font-size:13px;font-weight:700;color:#1a2417">Sobre a banca</div>
          <p style="font-size:13px;color:#6b7669;line-height:1.55;margin:8px 0 14px">{{ fornecedor.sobre }}</p>
          {% if fornecedor.whatsapp_loja %}<a href="https://wa.me/55{{ fornecedor.whatsapp_loja|replace('(','')|replace(')','')|replace(' ','')|replace('-','')|replace('+','') }}" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:8px;background:#25d366;color:#fff;text-decoration:none;border-radius:10px;padding:10px 18px;font-size:13px;font-weight:600">💬 Falar no WhatsApp</a>{% endif %}
        </div>
        {% endif %}
      </div>

      <div class="sz-cart" id="sz-cart" style="padding:20px;background:#fff;border:1px solid #e8ece5;border-radius:14px;align-self:start"></div>
    </div>
  </div>

  <!-- ============ PROMOÇÕES ============ -->
  <div class="sz-tab" data-tab="promocoes">
    <div style="padding:24px 22px 44px">
      <div style="font-size:13px;color:#f48b22;font-weight:700;text-transform:uppercase;letter-spacing:.04em">🏷️ Ofertas de hoje</div>
      <p style="font-size:13px;color:#6b7669;margin:6px 0 18px">Produtos com preço promocional – por tempo limitado.</p>
      {% set ns = namespace(has=false) %}
      {% for cat, itens in secoes %}{% for p in itens %}{% if p.em_promo and p.preco_promo_centavos %}{% set ns.has = true %}{% endif %}{% endfor %}{% endfor %}
      {% if ns.has %}
      <div class="sz-grid">{% for cat, itens in secoes %}{% for p in itens %}{% if p.em_promo and p.preco_promo_centavos %}{{ card_full(p) }}{% endif %}{% endfor %}{% endfor %}</div>
      {% else %}
      <div style="text-align:center;padding:40px 20px;color:#8a938a;font-size:14px">Nenhuma oferta ativa no momento. Volte logo! 🥕</div>
      {% endif %}
    </div>
  </div>

  <!-- ============ MEUS PEDIDOS (Fase 2 preenche 'pedidos') ============ -->
  <div class="sz-tab" data-tab="pedidos">
    <div style="max-width:720px;padding:26px 22px 44px">
      {% if pedidos %}
      {% for o in pedidos %}
      <div style="background:#fff;border:1px solid #e8ece5;border-radius:18px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(20,40,15,.05)">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
          <div>
            <span style="font-size:16px;font-weight:700;color:#1a2417">Pedido {{ o.code }}</span>
            <span style="font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;background:#eef6ea;color:#31772f;margin-left:8px">{{ o.status }}</span>
            <div style="font-size:12.5px;color:#8a938a;margin-top:3px">{{ o.data }}</div>
          </div>
          <div style="text-align:right"><div style="font-size:12px;color:#8a938a">Total</div><div style="font-size:18px;font-weight:700;color:#2f7d32">R$ {{ ((o.total_centavos or 0)/100)|n2 }}</div></div>
        </div>
      </div>
      {% endfor %}
      {% else %}
      <div style="text-align:center;padding:50px 20px;color:#8a938a;font-size:14px">Você ainda não fez pedidos nesta banca.<br>Adicione produtos e faça seu primeiro! 🛒</div>
      {% endif %}
    </div>
  </div>

  <!-- ============ ASSINAR CESTA ============ -->
  <div class="sz-tab" data-tab="assinar">
    <div style="padding:26px 22px 44px;max-width:640px">
      <div style="font-size:13px;color:#31772f;line-height:1.55;background:#eef6ea;border:1px solid #d8ead2;border-radius:12px;padding:14px 16px;margin-bottom:22px">🧺 Uma cesta fresca na sua porta toda semana. <strong>Sem fidelidade</strong> – cancele quando quiser.</div>
      <form method="post" action="/f/{{ fornecedor.slug }}/assinar">
        <div style="font-size:13px;color:#31772f;font-weight:600;margin-bottom:12px">1. Escolha o tamanho</div>
        {% for t in tamanhos %}
        <label onclick="szTam(this)" class="tamopt{% if escolha.tamanho_id == t.id %} sel{% endif %}" style="display:block;background:#fff;border:1px solid #e7eae5;border-radius:13px;padding:14px;margin-bottom:9px;cursor:pointer">
          <input type="radio" name="tamanho_id" value="{{ t.id }}" required style="display:none" {% if escolha.tamanho_id == t.id %}checked{% endif %}>
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div style="display:flex;align-items:center;gap:12px">
              <div style="width:44px;height:44px;border-radius:11px;background:#eef6ea;display:flex;align-items:center;justify-content:center;font-size:22px">🧺</div>
              <div>
                <div style="font-size:14px;color:#1a2417;font-weight:600">{{ t.nome }}</div>
                <div style="font-size:11px;color:#8a938a">{{ t.qtd_frutas }} frutas · {{ t.qtd_legumes }} legumes · {{ t.qtd_verduras }} verduras · {{ t.qtd_temperos }} temperos</div>
              </div>
            </div>
            <div style="font-size:15px;color:#2f7d32;font-weight:700">R$ {{ "%.0f"|format(t.preco_centavos/100) }}</div>
          </div>
        </label>
        {% endfor %}
        <div style="font-size:13px;color:#31772f;font-weight:600;margin:20px 0 12px">2. Com que frequência?</div>
        <div style="display:flex;gap:8px;margin-bottom:20px">
          <label class="freqopt" onclick="szFreq(this)" style="flex:1;text-align:center;background:#fff;color:#3a463a;border:1px solid #e7eae5;border-radius:11px;padding:11px;font-size:12.5px;cursor:pointer"><input type="radio" name="frequencia" value="semanal" style="display:none" {% if (escolha.frequencia or 'semanal')=='semanal' %}checked{% endif %}>Semanal</label>
          <label class="freqopt" onclick="szFreq(this)" style="flex:1;text-align:center;background:#fff;color:#3a463a;border:1px solid #e7eae5;border-radius:11px;padding:11px;font-size:12.5px;cursor:pointer"><input type="radio" name="frequencia" value="quinzenal" style="display:none" {% if escolha.frequencia=='quinzenal' %}checked{% endif %}>Quinzenal</label>
          <label class="freqopt" onclick="szFreq(this)" style="flex:1;text-align:center;background:#fff;color:#3a463a;border:1px solid #e7eae5;border-radius:11px;padding:11px;font-size:12.5px;cursor:pointer"><input type="radio" name="frequencia" value="mensal" style="display:none" {% if escolha.frequencia=='mensal' %}checked{% endif %}>Mensal</label>
        </div>
        <details style="margin-bottom:20px">
          <summary style="font-size:12px;color:#8a938a;cursor:pointer">Algum item que você não quer receber? (opcional)</summary>
          <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px">
            {% for p in produtos %}<label style="font-size:11px;color:#3a463a;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:16px;padding:4px 11px;cursor:pointer"><input type="checkbox" name="restricoes" value="{{ p.id }}" style="margin-right:4px" {% if p.id in (escolha.restricoes or []) %}checked{% endif %}>{{ p.nome }}</label>{% endfor %}
          </div>
        </details>
        <button type="submit" style="display:block;width:100%;background:#2f7d32;color:#fff;border:0;border-radius:11px;padding:14px;font-size:14px;font-weight:700;cursor:pointer;font-family:inherit">Assinar cesta</button>
        <div style="text-align:center;font-size:11px;color:#8a938a;margin-top:8px">Você só paga 4 dias antes de cada entrega</div>
      </form>
    </div>
  </div>

</div>

<script>
var SLUG="{{ fornecedor.slug }}";
var PIXPCT={{ fornecedor.desconto_pix_pct or 0 }};
var FRETEG={{ fornecedor.frete_gratis_acima_centavos or 0 }};
var CART={{ carrinho_json|safe }};
var SEL={};
function inc(u){return ['unidade','duzia','maco','bandeja','pacote'].indexOf(u)>=0?1:0.5;}
function brl(c){return 'R$ '+(c/100).toFixed(2).replace('.',',');}
function pix(c){return c*(1-PIXPCT/100);}

function szTab(q){
  document.querySelectorAll('.sz-tab').forEach(function(t){t.classList.toggle('on',t.dataset.tab===q);});
  document.querySelectorAll('.tabbtn').forEach(function(b){var on=b.dataset.tab===q;b.style.color=on?'#2f7d32':'#8a938a';b.style.borderBottomColor=on?'#2f7d32':'transparent';});
}
function szCat(cat,el){
  document.querySelectorAll('.szsec').forEach(function(s){var c=s.dataset.cat;s.style.display=(cat==='tudo'||c===cat||c==='_sazonal'||c==='_hot')?'block':'none';});
  document.querySelectorAll('.catbtn').forEach(function(b){b.classList.remove('on');b.style.background='none';});
  if(el&&el.classList.contains('catbtn')){el.classList.add('on');el.style.background='#eef6ea';}
}
function szBusca(q){
  q=(q||'').toLowerCase().trim();
  document.querySelectorAll('.prodcard').forEach(function(c){
    var m=(c.dataset.nome||'').toLowerCase().indexOf(q)>=0;
    c.style.display=(!q||m)?'':'none';
  });
}
function szTam(el){document.querySelectorAll('.tamopt').forEach(function(t){t.style.border='1px solid #e7eae5';t.style.background='#fff';});el.style.border='2px solid #2f7d32';el.style.background='#eef6ea';var r=el.querySelector('input');if(r)r.checked=true;}
function szFreq(el){document.querySelectorAll('.freqopt').forEach(function(f){f.style.border='1px solid #e7eae5';f.style.background='#fff';f.style.color='#3a463a';});el.style.border='2px solid #2f7d32';el.style.background='#eef6ea';el.style.color='#2f7d32';var r=el.querySelector('input');if(r)r.checked=true;}
function szInitAssinar(){
  var t=document.querySelector('.tamopt input:checked');if(t)szTam(t.parentNode);
  var f=document.querySelector('.freqopt input:checked');if(f)szFreq(f.parentNode);
}

function selQ(pid,u){if(SEL[pid]==null)SEL[pid]=inc(u);return SEL[pid];}
function szInc(pid,u){SEL[pid]=selQ(pid,u)+inc(u);document.getElementById('sel-'+pid).textContent=fmtQ(SEL[pid]);}
function szDec(pid,u){var v=selQ(pid,u)-inc(u);SEL[pid]=v<inc(u)?inc(u):v;document.getElementById('sel-'+pid).textContent=fmtQ(SEL[pid]);}
function fmtQ(v){return (v%1===0)?v:v.toFixed(1).replace('.',',');}

var REQSEQ=0;
function syncSrv(p){
  var my=++REQSEQ;
  p.then(function(r){return r.json();}).then(function(d){
    if(my===REQSEQ && d && d.itens!==undefined){CART=d;renderCart();syncCards();}
  }).catch(function(e){console.error(e);});
}
function prodInfo(pid){
  var el=document.querySelector('[data-pid="'+pid+'"]');
  if(!el)return null;
  return {nome:el.getAttribute('data-nome')||'Produto',
          preco:parseInt(el.getAttribute('data-preco'),10)||0,
          unidade:el.getAttribute('data-unidade')||''};
}
function addLocal(pid,q){
  var inf=prodInfo(pid); if(!inf)return;
  CART.itens=CART.itens||[];
  var it=null;for(var i=0;i<CART.itens.length;i++){if(CART.itens[i].produto_id===pid){it=CART.itens[i];break;}}
  if(it){it.qtd=(it.qtd||0)+q;it.total=Math.round(inf.preco*it.qtd);}
  else{CART.itens.push({produto_id:pid,nome:inf.nome,unidade:inf.unidade,qtd:q,total:Math.round(inf.preco*q)});}
  recalcLocal();renderCart();syncCards();
}
function szAdd(pid,u){
  var q=selQ(pid,u);
  addLocal(pid,q);
  syncSrv(fetch('/f/'+SLUG+'/carrinho/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({produto_id:pid,quantidade:q})}));
}
function szQuick(pid,u){
  addLocal(pid,inc(u));
  syncSrv(fetch('/f/'+SLUG+'/carrinho/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({produto_id:pid,quantidade:inc(u)})}));
}

function syncCards(){
  document.querySelectorAll('[data-incart]').forEach(function(el){el.style.display='none';});
  (CART.itens||[]).forEach(function(it){
    document.querySelectorAll('[data-incart="'+it.produto_id+'"]').forEach(function(el){
      el.style.display='block';el.querySelector('span').textContent=fmtQ(it.qtd)+it.unidade;
    });
  });
}

function recalcLocal(){
  var sub=0;(CART.itens||[]).forEach(function(i){sub+=i.total||0;});
  CART.subtotal=sub; CART.total=sub+(CART.itens&&CART.itens.length?(CART.taxa||0):0);
}
function szRemove(pid){
  CART.itens=(CART.itens||[]).filter(function(i){return i.produto_id!==pid;});
  recalcLocal(); renderCart(); syncCards();
  syncSrv(fetch('/f/'+SLUG+'/carrinho/qtd',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({produto_id:pid,quantidade:0})}));
}
function szLimpar(){
  CART.itens=[]; recalcLocal(); renderCart(); syncCards();
  syncSrv(fetch('/f/'+SLUG+'/carrinho/limpar',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}));
}
function renderCart(){
  var box=document.getElementById('sz-cart');
  var n=(CART.itens||[]).length;
  var h='<div style="font-size:15px;color:#1a2417;font-weight:700;margin-bottom:14px;display:flex;align-items:center;gap:9px"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#2f7d32" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="21" r="1"></circle><circle cx="20" cy="21" r="1"></circle><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"></path></svg>Seu pedido';
  if(n)h+='<span style="background:#31772f;color:#fff;font-size:11px;font-weight:700;padding:1px 8px;border-radius:12px;margin-left:auto">'+n+'</span>';
  h+='</div>';
  if(n)h+='<div style="text-align:right;margin:-10px 0 10px"><button onclick="szLimpar()" style="border:0;background:none;color:#a3aca0;font-size:11px;cursor:pointer;text-decoration:underline;font-family:inherit">limpar carrinho</button></div>';
  if(!n){h+='<div style="font-size:13px;color:#8a938a;line-height:1.5">Carrinho vazio. Adicione produtos!</div>';box.innerHTML=h;return;}
  var sub=CART.subtotal||0;
  if(FRETEG>0){
    var falta=FRETEG-sub, gratis=falta<=0, pct=Math.min(100,Math.round(sub/FRETEG*100));
    h+='<div style="margin-bottom:14px">';
    h+=gratis?'<div style="font-size:12px;color:#31772f;font-weight:600;margin-bottom:6px">🚚 Você ganhou frete grátis!</div>':'<div style="font-size:11.5px;color:#6b7669;margin-bottom:6px">Faltam <strong style="color:#31772f">'+brl(falta)+'</strong> pro frete grátis</div>';
    h+='<div style="height:7px;background:#e6ebe2;border-radius:5px;overflow:hidden"><div style="height:100%;width:'+pct+'%;background:'+(gratis?'#2f7d32':'#7bbf5a')+'"></div></div></div>';
  }
  (CART.itens||[]).forEach(function(it){
    h+='<div style="display:flex;align-items:center;gap:9px;font-size:12px;padding:9px 0;border-bottom:1px solid #e7eae5"><span style="flex-shrink:0;min-width:44px;text-align:center;background:#eef6ea;color:#31772f;font-weight:700;font-size:11px;border-radius:7px;padding:3px 6px">'+fmtQ(it.qtd)+it.unidade+'</span><span style="color:#3a463a;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+it.nome+'</span><span style="color:#1a2417;white-space:nowrap;font-weight:600">'+brl(it.total)+'</span><button onclick="szRemove('+it.produto_id+')" title="Remover item" style="flex-shrink:0;width:24px;height:24px;border-radius:50%;border:1px solid #ecd9d0;background:#fdf6f3;color:#c0603f;cursor:pointer;font-size:12px;line-height:1;font-family:inherit">✕</button></div>';
  });
  h+='<div style="font-size:12.5px;color:#8a938a;line-height:2.1;margin-top:10px">';
  h+='<div style="display:flex;justify-content:space-between"><span>Subtotal</span><span style="color:#1a2417">'+brl(sub)+'</span></div>';
  h+='<div style="display:flex;justify-content:space-between"><span>Entrega</span><span style="color:#1a2417">'+brl(CART.taxa||0)+'</span></div>';
  h+='<div style="display:flex;justify-content:space-between;border-top:1px solid #dde3d9;margin-top:6px;padding-top:8px"><span style="color:#1a2417;font-weight:600">Total</span><span style="color:#2f7d32;font-weight:700">'+brl(CART.total||0)+'</span></div>';
  if(PIXPCT>0)h+='<div style="display:flex;justify-content:space-between;font-size:11.5px;margin-top:2px"><span style="color:#31772f">no Pix (−'+PIXPCT+'%)</span><span style="color:#31772f;font-weight:700">'+brl(pix(CART.total||0))+'</span></div>';
  h+='</div>';
  var min=CART.minimo||0;
  if(min&&sub<min){
    h+='<div style="background:#fdf3e6;border:1px solid #f0c98f;border-radius:9px;padding:9px 11px;margin:12px 0;font-size:11.5px;color:#b5750f">Faltam '+brl(min-sub)+' pro mínimo</div>';
    h+='<button style="display:block;width:100%;background:#e6e9e2;color:#9aa398;border:0;border-radius:11px;padding:13px;font-size:13.5px;font-weight:600;cursor:not-allowed;font-family:inherit">Enviar pedido</button>';
  }else{
    h+='<div style="background:#eef6ea;border:1px solid #cfe6c6;border-radius:9px;padding:10px 12px;margin:12px 0;font-size:11.5px;color:#31772f;line-height:1.4">⏰ Confirma e paga depois. Sem cobrança antes.</div>';
    h+='<a href="/f/'+SLUG+'/carrinho/revisar" style="display:block;text-align:center;width:100%;background:#f48b22;color:#fff;text-decoration:none;border-radius:11px;padding:13px;font-size:13.5px;font-weight:700;font-family:inherit">Ver carrinho</a>';
  }
  box.innerHTML=h;
}
renderCart();syncCards();szInitAssinar();
</script>
</body></html>
"""

_LOJA_CONFIRMAR_NOVO = """{% extends "base" %}{% block conteudo %}
<div class="card" style="max-width:420px;margin:0 auto">
  {% if contexto == "carrinho" %}
    <h2 style="margin-bottom:.3rem">Quase lá! 🛒</h2>
    <p class="mut" style="margin-bottom:1.2rem;font-size:.9rem">
      Pra enviar seu pedido, entre ou crie sua conta rápida.
      Já tem conta? <a href="/login?next=/f/{{ slug }}/carrinho/materializar" style="color:var(--verde-claro)">Entrar</a>
    </p>
    {% set next_url = "/f/" + slug + "/carrinho/materializar" %}
    {% set btn_text = "Criar conta e enviar pedido" %}
    {% set help_text = "Conta grátis. Seu pedido será enviado após confirmar." %}
  {% else %}
    <h2 style="margin-bottom:.3rem">Confirme sua assinatura 🧺</h2>
    <p class="mut" style="margin-bottom:1.2rem;font-size:.9rem">
      Crie sua conta grátis pra receber sua cesta.
      Já tem conta? <a href="/login" style="color:var(--verde-claro)">Entrar</a>
    </p>
    {% set next_url = "/f/" + slug + "/confirmar" %}
    {% set btn_text = "Criar conta e assinar 🧺" %}
    {% set help_text = "Conta grátis. Você só paga a cesta, 4 dias antes de cada entrega. Sem fidelidade." %}
  {% endif %}
  {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
  <form method="post" action="/cadastro">
    <input type="hidden" name="next" value="{{ next_url }}">
    <input type="hidden" name="plano" value="cesta">
    <label>Email</label>
    <input name="email" type="email" required placeholder="seu@email.com" style="width:100%;margin-bottom:.6rem">
    <label>Senha</label>
    <input name="senha" type="password" required placeholder="mínimo 8 caracteres" style="width:100%;margin-bottom:.6rem">
    <label>📱 WhatsApp (com DDD) <span class="mut" style="font-size:.8rem">— pra avisar do seu pedido</span></label>
    <input name="whatsapp" required placeholder="86 98888-7777" maxlength="20" style="width:100%;margin-bottom:.6rem">
    {% if contexto == "carrinho" %}
    <input type="hidden" name="cep" value="{{ cep_pre|default('') }}">
    <input type="hidden" name="endereco" value="{{ endereco_pre|default('') }}">
    <div class="mut" style="font-size:.82rem;margin-bottom:1rem;padding:.55rem .7rem;background:#f2f5f0;border-radius:8px;line-height:1.4">
      📍 Entrega em: {{ endereco_pre|default('') or "endereço do passo anterior" }}
      <a href="/f/{{ slug }}/carrinho/revisar" style="color:var(--verde);margin-left:6px;white-space:nowrap">editar</a>
    </div>
    {% else %}
    <label>CEP</label>
    <input name="cep" required placeholder="64000-000" style="width:100%;margin-bottom:.6rem">
    <label>Endereço</label>
    <input name="endereco" required placeholder="Rua X, nº 123, bairro" style="width:100%;margin-bottom:1rem">
    {% endif %}
    <button style="background:var(--verde);color:#fff;padding:.7rem 1rem;border:0;border-radius:6px;cursor:pointer;width:100%;font-weight:600;font-size:.95rem">
      {{ btn_text }}
    </button>
  </form>
  <p class="mut" style="font-size:.8rem;text-align:center;margin-top:.8rem">
    {{ help_text }}
  </p>
</div>
{% endblock %}"""

_MEUS_PEDIDOS = """{% extends "base" %}{% block conteudo %}
<div style="max-width:920px;margin:0 auto">
  <h2 style="margin-bottom:.4rem">🛍️ Meus pedidos</h2>
  <p class="mut" style="margin-bottom:1.5rem;font-size:.86rem">Compras avulsas feitas nas lojas — cada pedido é único, sem recorrência.
  Suas cestas por assinatura (entrega recorrente) ficam em <a href="/painel/assinaturas" style="color:var(--verde-claro)">🧺 Minhas assinaturas</a>.</p>
  {% if not pedidos %}
  <div class="card" style="text-align:center">
    <p class="mut">Você ainda não tem pedidos. <a href="/" style="color:var(--verde-claro)">Explorar fornecedores</a></p>
  </div>
  {% else %}
    {% set grupos_reais = {} %}
    {% for p in pedidos %}
      {% set g = p.grupo %}
      {% if g not in grupos_reais %}{% set _ = grupos_reais.update({g: []}) %}{% endif %}
      {% set _ = grupos_reais[g].append(p) %}
    {% endfor %}

    {% for grupo_nome,rótulos in [('em_aberto','Em aberto'),('concluido','Concluído'),('cancelado','Cancelado')] %}
      {% if grupo_nome in grupos_reais %}
      <div style="margin-bottom:2rem">
        <h3 style="font-size:14px;color:var(--verde-claro);font-weight:600;margin-bottom:.8rem">{{ rótulos }}</h3>
        {% for p in grupos_reais[grupo_nome] %}
        <div style="background:var(--card);border:1px solid #232325;border-radius:12px;padding:1.1rem;margin-bottom:.7rem">
          <div style="display:flex;justify-content:space-between;align-items:start">
            <div style="flex:1">
              <div style="font-size:15px;color:#f4f4f4;font-weight:600">{{ p.fornecedor_nome }}</div>
              <div style="font-size:12px;color:#888780;margin:5px 0">Pedido #{{ p.id }} · {{ p.qtd_itens }} itens · {{ p.criado_em.strftime('%d/%m/%Y') if p.criado_em else 'Data' }}</div>
              <div style="font-size:13px;color:var(--verde-claro);font-weight:600;margin-top:.5rem">{{ p.total_centavos|brl }}</div>
            </div>
            <div style="text-align:right">
              <span style="display:inline-block;background:#15301f;color:var(--verde-claro);padding:4px 10px;border-radius:16px;font-size:11px;border:1px solid var(--verde)44">{{ p.status_rotulo }}</span>
              <a href="/pedido-enviado/{{ p.id }}" style="display:block;margin-top:8px;font-size:12px;color:var(--verde-claro);text-decoration:none">Detalhar →</a>
              {% if p.forma_pagamento == 'pagar_agora' and not p.pago and p.grupo == 'em_aberto' %}
              <a href="/pedido/{{ p.id }}/pagar" style="display:inline-block;margin-top:8px;background:#c99536;color:#1a1206;padding:6px 12px;border-radius:8px;font-size:11.5px;font-weight:700;text-decoration:none">⚡ Pagar agora</a>
              {% endif %}
            </div>
          </div>
        </div>
        {% endfor %}
      </div>
      {% endif %}
    {% endfor %}
  {% endif %}
</div>
{% endblock %}"""

_EMPRESA_DADOS = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <h2 style="margin:0 0 .3rem">🏢 Complete os dados da sua empresa</h2>
  <div class="mut" style="font-size:.82rem;margin-bottom:1rem">Precisamos disso pra organizar seu financeiro PJ direito. É rápido — digite o CNPJ e busque.</div>
  {% if erro %}<div style="background:#3a2020;border:1px solid #e07a5f55;color:#f0b0a0;padding:.6rem .9rem;border-radius:8px;font-size:.82rem;margin-bottom:1rem">{{ erro }}</div>{% endif %}
  <form method="post" action="/painel/empresa/dados">
    <label>CNPJ</label>
    <div style="display:flex;gap:.5rem">
      <input id="cnpj" name="documento" value="{{ dados.documento }}" inputmode="numeric" placeholder="00.000.000/0001-00" required style="flex:1 1 auto;width:auto;min-width:0">
      <button type="button" onclick="buscarCnpj()" style="flex:0 0 auto;width:auto;margin-top:0;background:#1d3a2e;color:var(--verde-claro);border:1px solid var(--verde)44;border-radius:7px;padding:0 1.1rem;cursor:pointer;white-space:nowrap">🔍 Buscar</button>
    </div>
    <div id="cnpj-msg" class="mut" style="font-size:.72rem;margin-top:.25rem"></div>
    <label>Razão social</label>
    <input id="razao" name="razao_social" value="{{ dados.razao_social }}" required>
    <label>Nome fantasia</label>
    <input id="fantasia" name="nome_fantasia" value="{{ dados.nome_fantasia }}">
    <div style="display:grid;grid-template-columns:1fr 2fr;gap:.7rem">
      <div><label>CEP</label>
        <input id="cep" name="cep" value="{{ dados.cep }}" inputmode="numeric" placeholder="00000-000"></div>
      <div><label>Endereço (rua, número)</label>
        <input id="endereco" name="endereco" value="{{ dados.endereco }}"></div>
    </div>
    <div style="display:grid;grid-template-columns:1.4fr 1.4fr .6fr;gap:.7rem">
      <div><label>Bairro</label>
        <input id="bairro" name="bairro" value="{{ dados.bairro }}"></div>
      <div><label>Cidade</label>
        <input id="cidade" name="cidade" value="{{ dados.cidade }}"></div>
      <div><label>UF</label>
        <input id="uf" name="uf" value="{{ dados.uf }}" maxlength="2" placeholder="UF" style="text-transform:uppercase"></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.7rem">
      <div><label>E-mail da empresa</label>
        <input id="email_empresa" name="email_empresa" type="email" value="{{ dados.email_empresa }}" placeholder="contato@empresa.com"></div>
      <div><label>Telefone</label>
        <input id="telefone" name="telefone" value="{{ dados.telefone }}" placeholder="(00) 0000-0000"></div>
    </div>
    <div class="mut" style="font-size:.72rem;margin-top:.2rem">O e-mail/telefone vêm do cadastro da Receita e podem estar desatualizados — confira antes de salvar.</div>
    <label>Ramo do negócio</label>
    <select id="nicho" name="nicho">
      {% for n in nichos_lista %}
      <option value="{{ n.slug }}" {% if dados.nicho == n.slug %}selected{% endif %}>{{ n.label }}</option>
      {% endfor %}
    </select>
    <div class="mut" style="font-size:.72rem;margin-top:.2rem">Detectamos pelo CNPJ — confira e ajuste se preciso. Define as unidades/categorias dos seus produtos.</div>
    <button type="submit" style="background:var(--verde);color:#fff;border:0;border-radius:8px;padding:.7rem 1.4rem;font-weight:600;cursor:pointer;margin-top:1rem">Salvar e continuar →</button>
  </form>
</div>
{% if eh_fornecedor %}
<div class="card larga">
  <h3 style="margin-top:0">🖼️ Identidade da loja</h3>
  <div class="mut" style="font-size:.82rem;margin-bottom:.8rem">A logo e a capa aparecem no topo da sua loja pública.</div>
  <div style="display:flex;gap:1.2rem;flex-wrap:wrap;align-items:flex-end">
    <div>
      <div class="mut" style="font-size:.8rem;margin-bottom:.4rem">Logomarca</div>
      <div id="forn-logo-prev" style="width:78px;height:78px;border-radius:16px;background:var(--card){% if identidade.logo_url %} url('{{ identidade.logo_url }}') center/cover{% endif %};border:1px solid var(--borda)"></div>
      <label style="display:inline-block;margin-top:.5rem;font-size:.82rem;color:var(--verde);cursor:pointer">enviar logo
        <input type="file" accept="image/*" onchange="fornLogoUpload(this)" style="display:none">
      </label>
    </div>
    <div style="flex:1;min-width:200px">
      <div class="mut" style="font-size:.8rem;margin-bottom:.4rem">Capa (banner)</div>
      <div id="forn-banner-prev" style="height:78px;border-radius:12px;background:var(--card){% if identidade.banner_url %} url('{{ identidade.banner_url }}') center/cover{% endif %};border:1px solid var(--borda)"></div>
      <label style="display:inline-block;margin-top:.5rem;font-size:.82rem;color:var(--verde);cursor:pointer">enviar capa
        <input type="file" accept="image/*" onchange="fornBannerUpload(this)" style="display:none">
      </label>
    </div>
  </div>
  <div id="forn-id-status" style="font-size:.78rem;color:#888780;margin-top:.5rem"></div>
</div>
<div class="card larga">
  <h3 style="margin-top:0">⚙️ Margem alvo</h3>
  <div class="mut" style="font-size:.82rem;margin-bottom:.8rem">Folga de custo pra cobrir perda, comissão e lucro. A cesta sempre respeita esse limite.</div>
  <form method="post" action="/painel/fornecedor/margem-alvo" style="display:flex;gap:.5rem;align-items:flex-end">
    <div style="flex:1">
      <label>Margem alvo (%)</label>
      <input name="margem_alvo" type="number" min="0" max="100" value="{{ margem_alvo }}" placeholder="60" style="width:100%">
      <small class="mut" style="display:block;margin-top:.3rem">Até {{ margem_alvo }}% do preço da cesta pode ser custo. Ex: 60% = você garante 40% de folga.</small>
    </div>
    <button style="background:var(--verde);color:#fff;padding:.5rem 1rem;border:0;border-radius:6px;cursor:pointer;font-weight:500">Salvar</button>
  </form>
</div>
{% endif %}
<script>
function _fornImgUpload(inp, url, prevId){
  var f=inp.files&&inp.files[0]; if(!f) return;
  var st=document.getElementById('forn-id-status'); st.textContent='enviando...';
  var fd=new FormData(); fd.append('arquivo', f);
  fetch(url,{method:'POST',body:fd}).then(function(r){return r.json();}).then(function(d){
    if(d.logo_url||d.banner_url){
      document.getElementById(prevId).style.background="var(--card) url('"+(d.logo_url||d.banner_url)+"') center/cover";
      st.textContent='✓ salvo';
    } else { st.textContent=d.erro||'falhou'; }
  }).catch(function(){st.textContent='erro de conexão';});
}
function fornLogoUpload(i){_fornImgUpload(i,'/painel/fornecedor/logo','forn-logo-prev');}
function fornBannerUpload(i){_fornImgUpload(i,'/painel/fornecedor/banner','forn-banner-prev');}
async function buscarCnpj(){
  var el=document.getElementById('cnpj');
  var msg=document.getElementById('cnpj-msg');
  var cnpj=(el.value||'').replace(/[^0-9]/g,'');
  if(cnpj.length!==14){ msg.textContent='Digite os 14 dígitos do CNPJ.'; return; }
  msg.textContent='Buscando...';
  try{
    var r=await fetch('/painel/empresa/buscar-cnpj?cnpj='+cnpj);
    var d=await r.json();
    if(d.ok){
      if(d.nome){ document.getElementById('razao').value=d.nome; document.getElementById('fantasia').value=d.nome; }
      if(d.endereco) document.getElementById('endereco').value=d.endereco;
      if(d.bairro) document.getElementById('bairro').value=d.bairro;
      if(d.cep) document.getElementById('cep').value=d.cep;
      if(d.cidade) document.getElementById('cidade').value=d.cidade;
      if(d.uf) document.getElementById('uf').value=d.uf;
      if(d.email) document.getElementById('email_empresa').value=d.email;
      if(d.telefone) document.getElementById('telefone').value=d.telefone;
      if(d.nicho){ var ns=document.getElementById('nicho'); if(ns) ns.value=d.nicho; }
      msg.textContent='✓ Dados encontrados — confira e ajuste se precisar.';
    } else { msg.textContent='Não achei esse CNPJ. Preencha manualmente.'; }
  }catch(e){ msg.textContent='Erro na busca. Preencha manualmente.'; }
}
</script>
{% endblock %}"""

_PRODUTOS = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem">
    <h2 style="margin:0">📦 Produtos{% if nicho_label %} <span style="color:#6a6a66;font-size:.7rem;font-weight:400">· {{ nicho_label }}</span>{% endif %}</h2>
    <div style="display:flex;gap:.5rem">
      <button type="button" onclick="prodNovo()" style="background:var(--verde);color:#fff;padding:.5rem 1rem;border:0;border-radius:8px;cursor:pointer;font-weight:600;font-size:.9rem">+ Novo produto</button>
      <button type="button" onclick="prodImportar()" style="padding:.5rem 1rem;background:transparent;border:1px solid var(--verde);color:var(--verde);border-radius:8px;cursor:pointer;font-weight:500;font-size:.9rem">📊 planilha</button>
    </div>
  </div>
  {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
  {% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}

  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem;margin:1rem 0">
    <div class="metric" style="padding:.7rem .8rem"><span style="font-size:.62rem;color:#6a8a7a">PRODUTOS</span><b style="color:var(--verde-claro);display:block;font-size:1.1rem">{{ resumo.total }}</b></div>
    <div class="metric" style="padding:.7rem .8rem"><span style="font-size:.62rem">EM ESTOQUE</span><b style="display:block;font-size:1.1rem">{{ brl(resumo.valor_estoque) }}</b></div>
    <div class="metric" style="padding:.7rem .8rem"><span style="font-size:.62rem;color:#c9a56a">ESTOQUE BAIXO</span><b style="color:#f0c05a;display:block;font-size:1.1rem">{{ resumo.baixo }}{% if resumo.baixo %} ⚠{% endif %}</b></div>
  </div>

  {% if produtos %}
  <input type="text" id="prod-busca" placeholder="🔍 buscar produto..." autocomplete="off" oninput="prodFiltrar(this.value)"
         style="width:100%;padding:.5rem .7rem;background:var(--card);border:1px solid var(--borda);border-radius:8px;color:#e8e8e8;font-size:.9rem;box-sizing:border-box;margin-bottom:.7rem">
  {% endif %}

  <div style="display:flex;flex-direction:column;gap:.5rem">
    {% if produtos %}
      {% for p in produtos %}
      <div class="prod-card" data-nome="{{ p.nome|lower }}" style="background:#1c1c1f;border:1px solid var(--borda);border-radius:10px;padding:.7rem .85rem">
        <div style="display:flex;align-items:center;gap:11px">
          <div class="prod-foto" style="width:44px;height:44px;border-radius:8px;flex-shrink:0;{% if p.foto_url %}background:url('{{ p.foto_url }}') center/cover{% else %}background:#1a2a1f;display:flex;align-items:center;justify-content:center{% endif %}">{% if not p.foto_url %}<span style="font-size:20px;color:#3a5a48">📦</span>{% endif %}</div>
          <div style="flex:1;min-width:0">
            <div style="font-size:.92rem;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ p.nome }} <span class="mut" style="font-weight:400;font-size:.8rem">· {{ label_unidade(p.unidade) }}</span>{% if p.em_promo %} <span style="background:#8a3680;color:#fff;font-size:.62rem;padding:1px 6px;border-radius:4px">promo</span>{% endif %}</div>
            <div class="mut" style="font-size:.75rem;margin-top:1px">saldo {{ p.saldo|round(1) }} {{ label_unidade(p.unidade) }}{% if p.categoria %} · {{ p.categoria }}{% endif %}{% if p.abaixo_minimo %} · <span style="color:#ff6b6b">abaixo do mínimo</span>{% endif %}</div>
          </div>
          <div style="text-align:right;flex-shrink:0">
            <div style="color:#cfcfcf;font-size:.95rem;font-weight:600">{{ brl(p.preco_venda_centavos) }}</div>
            <div style="color:#6a8a7a;font-size:.66rem">{% if p.margem_pct is not none %}margem {{ p.margem_pct|round|int }}%{% endif %}</div>
          </div>
        </div>
        <div class="prod-acoes" style="display:flex;gap:.35rem;flex-wrap:wrap;margin-top:.7rem;padding-top:.65rem;border-top:1px solid #242426">
          <button type="button" onclick="prodVender({{ p.id }})" style="background:var(--verde);color:#fff;border:0;border-radius:6px;padding:.32rem .8rem;cursor:pointer;font-size:.76rem;font-weight:600">vender</button>
          <button type="button" onclick="prodEntrada({{ p.id }})" style="background:#173d2e;color:var(--verde-claro);border:0;border-radius:6px;padding:.32rem .7rem;cursor:pointer;font-size:.76rem;font-weight:500">+ entrada</button>
          <button type="button" onclick="prodPerda({{ p.id }})" style="background:transparent;border:1px solid #3a3a3d;color:#b4b2a9;border-radius:6px;padding:.32rem .7rem;cursor:pointer;font-size:.76rem">perda</button>
          <button type="button" onclick="prodEditar({{ p.id }})" style="background:transparent;border:1px solid #3a3a3d;color:#b4b2a9;border-radius:6px;padding:.32rem .7rem;cursor:pointer;font-size:.76rem">editar</button>
          <button type="button" onclick="prodFoto({{ p.id }})" style="background:transparent;border:1px solid #3a3a3d;color:#a89ce8;border-radius:6px;padding:.32rem .7rem;cursor:pointer;font-size:.76rem">📷</button>
          <button type="button" onclick="prodPromo({{ p.id }})" style="background:transparent;border:1px solid #3a3a3d;color:#d89ad0;border-radius:6px;padding:.32rem .7rem;cursor:pointer;font-size:.76rem">🔖</button>
          <button type="button" onclick="prodApagar({{ p.id }})" style="background:transparent;border:1px solid #3a2a2a;color:#d98a8a;border-radius:6px;padding:.32rem .7rem;cursor:pointer;font-size:.76rem">🗑</button>
        </div>
      </div>
      {% endfor %}
    {% else %}
    <div style="text-align:center;padding:2rem 1rem;color:#6a6a66">
      <div style="font-size:1.6rem;margin-bottom:.4rem">📦</div>
      <div style="font-size:.9rem">Nenhum produto ainda. Clique em <b style="color:var(--verde-claro)">+ Novo produto</b> pra começar.</div>
    </div>
    {% endif %}
  </div>

  {% if pode_servico %}
  <div style="margin-top:1.4rem;padding-top:1rem;border-top:1px solid #1e1e20;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem">
    <span style="color:#6a6a66;font-size:.78rem">Você também presta serviços?</span>
    <a href="/painel/servicos" style="color:#a99aef;font-size:.78rem;text-decoration:none">Ativar aba Serviços →</a>
  </div>
  {% endif %}
</div>

<!-- MODAL cadastro/editar -->
<div id="prod-modal-form" class="prod-modal" style="display:none">
  <div class="prod-modal-box">
    <h3 id="prod-form-titulo" style="margin-top:0;font-size:1.05rem">Novo produto</h3>
    <form method="post" action="/painel/produtos/salvar">
      <input type="hidden" name="produto_id" id="prod-edit-id" value="">
      <label style="font-size:.85rem">Nome</label>
      <input name="nome" id="prod-f-nome" required placeholder="ex: Alface crespa" oninput="prodSugereCategoria(this.value);prodNovoBuscarSugestoes()" class="prod-inp">

      <div style="background:var(--card);border:1px solid #242426;border-radius:9px;padding:.7rem;margin:.2rem 0 .7rem">
        <div style="font-size:.78rem;color:var(--verde-claro);font-weight:600;margin-bottom:.5rem">📷 Foto do produto <span style="color:#6a6a6a;font-weight:400">(opcional)</span></div>
        <div style="display:flex;gap:11px;align-items:flex-start;margin-bottom:.6rem">
          <div id="prod-novo-previa" style="width:64px;height:64px;border-radius:9px;background:#1a2a1f;border:2px solid var(--verde);flex-shrink:0;display:flex;align-items:center;justify-content:center;background-size:cover;background-position:center">
            <span id="prod-novo-previa-vazia" style="font-size:24px;color:#3a5a48">📦</span>
          </div>
          <div style="flex:1;min-width:0">
            <div style="font-size:.72rem;color:#888780;margin-bottom:.4rem">Sugestões pelo nome:</div>
            <div id="prod-novo-sugestoes" style="display:flex;gap:6px;flex-wrap:wrap">
              <span style="font-size:.72rem;color:#6a6a6a">digite o nome acima</span>
            </div>
          </div>
        </div>
        <input id="prod-novo-foto-link" name="foto_url" placeholder="ou cole o link: https://..." oninput="prodNovoFotoPreview(this.value)" class="prod-inp" style="margin:.1rem 0 0;font-size:.8rem">
        <div style="font-size:.68rem;color:#6a6a6a;margin-top:.45rem;line-height:1.5">ℹ Quer a foto real do aparelho? Crie o produto e toque em <span style="color:#a89ce8">📷 foto</span>.</div>
      </div>

      <label style="font-size:.85rem">Unidade</label>
      <select name="unidade" id="prod-f-unidade" class="prod-inp">
        {% for u in unidades %}<option value="{{ u }}">{{ label_unidade(u) }}</option>{% endfor %}
      </select>

      <label style="font-size:.85rem">Categoria</label>
      {% if categorias %}
      <div id="prod-cat-chips" style="display:flex;gap:6px;flex-wrap:wrap;margin:.35rem 0 .5rem">
        {% for c in categorias %}<span class="prod-catchip" onclick="prodSetCat(this, {{ c|tojson }})" style="font-size:.76rem;color:#b4b2a9;background:var(--card);border:1.5px solid var(--borda);border-radius:16px;padding:4px 12px;cursor:pointer">{{ c }}</span>{% endfor %}
      </div>
      {% endif %}
      <input name="categoria" id="prod-f-categoria" placeholder="toque numa sugestão ou digite" oninput="prodCatLimpaSel()" class="prod-inp">
      <div style="display:flex;gap:.6rem;flex-wrap:wrap">
        <div style="flex:1;min-width:120px">
          <label style="font-size:.85rem">Preço de venda (R$)</label>
          <input name="preco_venda" id="prod-f-preco" type="number" step="0.01" required placeholder="3,50" class="prod-inp">
        </div>
        <div style="flex:1;min-width:120px">
          <label style="font-size:.85rem">Estoque mínimo</label>
          <input name="estoque_minimo" id="prod-f-min" type="number" step="0.1" placeholder="5" class="prod-inp">
        </div>
      </div>
      <div style="display:flex;gap:8px;margin-top:.9rem">
        <button style="flex:1;background:var(--verde);color:#fff;padding:.6rem;border:0;border-radius:8px;cursor:pointer;font-weight:600">Salvar</button>
        <button type="button" onclick="prodFecha('prod-modal-form')" style="background:transparent;border:1px solid #555;color:#aaa;border-radius:8px;padding:.6rem 1rem;cursor:pointer">Cancelar</button>
      </div>
    </form>
  </div>
</div>

<!-- MODAL entrada/perda -->
<div id="prod-modal-mov" class="prod-modal" style="display:none">
  <div class="prod-modal-box" style="max-width:340px">
    <h3 id="prod-mov-titulo" style="margin-top:0;font-size:1.05rem">Dar entrada</h3>
    <form method="post" id="prod-mov-form">
      <input type="hidden" name="produto_id" id="prod-mov-id">
      <label style="font-size:.85rem">Quantidade</label>
      <input name="quantidade" type="number" step="0.1" required class="prod-inp">
      <div id="prod-mov-custo-wrap">
        <label style="font-size:.85rem">Custo unitário (R$)</label>
        <input name="custo_unit" type="number" step="0.01" class="prod-inp">
        <label style="font-size:.85rem">Origem</label>
        <select name="origem_id" id="prod-mov-origem" class="prod-inp">{% for o in origens %}<option value="{{ o.id }}">{{ o.nome }}</option>{% endfor %}</select>
      </div>
      <div id="prod-mov-motivo-wrap" style="display:none">
        <label style="font-size:.85rem">Motivo (opcional)</label>
        <input name="motivo" class="prod-inp">
      </div>
      <div style="display:flex;gap:8px;margin-top:.6rem">
        <button style="flex:1;background:var(--verde);color:#fff;padding:.6rem;border:0;border-radius:8px;cursor:pointer;font-weight:600">Confirmar</button>
        <button type="button" onclick="prodFecha('prod-modal-mov')" style="background:transparent;border:1px solid #555;color:#aaa;border-radius:8px;padding:.6rem 1rem;cursor:pointer">Cancelar</button>
      </div>
    </form>
  </div>
</div>

<!-- MODAL foto -->
<div id="prod-modal-foto" class="prod-modal" style="display:none">
  <div class="prod-modal-box">
    <h4 style="margin-top:0">Foto de <span id="prod-foto-nome" style="color:var(--verde-claro)"></span></h4>
    <input type="hidden" id="prod-foto-pid">
    <label style="display:flex;align-items:center;gap:10px;background:var(--verde);color:#fff;border-radius:9px;padding:.7rem .9rem;font-size:12.5px;font-weight:600;cursor:pointer;margin-bottom:.8rem">
      <span style="font-size:20px">📷</span><div>Tirar foto / enviar do aparelho<div style="font-size:10px;font-weight:400;opacity:.85">a foto real do seu produto</div></div>
      <input type="file" accept="image/*" capture="environment" onchange="prodFotoUpload(this)" style="display:none">
    </label>
    <div id="prod-foto-upload-status" style="font-size:.74rem;color:#888780;margin-bottom:.8rem"></div>
    <div style="display:flex;gap:14px;align-items:flex-start;margin-bottom:1rem">
      <div id="prod-foto-previa" style="width:88px;height:88px;border-radius:10px;background:#1a2a1f;border:2px solid var(--verde);flex-shrink:0;background-size:cover;background-position:center;display:flex;align-items:center;justify-content:center"><span id="prod-foto-previa-vazia" style="font-size:28px;color:#3a5a48">📦</span></div>
      <div style="flex:1;font-size:.78rem;color:#888780">A prévia aparece quando você cola o link ou escolhe uma sugestão.</div>
    </div>
    <div id="prod-foto-sugbox" style="margin-bottom:.9rem"><div style="font-size:.78rem;color:var(--verde-claro);font-weight:600;margin-bottom:.4rem">✨ Sugestões</div><div id="prod-foto-sugestoes" style="display:flex;gap:7px;flex-wrap:wrap"><span style="font-size:.74rem;color:#888780">buscando...</span></div></div>
    <label style="font-size:.8rem;color:#b4b2a9">Ou cole o link da sua foto:</label>
    <input id="prod-foto-link" placeholder="https://..." oninput="prodFotoPreview(this.value)" class="prod-inp">
    <div style="display:flex;gap:8px">
      <button type="button" onclick="prodFotoSalvar()" style="flex:1;background:var(--verde);color:#fff;border:0;border-radius:8px;padding:.65rem;font-weight:600;cursor:pointer">Salvar</button>
      <button type="button" onclick="prodFotoRemover()" style="background:transparent;border:1px solid #8a3636;color:#c97;border-radius:8px;padding:.65rem .9rem;cursor:pointer">Remover</button>
      <button type="button" onclick="prodFecha('prod-modal-foto')" style="background:transparent;border:1px solid #555;color:#aaa;border-radius:8px;padding:.65rem .9rem;cursor:pointer">Cancelar</button>
    </div>
  </div>
</div>

<!-- MODAL promo -->
<div id="prod-modal-promo" class="prod-modal" style="display:none">
  <div class="prod-modal-box" style="max-width:340px">
    <h4 style="margin-top:0">Promoção · <span id="prod-promo-nome" style="color:#d89ad0"></span></h4>
    <input type="hidden" id="prod-promo-pid">
    <label style="display:flex;align-items:center;gap:8px;margin-bottom:.8rem;font-size:.9rem"><input type="checkbox" id="prod-promo-ativo" onchange="prodPromoToggle()"> Produto em promoção</label>
    <div id="prod-promo-preco-wrap" style="display:none">
      <label style="font-size:.85rem">Preço promocional (R$)</label>
      <input id="prod-promo-preco" type="number" step="0.01" class="prod-inp">
    </div>
    <div style="display:flex;gap:8px">
      <button type="button" onclick="prodPromoSalvar()" style="flex:1;background:#8a3680;color:#fff;border:0;border-radius:8px;padding:.6rem;font-weight:600;cursor:pointer">Salvar</button>
      <button type="button" onclick="prodFecha('prod-modal-promo')" style="background:transparent;border:1px solid #555;color:#aaa;border-radius:8px;padding:.6rem .9rem;cursor:pointer">Cancelar</button>
    </div>
  </div>
</div>

<!-- MODAL importar -->
<div id="prod-modal-importar" class="prod-modal" style="display:none">
  <div class="prod-modal-box">
    <h3 style="margin-top:0;font-size:1.05rem">Importar planilha (CSV)</h3>
    <p class="mut" style="font-size:.8rem">Colunas: nome, unidade, categoria, preço, estoque mínimo. Primeira linha é o cabeçalho.</p>
    <input type="file" id="prod-planilha-file" accept=".csv" onchange="prodLerPlanilha(this)" class="prod-inp">
    <div id="prod-planilha-previa" style="margin-top:.8rem"></div>
    <button type="button" onclick="prodFecha('prod-modal-importar')" style="width:100%;background:transparent;border:1px solid #555;color:#aaa;border-radius:8px;padding:.5rem;margin-top:.5rem;cursor:pointer">Cancelar</button>
  </div>
</div>

<form method="post" id="prod-apagar-form" action="/painel/produtos/apagar" style="display:none"><input type="hidden" name="produto_id" id="prod-apagar-id"></form>

<div id="prod-modal-pdv" class="prod-modal" style="display:none">
  <div class="prod-modal-box" style="max-width:440px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.6rem">
      <h3 style="margin:0">🛒 Venda de balcão</h3>
      <button type="button" onclick="pdvFecha()" style="background:transparent;border:none;color:#888;font-size:1.2rem;cursor:pointer;width:auto;margin:0;padding:0">✕</button>
    </div>
    <div id="pdv-itens" style="margin-bottom:.6rem"></div>
    <div style="position:relative;margin-bottom:.5rem">
      <input id="pdv-cli-busca" placeholder="Cliente — CPF, telefone ou nome" autocomplete="off" oninput="pdvCliBusca(this.value)" class="prod-inp" style="margin:0">
      <div id="pdv-cli-sug" style="display:none;position:absolute;left:0;right:0;top:100%;background:#1c1c1f;border:1px solid var(--borda);border-top:0;border-radius:0 0 7px 7px;z-index:20;max-height:170px;overflow:auto"></div>
      <div id="pdv-cli-sel" style="display:none;margin-top:.4rem;font-size:.82rem;color:var(--verde-claro)"></div>
      <div id="pdv-cli-novo" style="display:none;margin-top:.4rem;gap:.4rem;grid-template-columns:1fr 1fr">
        <input id="pdv-novo-nome" placeholder="Nome do novo cliente" class="prod-inp" style="margin:0">
        <input id="pdv-novo-cpf" placeholder="CPF (opcional)" class="prod-inp" style="margin:0">
      </div>
    </div>
    <div style="display:flex;gap:.5rem;margin-bottom:.6rem;align-items:center">
      <span style="font-size:.8rem;color:#888">Desconto R$</span>
      <input id="pdv-desc" type="number" step="0.01" min="0" value="0" class="prod-inp" style="margin:0;max-width:110px" oninput="pdvRender()">
    </div>
    <div style="font-size:.75rem;color:#888;margin-bottom:.3rem">Pagamento</div>
    <div id="pdv-pag" style="display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:.8rem">
      <span class="pdv-pag-chip pdv-pag-sel" data-pag="dinheiro" onclick="pdvPag(this)">Dinheiro</span>
      <span class="pdv-pag-chip" data-pag="pix" onclick="pdvPag(this)">Pix</span>
      <span class="pdv-pag-chip" data-pag="cartao" onclick="pdvPag(this)">Cartão</span>
    </div>
    <div id="pdv-erro" style="color:#d98a8a;font-size:.8rem;margin-bottom:.5rem;display:none"></div>
    <div style="display:flex;justify-content:space-between;align-items:baseline;border-top:1px solid var(--borda);padding-top:.6rem">
      <span style="color:#888">Total</span><span id="pdv-total" style="color:var(--verde-claro);font-size:1.3rem;font-weight:700">R$ 0,00</span>
    </div>
    <button type="button" onclick="pdvFinalizar()" id="pdv-finalizar" style="width:100%;background:var(--verde);color:#fff;padding:.65rem;border:0;border-radius:8px;cursor:pointer;font-weight:700;margin-top:.7rem">Finalizar venda</button>
  </div>
</div>

<style>
.prod-acoes button{width:auto;margin-top:0}
.prod-modal{position:fixed;inset:0;z-index:1000;background:#000000aa;display:flex;align-items:center;justify-content:center;padding:1rem}
.prod-modal-box{background:#1c1c1f;border:1px solid var(--borda);border-radius:12px;padding:1.3rem;max-width:440px;width:100%;max-height:88vh;overflow-y:auto}
.prod-inp{width:100%;box-sizing:border-box;padding:.5rem;background:var(--bg);border:1px solid var(--borda);color:#f4f4f4;border-radius:6px;margin:.25rem 0 .6rem}
.pdv-pag-chip{font-size:.78rem;color:#b4b2a9;background:var(--card);border:1.5px solid var(--borda);border-radius:16px;padding:.3rem .8rem;cursor:pointer}
.pdv-pag-chip.pdv-pag-sel{border-color:var(--verde);color:var(--verde-claro)}
</style>

<script>
window.PRODUTOS = {
  {% for p in produtos %}
  "{{ p.id }}": { nome: {{ p.nome|tojson }}, unidade: {{ p.unidade|tojson }}, categoria: {{ (p.categoria or '')|tojson }}, preco: {{ (p.preco_venda_centavos/100)|tojson }}, minimo: {{ (p.estoque_minimo if p.estoque_minimo is not none else '')|tojson }}, foto_url: {{ (p.foto_url or '')|tojson }}, em_promo: {{ 'true' if p.em_promo else 'false' }}, preco_promo: {{ ((p.preco_promo_centavos or 0)/100)|tojson }} }{% if not loop.last %},{% endif %}
  {% endfor %}
};
var prodFotoEscolhida = null;
var prodItensPlanilha = [];
function prodFecha(id){ document.getElementById(id).style.display='none'; }
var PDV_CART=[];
function prodVender(id){ location.href='/painel/pdv?add='+id; }
function pdvFecha(){ document.getElementById('prod-modal-pdv').style.display='none'; }
function pdvQtd(id,d){ var e=PDV_CART.find(function(x){return x.id==id;}); if(!e) return; e.qtd=Math.round((e.qtd+d)*1000)/1000; if(e.qtd<=0){ PDV_CART=PDV_CART.filter(function(x){return x.id!=id;}); } pdvRender(); }
function pdvPreco(id,v){ var e=PDV_CART.find(function(x){return x.id==id;}); if(e){ e.preco=parseFloat(String(v).replace(',','.'))||0; } pdvRender(); }
function pdvPag(el){ document.querySelectorAll('#pdv-pag .pdv-pag-chip').forEach(function(x){x.classList.remove('pdv-pag-sel');}); el.classList.add('pdv-pag-sel'); }
function pdvFmt(n){ return 'R$ '+n.toFixed(2).replace('.',','); }
function pdvRender(){ var box=document.getElementById('pdv-itens'); if(!PDV_CART.length){ box.innerHTML='<div style="color:#888;text-align:center;padding:1rem;font-size:.85rem">Carrinho vazio.</div>'; document.getElementById('pdv-total').textContent='R$ 0,00'; return; } var sub=0,h=''; PDV_CART.forEach(function(it){ var lt=it.preco*it.qtd; sub+=lt; h+='<div style="display:flex;align-items:center;gap:.5rem;padding:.4rem 0;border-bottom:1px solid #242426">'+'<div style="flex:1;min-width:0"><div style="color:var(--txt);font-size:.85rem">'+it.nome+'</div><input type="number" step="0.01" value="'+it.preco.toFixed(2)+'" onchange="pdvPreco('+it.id+',this.value)" style="width:80px;background:var(--bg);border:1px solid var(--borda);color:#cfcfcf;border-radius:5px;padding:.2rem .3rem;font-size:.72rem;margin-top:.2rem"> <span style="color:#6a8a7a;font-size:.68rem">/'+it.unidade+'</span></div>'+'<div style="display:flex;align-items:center;gap:.3rem"><span onclick="pdvQtd('+it.id+',-1)" style="width:22px;height:22px;border-radius:5px;background:#1c1c1f;border:1px solid #3a3a3d;color:#b4b2a9;display:flex;align-items:center;justify-content:center;cursor:pointer">-</span><span style="color:var(--txt);font-size:.8rem;min-width:44px;text-align:center">'+it.qtd+' '+it.unidade+'</span><span onclick="pdvQtd('+it.id+',1)" style="width:22px;height:22px;border-radius:5px;background:#1c1c1f;border:1px solid #3a3a3d;color:#b4b2a9;display:flex;align-items:center;justify-content:center;cursor:pointer">+</span></div>'+'<div style="color:#cfcfcf;font-size:.8rem;min-width:64px;text-align:right">'+pdvFmt(lt)+'</div>'+'</div>'; }); box.innerHTML=h; var desc=parseFloat(String(document.getElementById('pdv-desc').value).replace(',','.'))||0; var tot=sub-desc; if(tot<0)tot=0; document.getElementById('pdv-total').textContent=pdvFmt(tot); }
var PDV_CLI=null, PDV_CLI_RES=[], PDV_CLI_T=null;
function pdvCliBusca(q){ q=(q||'').trim(); PDV_CLI=null; document.getElementById('pdv-cli-sel').style.display='none'; if(PDV_CLI_T) clearTimeout(PDV_CLI_T); var sug=document.getElementById('pdv-cli-sug'); if(q.length<2){ sug.style.display='none'; return; } PDV_CLI_T=setTimeout(function(){ fetch('/painel/clientes/buscar?q='+encodeURIComponent(q)).then(function(r){return r.json();}).then(function(d){ PDV_CLI_RES=d.clientes||[]; var h=''; PDV_CLI_RES.forEach(function(c,i){ h+='<div onclick="pdvCliPick('+i+')" style="padding:.45rem .6rem;border-bottom:1px solid #242426;cursor:pointer;font-size:.82rem;color:var(--txt)">'+c.nome+'<span style="color:#6a8a7a;font-size:.72rem">'+(c.telefone?(' · '+c.telefone):'')+(c.cpf?(' · CPF '+c.cpf):'')+'</span></div>'; }); h+='<div onclick="pdvCliNovo()" style="padding:.45rem .6rem;cursor:pointer;font-size:.82rem;color:var(--verde-claro)">+ cadastrar novo cliente</div>'; sug.innerHTML=h; sug.style.display='block'; }).catch(function(){}); }, 250); }
function pdvCliPick(i){ var c=PDV_CLI_RES[i]; if(!c) return; PDV_CLI={id:c.id,nome:c.nome,telefone:c.telefone,cpf:c.cpf}; document.getElementById('pdv-cli-busca').value=c.nome; document.getElementById('pdv-cli-sug').style.display='none'; document.getElementById('pdv-cli-novo').style.display='none'; var sel=document.getElementById('pdv-cli-sel'); sel.innerHTML='✓ '+c.nome+' <a onclick="pdvCliLimpar()" style="color:#d98a8a;cursor:pointer;margin-left:.4rem">(trocar)</a>'; sel.style.display='block'; }
function pdvCliNovo(){ document.getElementById('pdv-cli-sug').style.display='none'; var q=document.getElementById('pdv-cli-busca').value||''; var novo=document.getElementById('pdv-cli-novo'); novo.style.display='grid'; var digs=q.replace(/[^0-9]/g,""); if(digs.length>=11){ document.getElementById('pdv-novo-cpf').value=q; document.getElementById('pdv-novo-nome').value=''; } else { document.getElementById('pdv-novo-nome').value=q; } PDV_CLI=null; }
function pdvCliLimpar(){ PDV_CLI=null; document.getElementById('pdv-cli-sel').style.display='none'; document.getElementById('pdv-cli-busca').value=''; document.getElementById('pdv-cli-novo').style.display='none'; document.getElementById('pdv-novo-nome').value=''; document.getElementById('pdv-novo-cpf').value=''; }
function pdvFinalizar(){ if(!PDV_CART.length){ return; } var btn=document.getElementById('pdv-finalizar'); btn.disabled=true; btn.textContent='Registrando...'; var desc=parseFloat(String(document.getElementById('pdv-desc').value).replace(',','.'))||0; var pagEl=document.querySelector('#pdv-pag .pdv-pag-sel'); var payload={ itens:PDV_CART.map(function(it){ return {produto_id:it.id, quantidade:it.qtd, preco_unit_centavos:Math.round(it.preco*100)}; }), pagamento:pagEl?pagEl.getAttribute('data-pag'):'dinheiro', desconto_centavos:Math.round(desc*100) }; if(PDV_CLI&&PDV_CLI.id){ payload.cliente_id=PDV_CLI.id; payload.cliente_nome=PDV_CLI.nome; } else { var nn=(document.getElementById('pdv-novo-nome').value||'').trim(); var nc=(document.getElementById('pdv-novo-cpf').value||'').trim(); if(nn) payload.cliente_nome=nn; if(nc) payload.cliente_cpf=nc; } fetch('/painel/produtos/vender',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(function(r){return r.json();}).then(function(d){ if(d.ok){ PDV_CART=[]; location.reload(); } else { var e=document.getElementById('pdv-erro'); e.textContent=d.erro||'erro'; e.style.display='block'; btn.disabled=false; btn.textContent='Finalizar venda'; } }).catch(function(){ var e=document.getElementById('pdv-erro'); e.textContent='erro de conexão'; e.style.display='block'; btn.disabled=false; btn.textContent='Finalizar venda'; }); }
function prodNovoFotoPreview(url){ var prev=document.getElementById('prod-novo-previa'); var vazia=document.getElementById('prod-novo-previa-vazia'); url=(url||'').trim(); if(!url){ prev.style.backgroundImage='none'; if(vazia){ vazia.style.display='block'; vazia.textContent='📦'; } return; } var img=new Image(); img.onload=function(){ prev.style.backgroundImage="url('"+url+"')"; if(vazia) vazia.style.display='none'; }; img.onerror=function(){ prev.style.backgroundImage='none'; if(vazia){ vazia.style.display='block'; vazia.textContent='✗'; } }; img.src=url; }
function prodSetCat(chip, valor){ var inp=document.getElementById('prod-f-categoria'); if(inp) inp.value=valor; var box=chip.parentElement; if(box) box.querySelectorAll('.prod-catchip').forEach(function(x){ x.style.border='1.5px solid var(--borda)'; x.style.color='#b4b2a9'; }); chip.style.border='1.5px solid var(--verde)'; chip.style.color='var(--verde-claro)'; }
function prodCatLimpaSel(){ var box=document.getElementById('prod-cat-chips'); if(box) box.querySelectorAll('.prod-catchip').forEach(function(x){ x.style.border='1.5px solid var(--borda)'; x.style.color='#b4b2a9'; }); }
function prodCatDestaca(valor){ var box=document.getElementById('prod-cat-chips'); if(!box) return; box.querySelectorAll('.prod-catchip').forEach(function(x){ var hit=x.textContent.trim()===valor; x.style.border=hit?'1.5px solid var(--verde)':'1.5px solid var(--borda)'; x.style.color=hit?'var(--verde-claro)':'#b4b2a9'; }); }
var _prodSugTimer=null;
function prodNovoBuscarSugestoes(){ var campo=document.getElementById('prod-f-nome'); var box=document.getElementById('prod-novo-sugestoes'); if(!campo||!box) return; var nome=(campo.value||'').trim(); if(nome.length<2){ box.innerHTML='<span style="font-size:.72rem;color:#6a6a6a">digite o nome acima</span>'; return; } clearTimeout(_prodSugTimer); _prodSugTimer=setTimeout(function(){ box.innerHTML='<span style="font-size:.72rem;color:#888780">buscando...</span>'; fetch('/painel/produtos/sugerir-fotos?nome='+encodeURIComponent(nome)).then(function(r){ return r.json(); }).then(function(d){ box.innerHTML=''; if(!d.opcoes||!d.opcoes.length){ box.innerHTML='<span style="font-size:.72rem;color:#888780">sem sugestões; cole um link</span>'; return; } d.opcoes.forEach(function(url){ var t=document.createElement('div'); t.style.cssText="width:40px;height:40px;border-radius:7px;background:url('"+url+"') center/cover;cursor:pointer;border:2px solid var(--borda);flex-shrink:0"; t.onclick=function(){ document.getElementById('prod-novo-foto-link').value=url; prodNovoFotoPreview(url); box.querySelectorAll('div').forEach(function(x){ x.style.border='2px solid var(--borda)'; }); t.style.border='2px solid var(--verde)'; }; box.appendChild(t); }); }).catch(function(){ box.innerHTML='<span style="font-size:.72rem;color:#888780">erro ao buscar; cole um link</span>'; }); }, 450); }
function prodNovo(){ document.getElementById('prod-form-titulo').textContent='Novo produto'; document.getElementById('prod-edit-id').value=''; document.getElementById('prod-f-nome').value=''; document.getElementById('prod-f-categoria').value=''; document.getElementById('prod-f-preco').value=''; document.getElementById('prod-f-min').value=''; document.getElementById('prod-novo-foto-link').value=''; prodNovoFotoPreview(''); prodCatLimpaSel(); document.getElementById('prod-novo-sugestoes').innerHTML='<span style="font-size:.72rem;color:#6a6a6a">digite o nome acima</span>'; document.getElementById('prod-modal-form').style.display='flex'; }
function prodImportar(){ document.getElementById('prod-modal-importar').style.display='flex'; }
function prodFiltrar(q){ q=q.toLowerCase(); var cards=document.querySelectorAll('.prod-card'); for(var i=0;i<cards.length;i++){ cards[i].style.display=cards[i].getAttribute('data-nome').indexOf(q)>-1?'':'none'; } }
function prodEditar(id){ var p=window.PRODUTOS[id]; document.getElementById('prod-form-titulo').textContent='Editar produto'; document.getElementById('prod-edit-id').value=id; document.getElementById('prod-f-nome').value=p.nome; document.getElementById('prod-f-unidade').value=p.unidade; document.getElementById('prod-f-categoria').value=p.categoria||''; document.getElementById('prod-f-preco').value=p.preco; document.getElementById('prod-f-min').value=p.minimo; document.getElementById('prod-novo-foto-link').value=p.foto_url||''; prodNovoFotoPreview(p.foto_url||''); prodCatDestaca(p.categoria||''); document.getElementById('prod-novo-sugestoes').innerHTML='<span style="font-size:.72rem;color:#6a6a6a">digite o nome pra ver sugestões</span>'; document.getElementById('prod-modal-form').style.display='flex'; }
function prodEntrada(id){ document.getElementById('prod-mov-titulo').textContent='Dar entrada · '+window.PRODUTOS[id].nome; document.getElementById('prod-mov-id').value=id; document.getElementById('prod-mov-form').action='/painel/produtos/entrada'; document.getElementById('prod-mov-custo-wrap').style.display='block'; document.getElementById('prod-mov-motivo-wrap').style.display='none'; document.getElementById('prod-modal-mov').style.display='flex'; }
function prodPerda(id){ document.getElementById('prod-mov-titulo').textContent='Registrar perda · '+window.PRODUTOS[id].nome; document.getElementById('prod-mov-id').value=id; document.getElementById('prod-mov-form').action='/painel/produtos/perda'; document.getElementById('prod-mov-custo-wrap').style.display='none'; document.getElementById('prod-mov-motivo-wrap').style.display='block'; document.getElementById('prod-modal-mov').style.display='flex'; }
function prodApagar(id){ if(confirm('Apagar '+window.PRODUTOS[id].nome+'? Ele some do catálogo. O histórico fica guardado.')){ document.getElementById('prod-apagar-id').value=id; document.getElementById('prod-apagar-form').submit(); } }
function prodSugereCategoria(nome){ if(!nome||nome.length<3){ return; } var c=document.getElementById('prod-f-categoria'); if(!c||c.value){ return; } fetch('/painel/produtos/sugerir-categoria?nome='+encodeURIComponent(nome)).then(function(r){ return r.json(); }).then(function(d){ if(d.categoria&&!c.value){ if(c.tagName==='SELECT'){ for(var i=0;i<c.options.length;i++){ if(c.options[i].value===d.categoria){ c.value=d.categoria; break; } } } else { c.value=d.categoria; } } }).catch(function(){}); }
function prodFotoPreview(url){ var prev=document.getElementById('prod-foto-previa'); var vazia=document.getElementById('prod-foto-previa-vazia'); if(!url){ prev.style.backgroundImage='none'; vazia.style.display='block'; prodFotoEscolhida=null; return; } var img=new Image(); img.onload=function(){ prev.style.backgroundImage="url('"+url+"')"; vazia.style.display='none'; prodFotoEscolhida=url; }; img.onerror=function(){ prev.style.backgroundImage='none'; vazia.style.display='block'; vazia.textContent='⚠'; prodFotoEscolhida=null; }; img.src=url; }
function prodFoto(pid){ var p=window.PRODUTOS[pid]; document.getElementById('prod-foto-pid').value=pid; document.getElementById('prod-foto-nome').textContent=p.nome; document.getElementById('prod-foto-link').value=p.foto_url||''; prodFotoPreview(p.foto_url||''); document.getElementById('prod-modal-foto').style.display='flex'; var box=document.getElementById('prod-foto-sugestoes'); box.innerHTML='<span style="font-size:.74rem;color:#888780">buscando...</span>'; fetch('/painel/produtos/sugerir-fotos?nome='+encodeURIComponent(p.nome)).then(function(r){ return r.json(); }).then(function(d){ box.innerHTML=''; if(!d.opcoes.length){ box.innerHTML='<span style="font-size:.74rem;color:#888780">Sem sugestões. Cole um link.</span>'; return; } d.opcoes.forEach(function(url){ var t=document.createElement('div'); t.style.cssText="width:56px;height:56px;border-radius:8px;background:url('"+url+"') center/cover;cursor:pointer;border:2px solid var(--borda)"; t.onclick=function(){ document.getElementById('prod-foto-link').value=url; prodFotoPreview(url); }; box.appendChild(t); }); }).catch(function(){ box.innerHTML='<span style="font-size:.74rem;color:#888780">Erro ao buscar. Cole um link.</span>'; }); }
function prodFotoSalvar(){ var pid=document.getElementById('prod-foto-pid').value; var url=(prodFotoEscolhida||document.getElementById('prod-foto-link').value.trim()||''); fetch('/painel/produtos/foto',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({produto_id:parseInt(pid),foto_url:url})}).then(function(r){ return r.json(); }).then(function(){ location.reload(); }).catch(function(){ alert('Não foi possível salvar a foto.'); }); }
function prodFotoRemover(){ var pid=document.getElementById('prod-foto-pid').value; fetch('/painel/produtos/foto',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({produto_id:parseInt(pid),foto_url:''})}).then(function(r){ return r.json(); }).then(function(){ location.reload(); }).catch(function(){ alert('Não foi possível remover.'); }); }
function prodFotoUpload(input){ var file=input.files[0]; if(!file){ return; } var pid=document.getElementById('prod-foto-pid').value; var status=document.getElementById('prod-foto-upload-status'); status.textContent='Enviando foto...'; var reader=new FileReader(); reader.onload=function(e){ prodFotoPreview(e.target.result); }; reader.readAsDataURL(file); var fd=new FormData(); fd.append('produto_id',pid); fd.append('arquivo',file); fetch('/painel/produtos/upload-foto',{method:'POST',body:fd}).then(function(r){ return r.json(); }).then(function(d){ if(d.erro){ status.textContent='⚠ '+d.erro; return; } status.textContent='✓ Foto enviada!'; setTimeout(function(){ location.reload(); },600); }).catch(function(){ status.textContent='⚠ Falhou. Tente de novo.'; }); }
function prodPromo(id){ var p=window.PRODUTOS[id]; document.getElementById('prod-promo-pid').value=id; document.getElementById('prod-promo-nome').textContent=p.nome; document.getElementById('prod-promo-ativo').checked=p.em_promo; document.getElementById('prod-promo-preco').value=p.em_promo?p.preco_promo:''; document.getElementById('prod-promo-preco-wrap').style.display=p.em_promo?'block':'none'; document.getElementById('prod-modal-promo').style.display='flex'; }
function prodPromoToggle(){ document.getElementById('prod-promo-preco-wrap').style.display=document.getElementById('prod-promo-ativo').checked?'block':'none'; }
function prodPromoSalvar(){ var pid=document.getElementById('prod-promo-pid').value; var ativo=document.getElementById('prod-promo-ativo').checked; var preco=parseFloat(document.getElementById('prod-promo-preco').value||0); fetch('/painel/produtos/promo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({produto_id:parseInt(pid),em_promo:ativo,preco_promo:preco})}).then(function(r){ return r.json(); }).then(function(){ location.reload(); }).catch(function(){ alert('Não foi possível salvar a promoção.'); }); }
function prodLerPlanilha(input){ var file=input.files[0]; if(!file){ return; } var fd=new FormData(); fd.append('arquivo',file); var prev=document.getElementById('prod-planilha-previa'); prev.innerHTML='<span class="mut">lendo...</span>'; fetch('/painel/produtos/ler-planilha',{method:'POST',body:fd}).then(function(r){ return r.json(); }).then(function(d){ if(!d.ok){ prev.innerHTML='<span style="color:#ff6b6b">'+(d.erro||'erro')+'</span>'; return; } prodItensPlanilha=d.itens||[]; if(!prodItensPlanilha.length){ prev.innerHTML='<span class="mut">Nenhum item válido.</span>'; return; } prev.innerHTML='<div style="font-size:.85rem;margin-bottom:.5rem">'+prodItensPlanilha.length+' itens lidos.</div><button type="button" id="prod-planilha-btn" style="width:100%;background:var(--verde);color:#fff;border:0;border-radius:8px;padding:.6rem;font-weight:600;cursor:pointer">Importar '+prodItensPlanilha.length+' produtos</button>'; document.getElementById('prod-planilha-btn').onclick=prodImportarConfirma; }).catch(function(){ prev.innerHTML='<span style="color:#ff6b6b">Erro ao ler.</span>'; }); }
function prodImportarConfirma(){ fetch('/painel/produtos/importar-planilha',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({itens:prodItensPlanilha})}).then(function(r){ return r.json(); }).then(function(d){ if(d.ok){ location.reload(); } else { alert(d.erro||'Erro ao importar'); } }).catch(function(){ alert('Erro ao importar'); }); }
</script>
{% endblock %}"""

_ABASTECIMENTO = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem">
    <h2 style="margin:0">🛒 Abastecimento <span style="color:#6a6a66;font-size:.7rem;font-weight:400">· entradas que repõem o estoque</span></h2>
    <div style="display:flex;gap:.5rem">
      <a href="/painel/produtos" style="padding:.5rem 1rem;background:transparent;border:1px solid #3a3a3d;color:#b4b2a9;border-radius:8px;text-decoration:none;font-size:.9rem">← Produtos</a>
      <button type="button" onclick="abastShowNova()" style="padding:.5rem 1rem;background:var(--verde);color:#fff;border:0;border-radius:8px;cursor:pointer;font-weight:600;font-size:.9rem">+ nova compra</button>
    </div>
  </div>
  {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
  {% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
  <div style="display:flex;flex-direction:column;gap:.8rem;margin-top:1rem">
    {% if compras %}
      {% for c in compras %}
      <div style="background:#1c1c1f;border:1px solid var(--borda);border-radius:8px;padding:1rem">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div>
            <strong>#{{ c.id }}</strong> · <span class="mut">{{ c.data_compra }}</span> · <span style="{% if c.status == 'confirmada' %}color:var(--verde){% else %}color:#ffa500{% endif %}">{{ c.status }}</span>
            <div class="mut" style="font-size:.85rem;margin-top:.3rem">Total: <strong>R$ {{ "%.2f" | format(c.total_centavos / 100) }}</strong> | Origem: {{ c.origem_nome or '-' }} | Fonte: {{ c.fonte }}</div>
          </div>
          <a href="/painel/produtos/abastecimento/{{ c.id }}" style="background:transparent;border:1px solid var(--verde-claro);color:var(--verde-claro);border-radius:4px;padding:.3rem .6rem;font-size:.85rem;text-decoration:none">revisar</a>
        </div>
      </div>
      {% endfor %}
    {% else %}
    <p class="mut">Nenhuma compra ainda. Clique em "+ nova compra" pra começar.</p>
    {% endif %}
  </div>
  <div id="abast-nova" style="display:none;margin-top:1.5rem;padding:1.2rem;background:#1c1c1f;border:1px solid var(--borda);border-radius:8px">
    <h4 style="margin-top:0">Nova compra</h4>
    <div style="display:flex;gap:.6rem;margin-bottom:1rem">
      <button type="button" onclick="abastShowManual()" style="flex:1;background:var(--verde);color:#fff;padding:.5rem;border:0;border-radius:6px;cursor:pointer;font-weight:500">Manual (digitar itens)</button>
      <button type="button" onclick="alert('Leitura de nota chega em breve.')" style="flex:1;background:transparent;border:1px solid var(--verde-claro);color:var(--verde-claro);padding:.5rem;border-radius:6px;cursor:pointer;font-weight:500">Com nota (em breve)</button>
    </div>
    <button type="button" onclick="abastHide('abast-nova')" style="background:transparent;border:none;color:var(--verde-claro);cursor:pointer;font-size:.85rem">Cancelar</button>
  </div>
  <div id="abast-manual" style="display:none;margin-top:1rem;padding:1rem;background:var(--card);border:1px solid var(--borda);border-radius:6px">
    <h4 style="margin-top:0;margin-bottom:.8rem">Nova compra manual</h4>
    <form method="post" action="/painel/produtos/abastecimento/criar">
      <label>Data da compra</label><input type="date" name="data_compra" style="width:100%">
      <label>De onde comprou?</label>
      <select name="origem_id" style="width:100%">
        <option value="">Selecione uma origem</option>
        {% for o in origens %}<option value="{{ o.id }}">{{ o.nome }}</option>{% endfor %}
      </select>
      <button type="button" onclick="abastShowOrigem()" style="background:transparent;border:1px solid var(--verde-claro);color:var(--verde-claro);padding:.3rem .6rem;cursor:pointer;font-size:.85rem;margin-top:.3rem">+ Nova origem</button>
      <button style="background:var(--verde);color:#fff;padding:.5rem 1rem;border:0;border-radius:6px;cursor:pointer;margin-top:.8rem;width:100%;font-weight:500">Criar compra</button>
    </form>
    <button type="button" onclick="abastHide('abast-manual')" style="background:transparent;border:none;color:var(--verde-claro);cursor:pointer;margin-top:.5rem;font-size:.85rem">Cancelar</button>
  </div>
  <div id="abast-origem" style="display:none;margin-top:1rem;padding:1rem;background:#1c1c1f;border:1px solid var(--borda);border-radius:6px">
    <h4 style="margin-top:0;margin-bottom:.6rem">Nova origem</h4>
    <form method="post" action="/painel/produtos/abastecimento/origem">
      <label>Nome</label><input name="nome" required placeholder="ex: CEASA, Sítio do João" style="width:100%">
      <label>Contato (opcional)</label><input name="contato" placeholder="tel, email, whatsapp" style="width:100%">
      <button style="background:var(--verde);color:#fff;padding:.4rem .8rem;border:0;border-radius:4px;cursor:pointer;margin-top:.5rem;font-weight:500">Criar origem</button>
    </form>
    <button type="button" onclick="abastHide('abast-origem')" style="background:transparent;border:none;color:var(--verde-claro);cursor:pointer;font-size:.85rem;margin-top:.5rem">Cancelar</button>
  </div>
</div>
<script>
function abastShowNova(){ document.getElementById('abast-nova').style.display='block'; }
function abastShowManual(){ document.getElementById('abast-manual').style.display='block'; }
function abastShowOrigem(){ document.getElementById('abast-origem').style.display='block'; }
function abastHide(id){ document.getElementById(id).style.display='none'; }
</script>
{% endblock %}"""

_CLIENTES = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem">
    <h2 style="margin:0">👥 Clientes <span style="color:#6a6a66;font-size:.7rem;font-weight:400">· {{ total }} na base</span></h2>
    <button type="button" onclick="document.getElementById('cli-novo').style.display='block'" style="background:var(--verde);color:#fff;padding:.5rem 1rem;border:0;border-radius:8px;cursor:pointer;font-weight:600;width:auto">+ novo cliente</button>
  </div>
  {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
  {% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
  <form method="get" action="/painel/clientes" style="margin:1rem 0">
    <input name="busca" value="{{ busca }}" placeholder="🔍 buscar por nome, telefone ou CPF..." style="width:100%">
  </form>
  <div id="cli-novo" style="display:none;background:var(--card);border:1px solid var(--borda);border-radius:8px;padding:1rem;margin-bottom:1rem">
    <h4 style="margin-top:0">Novo cliente</h4>
    <form method="post" action="/painel/clientes/novo">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem">
        <div><label>Nome *</label><input name="nome" required style="width:100%"></div>
        <div><label>Telefone / celular</label><input name="telefone" style="width:100%"></div>
        <div><label>CPF</label><input name="cpf" style="width:100%"></div>
        <div><label>E-mail</label><input name="email" style="width:100%"></div>
        <div><label>Aniversário</label><input type="date" name="aniversario" style="width:100%"></div>
        <div><label>Obs</label><input name="obs" style="width:100%"></div>
      </div>
      <div style="display:flex;gap:.5rem;margin-top:.7rem">
        <button style="background:var(--verde);color:#fff;padding:.5rem 1rem;border:0;border-radius:6px;cursor:pointer;font-weight:500;width:auto">Salvar</button>
        <button type="button" onclick="document.getElementById('cli-novo').style.display='none'" style="background:transparent;border:1px solid #555;color:#aaa;border-radius:6px;padding:.5rem 1rem;cursor:pointer;width:auto">Cancelar</button>
      </div>
    </form>
  </div>
  <div style="display:flex;flex-direction:column;gap:.5rem">
    {% if clientes %}
      {% for c in clientes %}
      <a href="/painel/clientes/{{ c.id }}" style="display:flex;justify-content:space-between;align-items:center;background:#1c1c1f;border:1px solid var(--borda);border-radius:8px;padding:.7rem .9rem;text-decoration:none">
        <div><div style="color:var(--txt)">{{ c.nome }}</div><div class="mut" style="font-size:.8rem">{% if c.telefone %}📱 {{ c.telefone }}{% endif %}{% if c.cpf %} · CPF {{ c.cpf }}{% endif %}{% if c.obs %} · {{ c.obs }}{% endif %}</div></div>
        <span style="color:var(--verde-claro);font-size:.85rem">ver →</span>
      </a>
      {% endfor %}
    {% else %}
    <p class="mut">{% if busca %}Nenhum cliente pra "{{ busca }}".{% else %}Nenhum cliente ainda. As vendas de balcão vão populando aqui, ou cadastre no botão acima.{% endif %}</p>
    {% endif %}
  </div>
</div>
{% endblock %}"""


_CLIENTE_DETALHE = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <a href="/painel/clientes" style="color:var(--verde-claro);text-decoration:none;font-size:.85rem">← Clientes</a>
  {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
  {% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
  <h2 style="margin:.4rem 0">{{ cliente.nome }}</h2>
  <div style="display:flex;gap:1.5rem;flex-wrap:wrap;margin-bottom:1rem">
    <div><div class="mut" style="font-size:.75rem">Compras</div><div style="font-size:1.1rem">{{ resumo.n }}</div></div>
    <div><div class="mut" style="font-size:.75rem">Total</div><div style="font-size:1.1rem;color:var(--verde-claro)">R$ {{ "%.2f"|format(resumo.total_centavos/100) }}</div></div>
    <div><div class="mut" style="font-size:.75rem">Ticket médio</div><div style="font-size:1.1rem">R$ {{ "%.2f"|format(resumo.ticket_centavos/100) }}</div></div>
    <div><div class="mut" style="font-size:.75rem">Última</div><div style="font-size:1.1rem">{{ resumo.ultima or '—' }}</div></div>
  </div>
  <details style="margin-bottom:1rem"><summary style="cursor:pointer;color:#b4b2a9">✏️ Editar cadastro</summary>
    <form method="post" action="/painel/clientes/{{ cliente.id }}/editar" style="margin-top:.7rem">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem">
        <div><label>Nome</label><input name="nome" value="{{ cliente.nome or '' }}" style="width:100%"></div>
        <div><label>Telefone</label><input name="telefone" value="{{ cliente.telefone or '' }}" style="width:100%"></div>
        <div><label>CPF</label><input name="cpf" value="{{ cliente.cpf or '' }}" style="width:100%"></div>
        <div><label>E-mail</label><input name="email" value="{{ cliente.email or '' }}" style="width:100%"></div>
        <div><label>Aniversário</label><input type="date" name="aniversario" value="{{ cliente.aniversario or '' }}" style="width:100%"></div>
        <div><label>Obs</label><input name="obs" value="{{ cliente.obs or '' }}" style="width:100%"></div>
      </div>
      <button style="background:var(--verde);color:#fff;padding:.5rem 1rem;border:0;border-radius:6px;cursor:pointer;margin-top:.7rem;width:auto">Salvar</button>
    </form>
  </details>
  {% if fiados %}
  <div style="background:var(--card);border:1px solid #3a2f17;border-radius:10px;padding:.8rem 1rem;margin-bottom:1rem">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.4rem">
      <span style="color:#e0b878;font-weight:600">Em aberto (fiado)</span>
      <span style="color:#e0b878;font-weight:700">R$ {{ "%.2f"|format(fiado_total/100) }}</span>
    </div>
    {% for f in fiados %}
    <div style="display:flex;justify-content:space-between;align-items:center;padding:.45rem 0;border-top:1px solid #242426">
      <div style="font-size:.82rem;color:#cfcfcf">Fiado #{{ f.id }} <span style="color:#888;font-size:.74rem">· vence {{ f.vencimento }}</span><br><span style="color:var(--txt)">R$ {{ "%.2f"|format(f.valor_centavos/100) }}</span></div>
      <div style="display:flex;gap:.4rem;align-items:center">
        {% if f.link %}<a href="{{ f.link }}" target="_blank" style="color:#c99536;font-size:.76rem">link Pix ↗</a>{% else %}<form method="post" action="/painel/clientes/{{ cliente.id }}/fiado/{{ f.id }}/cobrar" style="display:inline"><button style="background:none;border:1px solid #c99536;color:#c99536;border-radius:5px;padding:.25rem .55rem;cursor:pointer;font-size:.74rem;width:auto">cobrar Pix</button></form>{% endif %}
        <form method="post" action="/painel/clientes/{{ cliente.id }}/fiado/{{ f.id }}/baixar" style="display:inline" onsubmit="return confirm('Confirmar recebimento deste fiado?')"><button style="background:none;border:1px solid var(--verde);color:var(--verde-claro);border-radius:5px;padding:.25rem .55rem;cursor:pointer;font-size:.74rem;width:auto">dar baixa</button></form>
      </div>
    </div>
    {% endfor %}
  </div>
  {% endif %}
  <h3 style="margin:0 0 .5rem">Histórico de compras</h3>
  {% if compras %}
  <div style="display:flex;flex-direction:column;gap:.4rem">
    {% for v in compras %}
    <div style="display:flex;justify-content:space-between;background:#1c1c1f;border:1px solid var(--borda);border-radius:6px;padding:.5rem .8rem">
      <div><span class="mut" style="font-size:.8rem">{{ v.data }}</span>{% if v.pagamento %}<span class="mut" style="font-size:.72rem"> · {{ v.pagamento }}</span>{% endif %}</div>
      <div style="color:#cfcfcf">R$ {{ "%.2f"|format(v.valor_centavos/100) }}</div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <p class="mut">Sem compras registradas ainda.</p>
  {% endif %}
  <form method="post" action="/painel/clientes/{{ cliente.id }}/arquivar" style="margin-top:1.2rem" onsubmit="return confirm('Arquivar este cliente?')">
    <button style="background:transparent;border:1px solid #3a2a2a;color:#d98a8a;padding:.4rem .8rem;border-radius:6px;cursor:pointer;font-size:.85rem;width:auto">Arquivar cliente</button>
  </form>
</div>
{% endblock %}"""


_PDV = """{% extends "base" %}{% block conteudo %}
<style>
.prod-inp{width:100%;box-sizing:border-box;padding:.5rem;background:var(--bg);border:1px solid var(--borda);color:#f4f4f4;border-radius:6px;margin:.25rem 0 .6rem}
</style>
<div class="card larga" style="max-width:560px;margin:0 auto">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
    <h2 style="margin:0">\U0001F9FE Caixa</h2>
    <a href="/painel/produtos" style="color:#8a8a85;text-decoration:none;font-size:.85rem">← Produtos</a>
  </div>
  {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}

  <div style="position:relative;margin-bottom:.6rem">
    <input id="pdv-prod-busca" placeholder="\U0001F50D buscar produto pra adicionar" autocomplete="off" oninput="pdvProdBusca(this.value)" class="prod-inp" style="margin:0">
    <div id="pdv-prod-sug" style="display:none;position:absolute;left:0;right:0;top:100%;background:#1c1c1f;border:1px solid var(--borda);border-top:0;border-radius:0 0 7px 7px;z-index:20;max-height:190px;overflow:auto"></div>
  </div>

  <div style="color:#888;font-size:.72rem;margin:.6rem 0 .3rem">Venda</div>
  <div id="pdv-itens" style="margin-bottom:.6rem"></div>

  <div style="position:relative;margin-bottom:.5rem">
    <input id="pdv-cli-busca" placeholder="Cliente — CPF, telefone ou nome" autocomplete="off" oninput="pdvCliBusca(this.value)" class="prod-inp" style="margin:0">
    <div id="pdv-cli-sug" style="display:none;position:absolute;left:0;right:0;top:100%;background:#1c1c1f;border:1px solid var(--borda);border-top:0;border-radius:0 0 7px 7px;z-index:20;max-height:170px;overflow:auto"></div>
    <div id="pdv-cli-sel" style="display:none;margin-top:.4rem;font-size:.82rem;color:var(--verde-claro)"></div>
    <div id="pdv-cli-novo" style="display:none;margin-top:.4rem;gap:.4rem;grid-template-columns:1fr 1fr">
      <input id="pdv-novo-nome" placeholder="Nome do novo cliente" class="prod-inp" style="margin:0">
      <input id="pdv-novo-cpf" placeholder="CPF (opcional)" class="prod-inp" style="margin:0">
    </div>
  </div>

  <div style="color:#888;font-size:.72rem;margin-bottom:.3rem">Pagamento</div>
  <div id="pdv-pag" class="abas" style="margin-bottom:.7rem">
    <span class="aba on" data-pag="dinheiro" onclick="pdvPag(this)">Dinheiro</span>
    <span class="aba" data-pag="pix" onclick="pdvPag(this)">Pix</span>
    <span class="aba" data-pag="cartao" onclick="pdvPag(this)">Cartão</span>
    <span class="aba" data-pag="fiado" onclick="pdvPag(this)">Fiado</span>
  </div>

  <div id="pdv-din" style="display:flex;gap:.5rem;margin-bottom:.5rem">
    <div style="flex:1"><div style="font-size:.72rem;color:#888">Recebido</div><input id="pdv-recebido" type="number" step="0.01" min="0" oninput="pdvRender()" class="prod-inp" style="margin:.15rem 0 0"></div>
    <div style="flex:1"><div style="font-size:.72rem;color:#888">Desconto R$</div><input id="pdv-desc" type="number" step="0.01" min="0" value="0" oninput="pdvRender()" class="prod-inp" style="margin:.15rem 0 0"></div>
  </div>
  <div id="pdv-fiado-box" style="display:none;margin-bottom:.5rem">
    <div style="font-size:.72rem;color:#e0b878">Vencimento do fiado</div>
    <input id="pdv-venc" type="date" class="prod-inp" style="margin:.15rem 0 0;max-width:180px">
    <div style="font-size:.7rem;color:#888;margin-top:.2rem">Fiado exige cliente identificado.</div>
  </div>
  <div id="pdv-troco" style="display:flex;justify-content:space-between;font-size:.82rem;color:#888;margin-bottom:.6rem"><span>Troco</span><span id="pdv-troco-v" style="color:var(--txt)">R$ 0,00</span></div>

  <div id="pdv-erro" style="color:#d98a8a;font-size:.82rem;margin-bottom:.5rem;display:none"></div>
  <div style="display:flex;justify-content:space-between;align-items:baseline;border-top:1px solid var(--borda);padding-top:.7rem">
    <span style="color:#888">Total</span><span id="pdv-total" style="color:var(--verde-claro);font-size:1.5rem;font-weight:700">R$ 0,00</span>
  </div>
  <button type="button" onclick="pdvFinalizar()" id="pdv-finalizar" style="width:100%;background:var(--verde);color:#fff;padding:.75rem;border:0;border-radius:9px;cursor:pointer;font-weight:700;margin-top:.8rem;font-size:1rem">Finalizar venda</button>

  <div id="pdv-recibo" style="display:none;margin-top:1rem;background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:1rem"></div>
</div>

<div class="card larga" style="max-width:560px;margin:1rem auto 0">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.7rem">
    <h3 style="margin:0">Vendas de hoje</h3>
    <span class="mut" style="font-size:.8rem">{{ vendas|length }} venda(s)</span>
  </div>
  {% if vendas %}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-bottom:.6rem">
    {% for t in totais %}
    <div style="background:var(--card);border:1px solid var(--borda);border-radius:8px;padding:.5rem .7rem">
      <div class="mut" style="font-size:.72rem;text-transform:capitalize">{{ t.metodo }} · {{ t.n }}</div>
      <div style="font-size:1rem">R$ {{ "%.2f"|format(t.total_centavos/100) }}</div>
    </div>
    {% endfor %}
    {% if fiado_n %}
    <div style="background:#1c1710;border:1px solid #3a2f17;border-radius:8px;padding:.5rem .7rem">
      <div style="font-size:.72rem;color:#e0b878">Fiado · {{ fiado_n }} (a receber)</div>
      <div style="font-size:1rem;color:#e0b878">R$ {{ "%.2f"|format(fiado_total/100) }}</div>
    </div>
    {% endif %}
  </div>
  <div style="display:flex;justify-content:space-between;align-items:baseline;border-top:1px solid var(--borda);padding-top:.5rem;margin-bottom:.7rem">
    <span class="mut">Recebido hoje</span><span style="color:var(--verde-claro);font-size:1.2rem;font-weight:700">R$ {{ "%.2f"|format(recebido_centavos/100) }}</span>
  </div>
  <div class="mut" style="font-size:.72rem;margin-bottom:.3rem">Últimas vendas</div>
  {% for v in vendas %}
  <a href="{% if v.tipo=='paga' %}/painel/pdv/venda/{{ v.id }}{% else %}/painel/clientes/{{ v.cliente_id }}{% endif %}" style="display:flex;justify-content:space-between;align-items:center;padding:.5rem 0;border-bottom:1px solid #242426;text-decoration:none">
    <div><div style="color:var(--txt);font-size:.85rem">{{ v.cliente or 'sem cliente' }}</div><div class="mut" style="font-size:.72rem">{{ v.hora.strftime('%H:%M') if v.hora else '' }} · {{ v.pagamento }}{% if v.tipo=='fiado' %} · ver ficha{% endif %}</div></div>
    <div style="{% if v.tipo=='fiado' %}color:#e0b878{% else %}color:#cfcfcf{% endif %};font-size:.9rem">R$ {{ "%.2f"|format(v.valor_centavos/100) }}</div>
  </a>
  {% endfor %}
  {% else %}
  <p class="mut">Nenhuma venda hoje ainda.</p>
  {% endif %}
</div>
<script>
window.PDV_PROD = {{ produtos_json|tojson }};
window.PDV_ADD = {{ add }};
var PDV_CART=[], PDV_CLI=null, PDV_CLI_RES=[], PDV_CLI_T=null, PDV_PAG='dinheiro';
function pdvFmt(n){ return 'R$ '+(Math.round(n*100)/100).toFixed(2).replace('.',','); }
function pdvProdBusca(q){ q=(q||'').trim().toLowerCase(); var sug=document.getElementById('pdv-prod-sug'); if(q.length<1){ sug.style.display='none'; return; } var res=window.PDV_PROD.filter(function(p){return (p.nome||'').toLowerCase().indexOf(q)>=0;}).slice(0,12); var h=''; res.forEach(function(p){ h+='<div onclick="pdvAdd('+p.id+')" style="padding:.45rem .6rem;border-bottom:1px solid #242426;cursor:pointer;font-size:.82rem;color:var(--txt);display:flex;justify-content:space-between"><span>'+p.nome+'</span><span style="color:#6a8a7a;font-size:.72rem">'+pdvFmt(p.preco)+'/'+p.unidade+' · '+p.saldo+'</span></div>'; }); if(!res.length) h='<div style="padding:.5rem .6rem;color:#888;font-size:.8rem">nada encontrado</div>'; sug.innerHTML=h; sug.style.display='block'; }
function pdvAdd(id){ var p=window.PDV_PROD.find(function(x){return x.id==id;}); if(!p) return; var e=PDV_CART.find(function(x){return x.id==id;}); if(e){ e.qtd++; } else { PDV_CART.push({id:p.id,nome:p.nome,unidade:p.unidade,preco:(typeof p.preco==='number'?p.preco:0),qtd:1}); } document.getElementById('pdv-prod-sug').style.display='none'; document.getElementById('pdv-prod-busca').value=''; document.getElementById('pdv-erro').style.display='none'; pdvRender(); }
function pdvQtd(id,d){ var e=PDV_CART.find(function(x){return x.id==id;}); if(!e) return; e.qtd=Math.round((e.qtd+d)*1000)/1000; if(e.qtd<=0){ PDV_CART=PDV_CART.filter(function(x){return x.id!=id;}); } pdvRender(); }
function pdvPreco(id,v){ var e=PDV_CART.find(function(x){return x.id==id;}); if(e){ e.preco=parseFloat(String(v).replace(',','.'))||0; } pdvRender(); }
function pdvRender(){ var box=document.getElementById('pdv-itens'); var sub=0,h=''; if(!PDV_CART.length){ box.innerHTML='<div style="color:#888;text-align:center;padding:.8rem;font-size:.85rem">Carrinho vazio — busque um produto acima.</div>'; } else { PDV_CART.forEach(function(it){ var lt=it.preco*it.qtd; sub+=lt; h+='<div style="display:flex;align-items:center;gap:.5rem;padding:.4rem 0;border-bottom:1px solid #242426">'+'<div style="flex:1;min-width:0"><div style="color:var(--txt);font-size:.85rem">'+it.nome+'</div><input type="number" step="0.01" value="'+it.preco.toFixed(2)+'" onchange="pdvPreco('+it.id+',this.value)" style="width:78px;background:var(--bg);border:1px solid var(--borda);color:#cfcfcf;border-radius:5px;padding:.2rem .3rem;font-size:.72rem;margin-top:.2rem"> <span style="color:#6a8a7a;font-size:.68rem">/'+it.unidade+'</span></div>'+'<div style="display:flex;align-items:center;gap:.3rem"><span onclick="pdvQtd('+it.id+',-1)" style="width:22px;height:22px;border-radius:5px;background:#1c1c1f;border:1px solid #3a3a3d;color:#b4b2a9;display:flex;align-items:center;justify-content:center;cursor:pointer">-</span><span style="color:var(--txt);font-size:.8rem;min-width:44px;text-align:center">'+it.qtd+' '+it.unidade+'</span><span onclick="pdvQtd('+it.id+',1)" style="width:22px;height:22px;border-radius:5px;background:#1c1c1f;border:1px solid #3a3a3d;color:#b4b2a9;display:flex;align-items:center;justify-content:center;cursor:pointer">+</span></div>'+'<div style="color:#cfcfcf;font-size:.8rem;min-width:64px;text-align:right">'+pdvFmt(lt)+'</div>'+'</div>'; }); box.innerHTML=h; } var desc=parseFloat(String(document.getElementById('pdv-desc').value).replace(',','.'))||0; var tot=sub-desc; if(tot<0)tot=0; document.getElementById('pdv-total').textContent=pdvFmt(tot); var rec=parseFloat(String(document.getElementById('pdv-recebido').value).replace(',','.'))||0; var troco=rec-tot; document.getElementById('pdv-troco-v').textContent=pdvFmt(troco>0?troco:0); }
function pdvPag(el){ document.querySelectorAll('#pdv-pag .aba').forEach(function(x){x.classList.remove('on');}); el.classList.add('on'); PDV_PAG=el.getAttribute('data-pag'); var fiado=PDV_PAG==='fiado'; var din=PDV_PAG==='dinheiro'; document.getElementById('pdv-fiado-box').style.display=fiado?'block':'none'; document.getElementById('pdv-din').style.display=fiado?'none':'flex'; document.getElementById('pdv-troco').style.display=din?'flex':'none'; }
function pdvCliBusca(q){ q=(q||'').trim(); PDV_CLI=null; document.getElementById('pdv-cli-sel').style.display='none'; if(PDV_CLI_T) clearTimeout(PDV_CLI_T); var sug=document.getElementById('pdv-cli-sug'); if(q.length<2){ sug.style.display='none'; return; } PDV_CLI_T=setTimeout(function(){ fetch('/painel/clientes/buscar?q='+encodeURIComponent(q)).then(function(r){return r.json();}).then(function(d){ PDV_CLI_RES=d.clientes||[]; var h=''; PDV_CLI_RES.forEach(function(c,i){ h+='<div onclick="pdvCliPick('+i+')" style="padding:.45rem .6rem;border-bottom:1px solid #242426;cursor:pointer;font-size:.82rem;color:var(--txt)">'+c.nome+'<span style="color:#6a8a7a;font-size:.72rem">'+(c.telefone?(' · '+c.telefone):'')+(c.cpf?(' · CPF '+c.cpf):'')+'</span></div>'; }); h+='<div onclick="pdvCliNovo()" style="padding:.45rem .6rem;cursor:pointer;font-size:.82rem;color:var(--verde-claro)">+ cadastrar novo cliente</div>'; sug.innerHTML=h; sug.style.display='block'; }).catch(function(){}); }, 250); }
function pdvCliPick(i){ var c=PDV_CLI_RES[i]; if(!c) return; PDV_CLI={id:c.id,nome:c.nome,telefone:c.telefone,cpf:c.cpf}; document.getElementById('pdv-cli-busca').value=c.nome; document.getElementById('pdv-cli-sug').style.display='none'; document.getElementById('pdv-cli-novo').style.display='none'; var sel=document.getElementById('pdv-cli-sel'); sel.innerHTML='✓ '+c.nome+' <a onclick="pdvCliLimpar()" style="color:#d98a8a;cursor:pointer;margin-left:.4rem">(trocar)</a>'; sel.style.display='block'; }
function pdvCliNovo(){ document.getElementById('pdv-cli-sug').style.display='none'; var q=document.getElementById('pdv-cli-busca').value||''; document.getElementById('pdv-cli-novo').style.display='grid'; var digs=q.replace(/[^0-9]/g,''); if(digs.length>=11){ document.getElementById('pdv-novo-cpf').value=q; document.getElementById('pdv-novo-nome').value=''; } else { document.getElementById('pdv-novo-nome').value=q; } PDV_CLI=null; }
function pdvCliLimpar(){ PDV_CLI=null; document.getElementById('pdv-cli-sel').style.display='none'; document.getElementById('pdv-cli-busca').value=''; document.getElementById('pdv-cli-novo').style.display='none'; document.getElementById('pdv-novo-nome').value=''; document.getElementById('pdv-novo-cpf').value=''; }
function pdvClienteInfo(){ if(PDV_CLI&&PDV_CLI.id) return {cliente_id:PDV_CLI.id, cliente_nome:PDV_CLI.nome}; var nn=(document.getElementById('pdv-novo-nome').value||'').trim(); var nc=(document.getElementById('pdv-novo-cpf').value||'').trim(); var o={}; if(nn) o.cliente_nome=nn; if(nc) o.cliente_cpf=nc; return o; }
function pdvFinalizar(){ if(!PDV_CART.length){ return; } var e=document.getElementById('pdv-erro'); var ci=pdvClienteInfo(); if(PDV_PAG==='fiado' && !ci.cliente_id && !ci.cliente_nome){ e.textContent='Fiado exige um cliente identificado.'; e.style.display='block'; return; } var btn=document.getElementById('pdv-finalizar'); btn.disabled=true; btn.textContent='Registrando...'; var desc=parseFloat(String(document.getElementById('pdv-desc').value).replace(',','.'))||0; var payload={ itens:PDV_CART.map(function(it){ return {produto_id:it.id, quantidade:it.qtd, preco_unit_centavos:Math.round(it.preco*100)}; }), pagamento:PDV_PAG, desconto_centavos:Math.round(desc*100) }; for(var k in ci){ payload[k]=ci[k]; } if(PDV_PAG==='fiado'){ payload.vencimento=document.getElementById('pdv-venc').value||''; } fetch('/painel/produtos/vender',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(function(r){return r.json();}).then(function(d){ if(d.ok){ pdvRecibo(d,ci); } else { e.textContent=d.erro||'erro'; e.style.display='block'; btn.disabled=false; btn.textContent='Finalizar venda'; } }).catch(function(){ e.textContent='erro de conexão'; e.style.display='block'; btn.disabled=false; btn.textContent='Finalizar venda'; }); }
function pdvRecibo(d,ci){ var linhas=PDV_CART.map(function(it){ return '<div style="display:flex;justify-content:space-between;font-size:.8rem;color:#cfcfcf"><span>'+it.qtd+' '+it.unidade+' '+it.nome+'</span><span>'+pdvFmt(it.preco*it.qtd)+'</span></div>'; }).join(''); var extra=''; if(d.fiado){ extra='<div style="color:#e0b878;font-size:.82rem;margin-top:.3rem">Fiado · vence '+(d.vencimento||'')+'</div>'; } var box=document.getElementById('pdv-recibo'); box.innerHTML='<div style="text-align:center;color:var(--verde-claro);font-weight:700;margin-bottom:.5rem">✓ Venda registrada</div>'+linhas+'<div style="display:flex;justify-content:space-between;border-top:1px solid var(--borda);margin-top:.5rem;padding-top:.5rem;color:var(--txt);font-weight:600"><span>Total</span><span>'+pdvFmt((d.total_centavos||0)/100)+'</span></div>'+(ci.cliente_nome?('<div style="color:#888;font-size:.78rem;margin-top:.3rem">Cliente: '+ci.cliente_nome+'</div>'):'')+extra+'<div style="display:flex;gap:.5rem;margin-top:.8rem"><button type="button" onclick="window.print()" style="flex:1;background:transparent;border:1px solid #3a3a3d;color:#b4b2a9;border-radius:7px;padding:.5rem;cursor:pointer">Imprimir</button><button type="button" onclick="location.href=\\'/painel/pdv\\'" style="flex:1;background:var(--verde);color:#fff;border:0;border-radius:7px;padding:.5rem;cursor:pointer;font-weight:600">Nova venda</button></div>'; box.style.display='block'; box.scrollIntoView({behavior:'smooth'}); }
document.addEventListener('DOMContentLoaded', function(){ pdvRender(); if(window.PDV_ADD){ pdvAdd(window.PDV_ADD); } });
</script>
{% endblock %}"""

_VENDA_DETALHE = """{% extends "base" %}{% block conteudo %}
<div class="card larga" style="max-width:480px;margin:0 auto">
  <a href="/painel/pdv" style="color:#8a8a85;text-decoration:none;font-size:.85rem">← voltar ao caixa</a>
  {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin:.5rem 0 1rem">
    <div><div style="font-size:1.2rem;font-weight:600">Venda #{{ venda.id }}</div><div class="mut" style="font-size:.8rem">{{ venda.hora.strftime('%d/%m %H:%M') if venda.hora else '' }} · {{ venda.pagamento }}</div></div>
    {% if venda.cliente %}<div style="text-align:right"><div class="mut" style="font-size:.72rem">Cliente</div><div>{{ venda.cliente }}</div></div>{% endif %}
  </div>
  <div class="mut" style="font-size:.72rem;margin-bottom:.3rem">{{ itens|length }} item(ns)</div>
  {% for it in itens %}
  <div style="display:flex;justify-content:space-between;padding:.45rem 0;border-bottom:1px solid #242426">
    <div style="font-size:.88rem;color:var(--txt)">{{ it.descricao }} <span class="mut" style="font-size:.72rem">· {{ it.quantidade }} {{ it.unidade }} × R$ {{ "%.2f"|format(it.vu/100) }}</span></div>
    <div style="font-size:.88rem">R$ {{ "%.2f"|format(it.vt/100) }}</div>
  </div>
  {% endfor %}
  <div style="display:flex;justify-content:space-between;color:#888;font-size:.82rem;padding-top:.5rem"><span>Subtotal</span><span>R$ {{ "%.2f"|format(venda.subtotal_centavos/100) }}</span></div>
  {% if venda.desconto_centavos %}<div style="display:flex;justify-content:space-between;color:#888;font-size:.82rem"><span>Desconto</span><span>− R$ {{ "%.2f"|format(venda.desconto_centavos/100) }}</span></div>{% endif %}
  <div style="display:flex;justify-content:space-between;align-items:baseline;border-top:1px solid var(--borda);padding-top:.5rem;margin-top:.3rem"><span class="mut">Total</span><span style="color:var(--verde-claro);font-size:1.3rem;font-weight:700">R$ {{ "%.2f"|format(venda.valor_centavos/100) }}</span></div>
</div>
{% endblock %}"""

_EMPRESA = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem">
    <div><h2 style="margin:0">🏢 Empresa</h2>
      <div class="mut" style="font-size:.82rem">{{ empresa_nome }}{% if empresa_doc %} · CNPJ {{ empresa_doc }}{% endif %}<a href="/painel/empresa/dados" style="color:var(--verde-claro);font-size:.72rem;margin-left:.4rem;text-decoration:none">editar dados ›</a></div></div>
    <span style="background:#15301f;color:var(--verde-claro);font-size:.72rem;font-weight:600;padding:.25rem .7rem;border-radius:14px;border:1px solid var(--verde)44">Módulo PJ ativo</span>
  </div>

  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6rem;margin-top:1rem">
    <div class="card" style="margin:0"><div class="mut" style="font-size:.72rem">A pagar ({{ res.dias }} dias)</div>
      <div style="font-size:1.3rem;color:#e07a5f;font-weight:600">{{ res.a_pagar_centavos|brl }}</div>
      <div class="mut" style="font-size:.68rem">{{ res.n_pagar }} título(s)</div></div>
    <div class="card" style="margin:0"><div class="mut" style="font-size:.72rem">A receber ({{ res.dias }} dias)</div>
      <div style="font-size:1.3rem;color:var(--verde-claro);font-weight:600">{{ res.a_receber_centavos|brl }}</div>
      <div class="mut" style="font-size:.68rem">{{ res.n_receber }} título(s)</div></div>
    <div class="card" style="margin:0"><div class="mut" style="font-size:.72rem">Atrasados</div>
      <div style="font-size:1.3rem;color:#f0c05a;font-weight:600">{{ res.n_atrasados }}</div>
      <div class="mut" style="font-size:.68rem">{{ res.atrasados_centavos|brl }}</div></div>
    <div class="card" style="margin:0;border-color:var(--verde)33"><div class="mut" style="font-size:.72rem">Saldo projetado (30d)</div>
      <div style="font-size:1.3rem;color:var(--verde-claro);font-weight:600">{{ fluxo.saldo_projetado_centavos|brl }}</div>
      <div class="mut" style="font-size:.68rem">caixa + previsto</div></div>
  </div>
</div>

<div class="card larga">
  <div style="display:flex;justify-content:space-between"><strong>Fluxo de caixa projetado</strong>
    <span class="mut" style="font-size:.72rem">saldo hoje {{ fluxo.saldo_atual_centavos|brl }}</span></div>
  <table style="width:100%;margin-top:.6rem;font-size:.85rem">
    <tr class="mut" style="font-size:.72rem"><td>Semana</td><td style="text-align:right">Entra</td><td style="text-align:right">Sai</td><td style="text-align:right">Saldo previsto</td></tr>
    {% for p in fluxo.pontos %}<tr><td>até {{ p.ate.strftime('%d/%m') }}</td>
      <td style="text-align:right;color:var(--verde-claro)">{{ p.receber_centavos|brl }}</td>
      <td style="text-align:right;color:#e07a5f">{{ p.pagar_centavos|brl }}</td>
      <td style="text-align:right;font-weight:600">{{ p.saldo_centavos|brl }}</td></tr>{% endfor %}
  </table>
</div>

<div class="card larga">
  <div style="display:flex;justify-content:space-between"><strong>DRE do mês (simplificado)</strong>
    <span class="mut" style="font-size:.72rem">{{ '%02d'|format(dre.mes) }}/{{ dre.ano }}</span></div>
  <table style="width:100%;margin-top:.6rem;font-size:.9rem">
    <tr><td class="mut">Receitas</td><td style="text-align:right;color:var(--verde-claro)">{{ dre.receitas_centavos|brl }}</td></tr>
    <tr><td class="mut">(−) Despesas</td><td style="text-align:right">{{ dre.despesas_centavos|brl }}</td></tr>
    <tr style="border-top:1px solid var(--borda)"><td style="font-weight:600">Resultado</td>
      <td style="text-align:right;font-weight:600;color:{{ 'var(--verde-claro)' if dre.resultado_centavos>=0 else '#e07a5f' }}">{{ dre.resultado_centavos|brl }} · {{ dre.margem_pct }}%</td></tr>
  </table>

  {% if dre.a_definir_n %}
  <div style="background:#2b2416;border:1px solid #f0c05a44;border-radius:8px;padding:.55rem .9rem;margin-top:.7rem;color:#f0c05a;font-size:.78rem;line-height:1.35">
    ⚠️ <b>{{ dre.a_definir_centavos|brl }}</b> em <b>{{ dre.a_definir_n }}</b> lançamento(s) ainda a classificar <b>não entraram</b> neste DRE.
    <a href="/painel/financeiro?mes={{ dre.mes }}&natureza=a_definir" style="color:#f0c05a;text-decoration:underline">classificar agora →</a>
  </div>
  {% endif %}

  <div class="mut" style="font-size:.75rem;margin-top:.8rem">Relatório do contador: <a href="/painel/empresa/contador.csv?ano={{ dre.ano }}&mes={{ dre.mes }}" style="color:var(--verde-claro)">baixar planilha ({{ '%02d'|format(dre.mes) }}/{{ dre.ano }}) ↓</a></div>
</div>

<div class="card larga">
  <div style="display:flex;justify-content:space-between;align-items:center"><strong>Títulos a pagar e receber</strong></div>
  <form method="post" action="/painel/empresa/titulo" style="display:grid;grid-template-columns:1fr 2fr 1fr 1fr auto;gap:.5rem;margin:.7rem 0;align-items:end">
    <label style="font-size:.72rem;color:#8a938a">Tipo<select name="tipo" style="width:100%">
      <option value="pagar">A pagar</option><option value="receber">A receber</option></select></label>
    <label style="font-size:.72rem;color:#8a938a">Descrição<input name="descricao" required placeholder="Ex: Aluguel do ponto" style="width:100%"></label>
    <label style="font-size:.72rem;color:#8a938a">Valor R$<input name="valor" required inputmode="decimal" placeholder="0,00" style="width:100%"></label>
    <label style="font-size:.72rem;color:#8a938a">Vencimento<input name="vencimento" type="date" required style="width:100%"></label>
    <button type="submit" style="background:var(--verde);color:#fff;border:0;border-radius:6px;padding:.55rem .8rem;font-weight:600;cursor:pointer">+ Add</button>
  </form>
  <label style="font-size:.72rem;color:#8a938a;display:flex;gap:.4rem;align-items:center;margin-bottom:.6rem"><input type="checkbox" form="_nada" disabled style="width:auto"> <span class="mut">Contraparte e recorrência aparecem ao detalhar o título.</span></label>
  {% if titulos %}<table style="width:100%;font-size:.85rem">
    <tr class="mut" style="font-size:.72rem"><td>Venc.</td><td>Descrição</td><td>Tipo</td><td style="text-align:right">Valor</td><td></td></tr>
    {% for t in titulos %}<tr style="border-top:1px solid #232325">
      <td style="{% if t.atrasado %}color:#f0c05a{% endif %}">{{ t.vencimento.strftime('%d/%m') }}{% if t.atrasado %} ⚠{% endif %}</td>
      <td>{{ t.descricao }}{% if t.contraparte %} <span class="mut">· {{ t.contraparte }}</span>{% endif %}</td>
      <td>{% if t.tipo=='pagar' %}<span style="color:#e07a5f">pagar</span>{% else %}<span style="color:var(--verde-claro)">receber</span>{% endif %}</td>
      <td style="text-align:right;font-weight:600">{{ t.valor_centavos|brl }}</td>
      <td style="text-align:right;white-space:nowrap">
        <form method="post" action="/painel/empresa/titulo/{{ t.id }}/baixa" style="display:inline"><button style="background:none;border:0;color:var(--verde-claro);cursor:pointer;font-size:.78rem">dar baixa ✓</button></form>
        {% if t.tipo=='receber' %}{% if t.cobranca_link_url %}<a href="{{ t.cobranca_link_url }}" target="_blank" style="color:#c99536;font-size:.78rem;margin-left:.5rem">link Pix ↗</a>{% else %}<form method="post" action="/painel/empresa/titulo/{{ t.id }}/cobrar" style="display:inline"><button style="background:none;border:0;color:#c99536;cursor:pointer;font-size:.78rem;margin-left:.5rem">cobrar via Pix →</button></form>{% endif %}{% endif %}
        <button type="button" data-desc="{{ t.descricao }}" onclick="titEditarDesc({{ t.id }}, this)" title="corrigir a descrição" style="background:none;border:0;color:#8a938a;cursor:pointer;font-size:.78rem;margin-left:.5rem">editar</button>
        <form method="post" action="/painel/empresa/titulo/{{ t.id }}/apagar" style="display:inline" onsubmit="return confirm('Apagar este título? (só some da lista; não mexe em nada já pago)')"><button title="apagar título" style="background:none;border:0;color:#8a3636;cursor:pointer;font-size:.78rem;margin-left:.5rem">apagar ✕</button></form>
      </td></tr>{% endfor %}
  </table>{% else %}<div class="mut" style="font-size:.85rem">Nenhum título em aberto. Adicione acima — ou peça pro Zaq no WhatsApp.</div>{% endif %}
  <form method="post" id="tit-desc-form" style="display:none"><input type="hidden" name="descricao" id="tit-desc-input"></form>
  <script>
  function titEditarDesc(id, btn){
    var atual = btn.getAttribute('data-desc') || '';
    var nova = prompt('Corrigir a descrição do título:', atual);
    if(nova === null) return;              // cancelou
    nova = nova.trim();
    if(!nova || nova === atual) return;    // vazio ou sem mudança
    var f = document.getElementById('tit-desc-form');
    f.action = '/painel/empresa/titulo/' + id + '/descricao';
    document.getElementById('tit-desc-input').value = nova;
    f.submit();
  }
  </script>
</div>

<div class="card larga">
  <div style="display:flex;justify-content:space-between;align-items:center"><strong>Equipe e folha</strong>
    <span class="mut" style="font-size:.72rem">folha de {{ '%02d'|format(dre.mes) }}/{{ dre.ano }}: <b style="color:#e07a5f">{{ folha.total_a_pagar_centavos|brl }}</b> · custo real ≈ {{ folha.custo_real_total_centavos|brl }}</span></div>
  <form method="post" action="/painel/empresa/funcionario" style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr auto;gap:.5rem;margin:.7rem 0;align-items:end">
    <label style="font-size:.72rem;color:#8a938a">Nome<input name="nome" required placeholder="Nome do funcionário" style="width:100%"></label>
    <label style="font-size:.72rem;color:#8a938a">Cargo<input name="cargo" placeholder="Ex: Vendedor" style="width:100%"></label>
    <label style="font-size:.72rem;color:#8a938a">Salário R$<input name="salario" required inputmode="decimal" placeholder="0,00" style="width:100%"></label>
    <label style="font-size:.72rem;color:#8a938a">Dia pgto<input name="dia" type="number" min="1" max="28" value="5" style="width:100%"></label>
    <button type="submit" style="background:var(--verde);color:#fff;border:0;border-radius:6px;padding:.55rem .8rem;font-weight:600;cursor:pointer">+ Add</button>
  </form>
  {% if folha.itens %}<table style="width:100%;font-size:.85rem">
    <tr class="mut" style="font-size:.72rem"><td>Funcionário</td><td style="text-align:right">Salário</td><td style="text-align:right">Vale</td><td style="text-align:right">A pagar</td><td style="text-align:right">Custo real</td><td></td></tr>
    {% for f in folha.itens %}<tr style="border-top:1px solid #232325">
      <td>{{ f.nome }}{% if f.pro_labore %} <span class="mut">· pró-labore</span>{% elif f.cargo %} <span class="mut">· {{ f.cargo }}</span>{% endif %}</td>
      <td style="text-align:right">{{ f.salario_centavos|brl }}</td>
      <td style="text-align:right;color:#f0c05a">{% if f.vales_centavos %}-{{ f.vales_centavos|brl }}{% else %}—{% endif %}</td>
      <td style="text-align:right;font-weight:600">{% if f.quitado %}<span style="color:var(--verde-claro)">pago ✓</span>{% else %}{{ f.a_pagar_centavos|brl }}{% endif %}</td>
      <td style="text-align:right" class="mut">{{ f.custo_real_centavos|brl }}</td>
      <td style="text-align:right;white-space:nowrap">
        {% if not f.pro_labore %}<form method="post" action="/painel/empresa/funcionario/{{ f.id }}/vale" style="display:inline"><input name="valor" inputmode="decimal" placeholder="vale R$" style="width:70px;font-size:.75rem"><button style="background:none;border:0;color:#f0c05a;cursor:pointer;font-size:.78rem">dar vale</button></form>{% endif %}
        {% if not f.quitado %}<form method="post" action="/painel/empresa/folha/pagar" style="display:inline"><input type="hidden" name="funcionario_id" value="{{ f.id }}"><button style="background:none;border:0;color:var(--verde-claro);cursor:pointer;font-size:.78rem;margin-left:.4rem">pagar ✓</button></form>{% endif %}
      </td></tr>{% endfor %}
  </table>
  <form method="post" action="/painel/empresa/folha/pagar" style="margin-top:.7rem"><button style="background:var(--verde);color:#fff;border:0;border-radius:6px;padding:.5rem 1rem;font-weight:600;cursor:pointer">Pagar folha inteira ✓</button></form>
  {% else %}<div class="mut" style="font-size:.85rem">Nenhum funcionário cadastrado. Adicione acima.</div>{% endif %}
  <div class="mut" style="font-size:.72rem;margin-top:.7rem">ℹ️ Controle gerencial de pessoal — a folha oficial (eSocial, guias, holerite) segue com seu contador, que recebe tudo no relatório mensal.</div>
</div>
{% endblock %}"""

_PROMOCOES_EM_BREVE = """{% extends "base" %}{% block conteudo %}
<div class="card" style="max-width:440px;margin:2rem auto;text-align:center">
  <div style="font-size:42px;margin-bottom:.5rem">🏷️</div>
  <h2>Promoções em breve!</h2>
  <p class="mut">O {{ fornecedor.nome }} ainda não tem ofertas ativas. Volte logo — ou aproveite a loja!</p>
  <a href="/f/{{ fornecedor.slug }}" style="display:inline-block;margin-top:1rem;background:var(--verde);color:#fff;padding:.7rem 1.4rem;border-radius:9px;text-decoration:none;font-weight:600">Ver produtos</a>
</div>
{% endblock %}"""

_ATIVAR_APP = """{% extends "base" %}{% block conteudo %}
<div class="card" style="max-width:480px;margin:0 auto">
  <h2>Ative o app financeiro 💰</h2>
  {% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
  <p class="mut" style="font-size:.9rem;line-height:1.6">
    Além da sua cesta, o Zaq controla seus gastos, lê cupom fiscal pela foto e
    organiza sua lista de compras — tudo pelo WhatsApp ou Telegram.
  </p>
  <ul style="font-size:.88rem;color:#c5d8ce;line-height:1.8;margin:.5rem 0 1rem">
    <li>📊 Controle de gastos automático</li>
    <li>🧾 Leitura de cupom fiscal por foto</li>
    <li>🛒 Lista de compras inteligente</li>
  </ul>
  <form method="post" action="/painel/ativar-app">
    <label>Escolha seu plano:</label>
    <select name="plano" style="width:100%;margin:.4rem 0 1rem;padding:.4rem;border:1px solid var(--borda);border-radius:4px;background:#0a0a0a;color:#fff">
      {% for p in planos %}
      <option value="{{ p[0] }}">{{ p[1] }} — {% if beta_gratis %}Grátis (beta){% else %}R$ {{ (p[3]/100)|n2 }}/mês{% endif %}</option>
      {% endfor %}
    </select>
    <button type="submit" style="background:#534AB7;color:#fff;padding:.7rem 1rem;border:0;border-radius:6px;cursor:pointer;width:100%;font-weight:600;font-size:.95rem">
      Ativar app financeiro
    </button>
  </form>
  <p class="mut" style="font-size:.8rem;text-align:center;margin-top:.8rem">
    Você continua com sua cesta normalmente. O app é um extra.
  </p>
</div>
{% endblock %}"""

_PAINEL_ASSINATURAS = """{% extends "base" %}{% block conteudo %}
{% if not (conta and conta[4] and conta[4] != 'zaq_cesta') %}{% include "bloco_conta" %}{% endif %}
<div class="card larga"><h2>🧺 Minhas assinaturas</h2>
<p class="mut" style="margin-top:-.3rem;margin-bottom:1rem;font-size:.86rem">Entrega recorrente: você recebe e paga por período, sem refazer o pedido.
Suas compras avulsas nas lojas ficam em <a href="/painel/meus-pedidos" style="color:var(--verde-claro)">🛍️ Meus pedidos</a>.</p>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if erro_asaas %}<div class="erro" style="margin-bottom:1rem;font-size:.85rem">⚠️ Asaas: {{ erro_asaas }}</div>{% endif %}
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
{% if assinaturas %}
{% for a in assinaturas %}
<div style="background:#1c1c1f;border:1px solid var(--borda);border-radius:8px;padding:1rem;margin-bottom:.6rem">
  <div style="display:flex;justify-content:space-between;align-items:start">
    <div style="flex:1">
      <strong>{{ a.tamanho_nome }}</strong> — <strong>R$ {{ (a.preco_centavos/100)|n2 }}</strong>
      <div class="mut" style="font-size:.85rem;margin-top:.3rem">De: {{ a.fornecedor_nome }} | Frequência: {{ a.frequencia }} | Status: <strong>{{ a.status }}</strong></div>
      <!-- Status: aguardando pagamento -->
      {% if a.status == 'aguardando_pagamento' %}
      <div style="margin-top:.6rem;padding:.6rem;background:var(--borda);border-radius:4px;border-left:3px solid #ffa500">
        <p class="mut" style="font-size:.85rem;margin:0 0 .4rem">⏳ Aguardando pagamento</p>
        <a href="/painel/assinaturas/{{ a.id }}/pagar" style="display:inline-block;background:var(--verde-claro);color:#0a0a0a;padding:.4rem .8rem;border-radius:4px;text-decoration:none;font-weight:600;font-size:.85rem;cursor:pointer">
          Pagar agora →
        </a>
      </div>
      {% endif %}

      <!-- Trocar tamanho (apenas se tem tamanhos disponíveis) -->
      {% if tamanhos_por_fornecedor.get(a.fornecedor_id) %}
      <form method="post" action="/painel/assinaturas/{{ a.id }}/trocar-tamanho" style="display:flex;gap:.5rem;margin-top:.6rem;align-items:end">
        <div style="flex:1">
          <label style="font-size:.8rem;color:#888">Mudar tamanho:</label>
          <select name="novo_tamanho_id" style="width:100%;padding:.4rem;border:1px solid var(--borda);border-radius:4px;background:#0a0a0a;color:#fff;font-size:.85rem">
            <option value="">Escolher novo tamanho...</option>
            {% for t in tamanhos_por_fornecedor[a.fornecedor_id] %}
              {% if t.id != a.tamanho_id_atual %}
              <option value="{{ t.id }}">{{ t.nome }} — R$ {{ (t.preco_centavos/100)|n2 }}</option>
              {% endif %}
            {% endfor %}
          </select>
        </div>
        <button type="submit" style="background:var(--verde-claro);color:#0a0a0a;border:0;padding:.4rem .8rem;border-radius:4px;cursor:pointer;font-weight:600;font-size:.85rem;white-space:nowrap">Trocar</button>
      </form>
      <p class="mut" style="font-size:.75rem;margin-top:.3rem">Muda a partir da próxima entrega.</p>
      {% endif %}
    </div>
    <form method="post" action="/painel/assinaturas/{{ a.id }}/status" style="display:inline">
      <select name="novo_status" onchange="this.form.submit()" style="padding:.3rem;border:1px solid var(--borda);border-radius:4px;background:#0a0a0a;color:#fff;font-size:.85rem">
        <option value="">Ações</option>
        {% if a.status != 'ativa' %}<option value="ativa">Reativar</option>{% endif %}
        {% if a.status == 'ativa' %}<option value="pausada">Pausar</option>{% endif %}
        {% if a.status != 'cancelada' %}<option value="cancelada">Cancelar</option>{% endif %}
      </select>
    </form>
  </div>
</div>
{% endfor %}
{% else %}
<p class="mut">Você ainda não tem assinaturas. <a href="/" style="color:var(--verde-claro)">Explore os fornecedores</a></p>
{% endif %}
</div>
{% endblock %}"""

_MEU_PLANO = """{% extends "base" %}{% block conteudo %}
<div class="card larga"><h2>Meu plano</h2>
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if assinaturas %}
{% for a in assinaturas %}
<div style="background:#1c1c1f;border:1px solid var(--borda);border-radius:8px;padding:1rem;margin-bottom:.8rem">
  <strong>{{ a.tamanho_nome }}</strong> — R$ {{ (a.preco_centavos/100)|n2 }}
  <div class="mut" style="font-size:.85rem">De: {{ a.fornecedor_nome }} · {{ a.frequencia }} · {{ a.status }}</div>
  {% if a.status == 'aguardando_pagamento' %}
  <div style="border-left:3px solid #e0a83d;background:#1a1712;border-radius:6px;padding:.7rem .9rem;margin-top:.7rem">
    <div style="font-size:.9rem;color:#e0a83d;margin-bottom:.45rem">⏳ Aguardando pagamento</div>
    <a href="/painel/assinaturas/{{ a.id }}/pagar" style="display:inline-block;background:var(--verde-claro);color:#0a0a0a;padding:.45rem .9rem;border-radius:6px;text-decoration:none;font-weight:600;font-size:.85rem">Pagar agora →</a>
  </div>
  {% endif %}
  <!-- Trocar de tamanho -->
  <form method="post" action="/painel/meu-plano/trocar" style="margin-top:.6rem">
    <input type="hidden" name="assinatura_id" value="{{ a.id }}">
    <label style="font-size:.85rem">Trocar tamanho (vale na próxima entrega):</label>
    <select name="novo_tamanho_id" style="width:100%;margin:.3rem 0;padding:.4rem;border:1px solid var(--borda);border-radius:4px;background:#0a0a0a;color:#fff">
      <option value="">Escolher novo tamanho...</option>
      {% for t in tamanhos_por_forn[a.fornecedor_id] %}
        {% if t.id != a.tamanho_id_atual %}
        <option value="{{ t.id }}">{{ t.nome }} — R$ {{ (t.preco_centavos/100)|n2 }}</option>
        {% endif %}
      {% endfor %}
    </select>
    <button type="submit" style="background:var(--verde);color:#fff;padding:.4rem .8rem;border:0;border-radius:6px;cursor:pointer;font-size:.85rem;margin-top:.3rem">Trocar tamanho</button>
  </form>
</div>
{% endfor %}
{% else %}
<p class="mut">Você ainda não tem uma assinatura. <a href="/painel/assinaturas" style="color:var(--verde-claro)">Ver fornecedores</a></p>
{% endif %}
<!-- UPSELL discreto do app financeiro -->
<div style="background:var(--card);border:1px solid var(--borda);border-radius:8px;padding:1rem;margin-top:1.5rem">
  <p style="margin:0;font-size:.88rem;color:var(--txt-mut)">
    💡 Sabia que o Zaq também controla seus gastos, lê cupom fiscal e organiza sua lista
    de compras? <a href="/painel/ativar-app" style="color:var(--verde-claro)">Conheça o app financeiro →</a>
  </p>
</div>
</div>
{% endblock %}"""

_PAGAR_AVISO = """{% extends "base" %}{% block conteudo %}
<div class="card"><h1>Pagamento</h1>
<p class="mut">{{ msg }}</p>
<a href="https://wa.me/5586981885930?text=Oi%21%20Quero%20pagar%20meu%20plano%20do%20Zaq"
   target="_blank" rel="noopener"
   style="display:inline-block;margin-top:1rem;background:#25D366;color:#fff;padding:.6rem 1.1rem;border-radius:8px;text-decoration:none;font-weight:600">Falar no WhatsApp</a>
<div style="margin-top:1rem"><a href="/painel" style="color:var(--verde-claro)">← Voltar ao painel</a></div>
</div>{% endblock %}"""

_CESTA_AJUSTE = """{% extends "base" %}{% block conteudo %}
<div class="card larga"><h2>🧺 Sua cesta da semana</h2>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}

<div style="background:#1c1c1f;border:1px solid var(--borda);border-radius:8px;padding:1.2rem;margin-bottom:1.5rem">
  <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:1rem">
    <div>
      <h3 style="margin:0;margin-bottom:.5rem">{{ cesta.fornecedor_nome }}</h3>
      <strong>R$ {{ (cesta.preco_centavos/100)|n2 }}</strong> · Entrega: <strong>{{ cesta.data_entrega }}</strong>
    </div>
    <div style="text-align:right;font-size:.9rem;color:var(--verde-claro)">
      {% if cesta.status == 'confirmada' %}✓ Confirmada{% elif cesta.status == 'em_ajuste' %}Ajustando...{% else %}Sugerida{% endif %}
    </div>
  </div>
  <p class="mut" style="margin:0;font-size:.85rem">Está tudo certo? Não precisa fazer nada — sua cesta vai automático.<br>Quer tirar algo? É só remover abaixo.</p>
</div>

{% if cesta.itens %}
{% set grupos = {} %}
{% for item in cesta.itens %}
  {% if item.grupo not in grupos %}{% set _ = grupos.update({item.grupo: []}) %}{% endif %}
  {% set _ = grupos[item.grupo].append(item) %}
{% endfor %}

{% for grupo in ['fruta', 'legume', 'verdura', 'tempero'] %}
{% if grupo in grupos %}
<div style="margin-bottom:1.5rem">
  <h4 style="margin:0 0 .8rem 0;color:var(--verde-claro);text-transform:capitalize">🥗 {{ grupo }}s</h4>
  <div style="display:grid;gap:.6rem">
  {% for item in grupos[grupo] %}
    <div style="background:#0a0a0a;border:1px solid var(--borda);border-radius:6px;padding:.8rem;display:flex;justify-content:space-between;align-items:center">
      <div>
        <strong>{{ item.nome }}</strong><br>
        <span class="mut" style="font-size:.85rem">{{ item.quantidade }} {{ item.unidade }}</span>
      </div>
      {% if cesta.status != 'confirmada' %}
      <form method="post" action="/cesta/{{ cesta.id }}/remover" style="display:inline">
        <input type="hidden" name="item_id" value="{{ item.item_id }}">
        <button style="background:#8a3636;color:#fff;border:0;border-radius:4px;padding:.4rem .8rem;cursor:pointer;font-size:.85rem">tirar</button>
      </form>
      {% endif %}
    </div>
  {% endfor %}
  </div>
</div>
{% endif %}
{% endfor %}

{% if cesta.status != 'confirmada' %}
<form method="post" action="/cesta/{{ cesta.id }}/confirmar" style="margin-top:1.5rem">
  <button style="background:var(--verde);color:#fff;padding:.7rem 1rem;border:0;border-radius:6px;cursor:pointer;width:100%;font-weight:600;font-size:1rem">✓ Confirmar cesta</button>
  <p class="mut" style="font-size:.85rem;text-align:center;margin-top:.5rem">Ou deixe como está — vai automático. 👍</p>
</form>
{% else %}
<div style="background:#1d4620;border:1px solid #2d8659;border-radius:6px;padding:1rem;text-align:center;margin-top:1.5rem">
  <p style="margin:0;color:var(--verde-claro);font-weight:500">✓ Cesta confirmada</p>
  <small class="mut">Ela será entregue em {{ cesta.data_entrega }}</small>
</div>
{% endif %}

{% else %}
<p class="mut">Nenhum item na sua cesta. Algo deu errado — avisa pra gente!</p>
{% endif %}
</div>
{% endblock %}"""

_PEDIDOS_FORN = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <a href="/painel/fornecedor" class="ped-back">← voltar</a>
  <h2 style="margin:.2rem 0 1.2rem">📋 Pedidos</h2>

  <!-- SUB-ABAS -->
  <div class="ped-abas">
    <a href="/painel/fornecedor/pedidos" class="ped-aba ativa">Lista</a>
    <a href="/painel/fornecedor/separacao" class="ped-aba">Separação</a>
    <a href="/painel/fornecedor/embalagem" class="ped-aba">Embalagem</a>
    <a href="/painel/fornecedor/rotas" class="ped-aba">Rotas</a>
  </div>

  <form method="get" action="/painel/fornecedor/pedidos" class="ped-filtros">
    <select name="status" onchange="this.form.submit()">
      <option value="">Todos os status</option>
      <option value="em_aberto"  {% if filtro_status=='em_aberto' %}selected{% endif %}>Em aberto</option>
      <option value="sugerida"   {% if filtro_status=='sugerida' %}selected{% endif %}>Sugerida</option>
      <option value="em_ajuste"  {% if filtro_status=='em_ajuste' %}selected{% endif %}>Em ajuste</option>
      <option value="confirmada" {% if filtro_status=='confirmada' %}selected{% endif %}>Confirmada</option>
      <option value="cobrada"    {% if filtro_status=='cobrada' %}selected{% endif %}>Cobrada</option>
      <option value="entregue"   {% if filtro_status=='entregue' %}selected{% endif %}>Entregue</option>
      <option value="cancelada"  {% if filtro_status=='cancelada' %}selected{% endif %}>Cancelada</option>
    </select>
    <select name="periodo" onchange="this.form.submit()">
      <option value="">Qualquer data</option>
      <option value="proxima"        {% if filtro_periodo=='proxima' %}selected{% endif %}>Próximas entregas</option>
      <option value="esta_semana"    {% if filtro_periodo=='esta_semana' %}selected{% endif %}>Esta semana</option>
      <option value="proxima_semana" {% if filtro_periodo=='proxima_semana' %}selected{% endif %}>Próxima semana</option>
      <option value="mes"            {% if filtro_periodo=='mes' %}selected{% endif %}>Próximos 30 dias</option>
      <option value="passadas"       {% if filtro_periodo=='passadas' %}selected{% endif %}>Já entregues</option>
    </select>
    <input name="busca" value="{{ filtro_busca or '' }}" placeholder="Buscar cliente..." class="ped-busca">
    {% if filtro_status or filtro_periodo or filtro_busca %}
    <a href="/painel/fornecedor/pedidos" class="ped-limpa" title="Limpar filtros">×</a>
    {% endif %}
  </form>

  <div class="ped-resumo">
    {{ pedidos|length }} pedido{% if pedidos|length != 1 %}s{% endif %}
    {% if contagens.em_aberto %} · <strong style="color:#e0a83d">{{ contagens.em_aberto }}</strong> em aberto{% endif %}
    {% if contagens.entregue %} · <strong style="color:var(--verde)">{{ contagens.entregue }}</strong> já entregue{% if contagens.entregue != 1 %}s{% endif %}{% endif %}
  </div>

  {% if pedidos %}
  <div class="ped-lista">
    {% for p in pedidos %}
    <a href="/painel/fornecedor/pedidos/{{ p.id }}" class="ped-card">
      <div class="ped-card-topo">
        <div class="ped-card-cli">
          <div class="ped-cli-nome">{{ p.cliente_nome }}</div>
          <div class="ped-cli-bairro">{{ p.bairro or 'sem bairro' }}</div>
        </div>
        <div class="ped-card-val">
          R$ {{ (p.preco_reais)|n2 }}
          <div class="ped-card-tam">{{ p.tamanho_nome }}</div>
        </div>
      </div>
      <div class="ped-card-meta">
        <span class="ped-st ped-st-{{ p.status }}">{{ p.status|replace('_',' ') }}</span>
        <span class="ped-pg ped-pg-{{ p.status_pagamento }}">{{ p.status_pagamento }}</span>
        <span class="ped-meta-sep">{{ p.qtd_itens }} {% if p.qtd_itens == 1 %}item{% else %}itens{% endif %}</span>
        {% if p.data_entrega %}<span class="ped-meta-data">📅 {{ p.data_entrega }}</span>{% endif %}
      </div>
    </a>
    {% endfor %}
  </div>
  {% else %}
  <div class="ped-vazio">
    <div style="font-size:2.5rem;opacity:.4;margin-bottom:.5rem">📭</div>
    {% if filtro_status or filtro_periodo or filtro_busca %}
      <p>Nenhum pedido com esses filtros.</p>
      <a href="/painel/fornecedor/pedidos" style="color:var(--verde-claro)">Limpar filtros</a>
    {% else %}
      <p>Ainda não há pedidos.</p>
      <p class="mut" style="margin-top:.5rem;font-size:.9rem">
        Os pedidos aparecem aqui quando a janela semanal monta as cestas dos seus clientes.
      </p>
    {% endif %}
  </div>
  {% endif %}
</div>

<style>
.ped-back{color:var(--verde-claro);display:inline-block;margin-bottom:.8rem;font-size:.9rem;text-decoration:none}
.ped-back:hover{text-decoration:underline}
.ped-filtros{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin-bottom:.8rem;padding:.8rem;background:var(--card-2);border:1px solid var(--borda);border-radius:8px}
.ped-filtros select,.ped-filtros input{background:#0a0a0a;color:var(--txt);border:1px solid var(--borda);border-radius:6px;padding:.5rem .7rem;font-size:.9rem}
.ped-filtros select{cursor:pointer;min-width:140px}
.ped-busca{flex:1;min-width:160px}
.ped-limpa{display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;background:#2a1414;color:#c66;border-radius:6px;text-decoration:none;font-size:1.2rem;line-height:1}
.ped-limpa:hover{background:#3a1818}
.ped-resumo{color:var(--txt-mut);font-size:.88rem;margin-bottom:1rem;padding:0 .2rem}
.ped-lista{display:flex;flex-direction:column;gap:.6rem}
.ped-card{display:block;background:var(--card-2);border:1px solid var(--borda);border-radius:8px;padding:1rem;text-decoration:none;color:inherit;transition:border-color .15s,transform .1s}
.ped-card:hover{border-color:var(--verde-claro);transform:translateX(2px)}
.ped-card:active{transform:translateX(2px) scale(.998)}
.ped-card-topo{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;margin-bottom:.7rem}
.ped-card-cli{flex:1;min-width:0}
.ped-cli-nome{font-weight:600;color:var(--txt);font-size:1rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ped-cli-bairro{color:var(--txt-mut);font-size:.82rem;margin-top:.15rem}
.ped-card-val{text-align:right;font-weight:600;color:var(--verde-claro);font-size:1.05rem;white-space:nowrap}
.ped-card-tam{color:var(--txt-mut);font-size:.78rem;font-weight:400;margin-top:.15rem}
.ped-card-meta{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;font-size:.78rem}
.ped-st,.ped-pg{display:inline-block;padding:.2rem .55rem;border-radius:10px}
.ped-st-sugerida{background:var(--borda);color:#c9c9c9}
.ped-st-em_ajuste{background:#3a2d12;color:#e0a83d}
.ped-st-confirmada{background:#15241d;color:var(--verde-claro)}
.ped-st-cobrada{background:#15241d;color:var(--verde-claro)}
.ped-st-entregue{background:#0e2a1e;color:var(--verde)}
.ped-st-cancelada{background:#2a1414;color:#c66}
.ped-pg-pago{background:#0e2a1e;color:var(--verde)}
.ped-pg-aguardando{background:#3a2d12;color:#e0a83d}
.ped-pg-atrasado{background:#2a1414;color:#c66}
.ped-pg-cancelado{background:#1c1c1f;color:#7a7a7a}
.ped-meta-sep{color:#7a7a7a;margin-left:auto}
.ped-meta-data{color:var(--txt-mut)}
.ped-vazio{text-align:center;padding:3rem 1rem;color:var(--txt-mut)}
.ped-abas{display:flex;gap:0;border-bottom:1px solid var(--borda);margin-bottom:1rem;overflow-x:auto}
.ped-aba{padding:.55rem 1rem;color:var(--txt-mut);border-bottom:2px solid transparent;text-decoration:none;font-size:.92rem;white-space:nowrap}
.ped-aba:hover{color:var(--txt)}
.ped-aba.ativa{color:var(--verde-claro);border-color:var(--verde-claro);font-weight:600}
.ped-aba.off{color:#5a5a5a;font-size:.88rem;cursor:default}
@media (max-width:520px){
  .ped-filtros{padding:.6rem;gap:.4rem}
  .ped-filtros select{min-width:0;flex:1}
  .ped-busca{flex-basis:100%}
  .ped-card{padding:.85rem}
  .ped-cli-nome{font-size:.95rem}
  .ped-card-val{font-size:1rem}
  .ped-meta-sep{margin-left:0}
}
</style>
{% endblock %}"""

_PEDIDO_DETALHE_FORN = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <a href="/painel/fornecedor/pedidos" class="ped-back">← voltar pra lista</a>
  <h2 style="margin:.2rem 0 1.2rem">Pedido #{{ p.id }}</h2>

  <div class="ped-det">
    <div class="ped-det-bloco">
      <div class="ped-det-rot">Cliente</div>
      <div class="ped-det-val">{{ p.cliente_nome }}</div>
      {% if p.endereco %}<div class="mut" style="margin-top:.3rem;font-size:.9rem">{{ p.endereco }}</div>{% endif %}
      {% if p.cep %}<div class="mut" style="font-size:.85rem">CEP {{ p.cep }}</div>{% endif %}
    </div>
    <div class="ped-det-bloco">
      <div class="ped-det-rot">Cesta</div>
      <div class="ped-det-val">{{ p.tamanho_nome }} — R$ {{ (p.preco_reais)|n2 }}</div>
      <div class="mut" style="margin-top:.3rem;font-size:.9rem">Entrega: {{ p.data_entrega or 'a definir' }}</div>
      <div style="margin-top:.6rem">
        <span class="ped-st ped-st-{{ p.status }}">{{ p.status|replace('_',' ') }}</span>
        <span class="ped-pg ped-pg-{{ p.status_pagamento }}">{{ p.status_pagamento }}</span>
      </div>
    </div>
  </div>

  <h3 style="margin:2rem 0 .8rem;font-size:1.05rem">Itens ({{ p.qtd_itens }})</h3>
  {% if p.itens %}
  <div class="ped-itens">
    {% for it in p.itens %}
    <div class="ped-item">
      <div class="ped-item-info">
        <div class="ped-item-nome">{{ it.produto_nome }}</div>
        <div class="ped-item-grupo">{{ it.grupo }}</div>
      </div>
      <div class="ped-item-qtd">{{ it.quantidade }} {{ it.unidade }}</div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <p class="mut" style="padding:1rem 0">Cesta ainda sem itens montados.</p>
  {% endif %}
</div>

<style>
.ped-back{color:var(--verde-claro);display:inline-block;margin-bottom:.8rem;font-size:.9rem;text-decoration:none}
.ped-back:hover{text-decoration:underline}
.ped-det{display:grid;grid-template-columns:1fr 1fr;gap:1rem;background:var(--card-2);border:1px solid var(--borda);border-radius:8px;padding:1.2rem}
.ped-det-rot{color:var(--txt-mut);font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;margin-bottom:.4rem}
.ped-det-val{color:var(--txt);font-size:1.05rem;font-weight:600}
.ped-st,.ped-pg{display:inline-block;padding:.2rem .55rem;border-radius:10px;font-size:.78rem;margin-right:.3rem}
.ped-st-sugerida{background:var(--borda);color:#c9c9c9}
.ped-st-em_ajuste{background:#3a2d12;color:#e0a83d}
.ped-st-confirmada{background:#15241d;color:var(--verde-claro)}
.ped-st-cobrada{background:#15241d;color:var(--verde-claro)}
.ped-st-entregue{background:#0e2a1e;color:var(--verde)}
.ped-st-cancelada{background:#2a1414;color:#c66}
.ped-pg-pago{background:#0e2a1e;color:var(--verde)}
.ped-pg-aguardando{background:#3a2d12;color:#e0a83d}
.ped-pg-atrasado{background:#2a1414;color:#c66}
.ped-pg-cancelado{background:#1c1c1f;color:#7a7a7a}
.ped-itens{display:flex;flex-direction:column;gap:.4rem}
.ped-item{display:flex;justify-content:space-between;align-items:center;padding:.7rem 1rem;background:var(--card-2);border:1px solid var(--borda);border-radius:6px;gap:1rem}
.ped-item-info{flex:1;min-width:0}
.ped-item-nome{color:var(--txt);font-weight:500}
.ped-item-grupo{color:#7a7a7a;font-size:.78rem;text-transform:capitalize;margin-top:.1rem}
.ped-item-qtd{color:var(--verde-claro);font-weight:600;white-space:nowrap}
@media (max-width:520px){.ped-det{grid-template-columns:1fr}}
</style>
{% endblock %}"""

_SEPARACAO_FORN = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <a href="/painel/fornecedor" class="ped-back">← voltar</a>
  <h2 style="margin:.2rem 0 1.2rem">📋 Pedidos</h2>

  <!-- SUB-ABAS -->
  <div class="ped-abas">
    <a href="/painel/fornecedor/pedidos" class="ped-aba">Lista</a>
    <a href="/painel/fornecedor/separacao" class="ped-aba ativa">Separação</a>
    <a href="/painel/fornecedor/embalagem" class="ped-aba">Embalagem</a>
    <a href="/painel/fornecedor/rotas" class="ped-aba">Rotas</a>
  </div>

  <!-- FILTRO DE PERÍODO -->
  <form method="get" action="/painel/fornecedor/separacao" class="sep-filtro">
    <select name="periodo" onchange="this.form.submit()">
      <option value="esta_semana"    {% if periodo=='esta_semana' %}selected{% endif %}>Esta semana</option>
      <option value="proxima_semana" {% if periodo=='proxima_semana' %}selected{% endif %}>Próxima semana</option>
      <option value="proxima"        {% if periodo=='proxima' %}selected{% endif %}>Próximas entregas</option>
      <option value="mes"            {% if periodo=='mes' %}selected{% endif %}>Próximos 30 dias</option>
    </select>
    {% if dados.data_de and dados.data_ate %}
    <span class="sep-range">{{ dados.data_de }} — {{ dados.data_ate }}</span>
    {% elif dados.data_de %}
    <span class="sep-range">a partir de {{ dados.data_de }}</span>
    {% endif %}
  </form>

  <!-- RESUMO -->
  <div class="sep-resumo">
    <div class="sep-card">
      <div class="sep-card-num">{{ dados.qtd_cestas_confirmadas }}</div>
      <div class="sep-card-rot">cesta{% if dados.qtd_cestas_confirmadas != 1 %}s{% endif %} confirmada{% if dados.qtd_cestas_confirmadas != 1 %}s{% endif %}</div>
    </div>
    <div class="sep-card">
      <div class="sep-card-num">{{ dados.total_itens_distintos }}</div>
      <div class="sep-card-rot">produto{% if dados.total_itens_distintos != 1 %}s{% endif %} distinto{% if dados.total_itens_distintos != 1 %}s{% endif %}</div>
    </div>
    <div class="sep-card">
      <div class="sep-card-num">R$ {{ (dados.valor_total_reais)|n2 }}</div>
      <div class="sep-card-rot">valor total</div>
    </div>
  </div>

  {% if dados.qtd_cestas_em_ajuste %}
  <div class="sep-alerta">
    ⚠️ <strong>{{ dados.qtd_cestas_em_ajuste }}</strong> cesta{% if dados.qtd_cestas_em_ajuste != 1 %}s{% endif %} ainda em ajuste pelos clientes — a lista pode mudar.
    <a href="/painel/fornecedor/pedidos?status=em_aberto" class="sep-alerta-link">ver →</a>
  </div>
  {% endif %}

  <!-- LISTA POR GRUPO -->
  {% if dados.grupos %}
  <div class="sep-acoes">
    <button onclick="window.print()" class="sep-btn">🖨️ Imprimir / Salvar PDF</button>
  </div>

  {% for g in dados.grupos %}
  <div class="sep-grupo">
    <h3 class="sep-grupo-tit">{{ g.grupo|capitalize }}</h3>
    <div class="sep-itens">
      {% for it in g.itens %}
      <div class="sep-item {% if not it.suficiente %}sep-item-falta{% endif %}">
        <div class="sep-item-info">
          <div class="sep-item-nome">{{ it.produto_nome }}</div>
          <div class="sep-item-meta">
            <span class="sep-meta-rot">precisa:</span>
            <strong>{{ it.quantidade }} {{ it.unidade }}</strong>
            <span class="sep-meta-sep">·</span>
            <span class="sep-meta-rot">estoque:</span>
            <strong>{{ it.saldo_atual }} {{ it.unidade }}</strong>
            <span class="sep-meta-sep">·</span>
            <span class="sep-meta-rot">sobra:</span>
            {% if it.sobra_apos >= 0 %}
              <strong style="color:var(--verde-claro)">{{ it.sobra_apos }} {{ it.unidade }}</strong>
            {% else %}
              <strong style="color:#c66">faltam {{ it.falta }} {{ it.unidade }}</strong>
            {% endif %}
          </div>
        </div>
        <div class="sep-item-status">
          {% if it.suficiente %}
            <span class="sep-ok">✓</span>
          {% else %}
            <span class="sep-fail">⚠ repor</span>
          {% endif %}
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endfor %}

  {% else %}
  <div class="ped-vazio">
    <div style="font-size:2.5rem;opacity:.4;margin-bottom:.5rem">🏭</div>
    {% if dados.qtd_cestas_em_ajuste %}
      <p>Nenhuma cesta confirmada nesse período ainda.</p>
      <p class="mut" style="margin-top:.5rem;font-size:.9rem">
        Há {{ dados.qtd_cestas_em_ajuste }} em ajuste pelos clientes — assim que confirmarem, aparecem aqui.
      </p>
    {% else %}
      <p>Sem cestas confirmadas pra esse período.</p>
      <p class="mut" style="margin-top:.5rem;font-size:.9rem">
        Cestas confirmadas viram a sua lista de compras do CEASA.
      </p>
    {% endif %}
  </div>
  {% endif %}
</div>

<style>
.ped-back{color:var(--verde-claro);display:inline-block;margin-bottom:.8rem;font-size:.9rem;text-decoration:none}
.ped-back:hover{text-decoration:underline}
.ped-abas{display:flex;gap:0;border-bottom:1px solid var(--borda);margin-bottom:1rem;overflow-x:auto}
.ped-aba{padding:.55rem 1rem;color:var(--txt-mut);border-bottom:2px solid transparent;text-decoration:none;font-size:.92rem;white-space:nowrap}
.ped-aba:hover{color:var(--txt)}
.ped-aba.ativa{color:var(--verde-claro);border-color:var(--verde-claro);font-weight:600}
.ped-aba.off{color:#5a5a5a;font-size:.88rem;cursor:default}
.sep-filtro{display:flex;align-items:center;gap:.8rem;margin-bottom:1rem;padding:.7rem .9rem;background:var(--card-2);border:1px solid var(--borda);border-radius:8px;flex-wrap:wrap}
.sep-filtro select{background:#0a0a0a;color:var(--txt);border:1px solid var(--borda);border-radius:6px;padding:.5rem .7rem;font-size:.9rem;cursor:pointer}
.sep-range{color:var(--txt-mut);font-size:.85rem}
.sep-resumo{display:grid;grid-template-columns:repeat(3,1fr);gap:.6rem;margin-bottom:1rem}
.sep-card{background:var(--card-2);border:1px solid var(--borda);border-radius:8px;padding:1rem;text-align:center}
.sep-card-num{color:var(--verde-claro);font-size:1.6rem;font-weight:700;line-height:1.1}
.sep-card-rot{color:var(--txt-mut);font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;margin-top:.3rem}
.sep-alerta{background:#3a2d12;border:1px solid #6b5320;border-radius:8px;padding:.8rem 1rem;margin-bottom:1rem;color:#e0a83d;font-size:.9rem}
.sep-alerta-link{color:#e0a83d;text-decoration:underline;margin-left:.4rem;white-space:nowrap}
.sep-acoes{margin-bottom:1rem}
.sep-btn{background:#15241d;color:var(--verde-claro);border:1px solid var(--verde-claro);border-radius:6px;padding:.5rem .9rem;cursor:pointer;font-size:.9rem}
.sep-btn:hover{background:#1d2e25}
.sep-grupo{margin-bottom:1.2rem}
.sep-grupo-tit{color:var(--verde-claro);font-size:.92rem;text-transform:uppercase;letter-spacing:.06em;margin:0 0 .5rem;padding-bottom:.3rem;border-bottom:1px solid var(--borda)}
.sep-itens{display:flex;flex-direction:column;gap:.4rem}
.sep-item{display:flex;justify-content:space-between;align-items:center;padding:.8rem 1rem;background:var(--card-2);border:1px solid var(--borda);border-radius:6px;gap:1rem}
.sep-item-falta{border-left:3px solid #c66}
.sep-item-info{flex:1;min-width:0}
.sep-item-nome{color:var(--txt);font-weight:500}
.sep-item-meta{color:var(--txt-mut);font-size:.85rem;margin-top:.3rem;display:flex;flex-wrap:wrap;gap:.3rem;align-items:center}
.sep-item-meta strong{color:var(--txt);font-weight:600}
.sep-meta-rot{color:#7a7a7a;font-size:.78rem;text-transform:uppercase;letter-spacing:.04em}
.sep-meta-sep{color:#3a3a3b;margin:0 .15rem}
.sep-item-status{font-size:1.5rem;white-space:nowrap}
.sep-ok{color:var(--verde)}
.sep-fail{color:#c66;font-size:.9rem;font-weight:600;background:#2a1414;padding:.3rem .6rem;border-radius:6px}
.ped-vazio{text-align:center;padding:3rem 1rem;color:var(--txt-mut)}
@media (max-width:520px){
  .sep-item-meta{font-size:.78rem;gap:.2rem;flex-direction:column;align-items:flex-start;gap:.1rem}
  .sep-meta-sep{display:none}
  .sep-resumo{grid-template-columns:1fr;gap:.4rem}
  .sep-card{padding:.8rem}
  .sep-card-num{font-size:1.3rem}
}
@media print{
  .topo,.ped-back,.ped-abas,.sep-filtro,.sep-acoes,.sep-alerta{display:none}
  .card.larga{border:none;background:white;color:black}
  .sep-card{background:white;border:1px solid #ccc;color:black}
  .sep-card-num,.sep-grupo-tit,.sep-item-qtd{color:black}
  .sep-item{background:white;border:1px solid #ccc;color:black}
  .sep-item-nome,.sep-item-saldo{color:black}
}
</style>
{% endblock %}"""

_EMBALAGEM_FORN = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <a href="/painel/fornecedor" class="ped-back">← voltar</a>
  <h2 style="margin:.2rem 0 1.2rem">📋 Pedidos</h2>

  <!-- SUB-ABAS -->
  <div class="ped-abas">
    <a href="/painel/fornecedor/pedidos" class="ped-aba">Lista</a>
    <a href="/painel/fornecedor/separacao" class="ped-aba">Separação</a>
    <a href="/painel/fornecedor/embalagem" class="ped-aba ativa">Embalagem</a>
    <a href="/painel/fornecedor/rotas" class="ped-aba">Rotas</a>
  </div>

  <!-- FILTRO -->
  <form method="get" action="/painel/fornecedor/embalagem" class="sep-filtro">
    <select name="periodo" onchange="this.form.submit()">
      <option value="esta_semana"    {% if periodo=='esta_semana' %}selected{% endif %}>Esta semana</option>
      <option value="proxima_semana" {% if periodo=='proxima_semana' %}selected{% endif %}>Próxima semana</option>
      <option value="proxima"        {% if periodo=='proxima' %}selected{% endif %}>Próximas entregas</option>
      <option value="mes"            {% if periodo=='mes' %}selected{% endif %}>Próximos 30 dias</option>
    </select>
    {% if dados.data_de and dados.data_ate %}
    <span class="sep-range">{{ dados.data_de }} → {{ dados.data_ate }}</span>
    {% endif %}
  </form>

  <!-- RESUMO -->
  <div class="sep-resumo">
    <div class="sep-card">
      <div class="sep-card-num">{{ dados.qtd_total }}</div>
      <div class="sep-card-rot">cesta{% if dados.qtd_total != 1 %}s{% endif %} pra embalar</div>
    </div>
    <div class="sep-card">
      <div class="sep-card-num" style="color:var(--verde)">{{ dados.qtd_embaladas }}</div>
      <div class="sep-card-rot">já embaladas</div>
    </div>
    <div class="sep-card">
      <div class="sep-card-num" style="color:#e0a83d">{{ dados.qtd_falta_embalar }}</div>
      <div class="sep-card-rot">faltam</div>
    </div>
  </div>

  {% if dados.cestas %}
  <div class="emb-cards">
    {% for c in dados.cestas %}
    <div class="emb-card {% if c.esta_embalada %}emb-card-pronta{% endif %}">
      <div class="emb-card-head">
        <div class="emb-card-cli">
          <div class="emb-card-nome">🧺 {{ c.tamanho_nome }} — {{ c.cliente_nome }}</div>
          {% if c.endereco %}<div class="emb-card-end">{{ c.endereco }}</div>{% endif %}
          {% if c.cep %}<div class="emb-card-cep">CEP {{ c.cep }} {% if c.data_entrega %}· 📅 {{ c.data_entrega }}{% endif %}</div>
          {% elif c.data_entrega %}<div class="emb-card-cep">📅 {{ c.data_entrega }}</div>{% endif %}
        </div>
        <div class="emb-card-acoes">
          {% if c.esta_embalada %}
            <div class="emb-pronto">✓ embalada<br><small>{{ c.embalada_em }}</small></div>
            <form method="post" action="/painel/fornecedor/embalagem/{{ c.id }}/desmarcar" style="display:inline">
              <button class="emb-btn-desfazer" title="desfazer">↶ desmarcar</button>
            </form>
          {% else %}
            <form method="post" action="/painel/fornecedor/embalagem/{{ c.id }}/marcar" style="display:inline">
              <button class="emb-btn-marcar">✓ Embalada</button>
            </form>
          {% endif %}
          <a href="/painel/fornecedor/embalagem/{{ c.id }}/etiqueta" target="_blank" rel="noopener" class="emb-btn-etiqueta">🏷️ Etiqueta</a>
        </div>
      </div>
      <div class="emb-itens">
        {% for it in c.itens %}
        <span class="emb-item">{{ it.quantidade }} {{ it.unidade }} · <strong>{{ it.produto_nome }}</strong></span>
        {% endfor %}
      </div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="ped-vazio">
    <div style="font-size:2.5rem;opacity:.4;margin-bottom:.5rem">📦</div>
    <p>Sem cestas confirmadas pra embalar neste período.</p>
    <p class="mut" style="margin-top:.5rem;font-size:.9rem">Cestas em sugerida/em_ajuste não aparecem aqui (cliente ainda pode mexer).</p>
  </div>
  {% endif %}
</div>

<style>
.ped-back{color:var(--verde-claro);display:inline-block;margin-bottom:.8rem;font-size:.9rem;text-decoration:none}
.ped-back:hover{text-decoration:underline}
.ped-abas{display:flex;gap:0;border-bottom:1px solid var(--borda);margin-bottom:1rem;overflow-x:auto}
.ped-aba{padding:.55rem 1rem;color:var(--txt-mut);border-bottom:2px solid transparent;text-decoration:none;font-size:.92rem;white-space:nowrap}
.ped-aba:hover{color:var(--txt)}
.ped-aba.ativa{color:var(--verde-claro);border-color:var(--verde-claro);font-weight:600}
.ped-aba.off{color:#5a5a5a;font-size:.88rem;cursor:default}
.sep-filtro{display:flex;align-items:center;gap:.8rem;margin-bottom:1rem;padding:.7rem .9rem;background:var(--card-2);border:1px solid var(--borda);border-radius:8px;flex-wrap:wrap}
.sep-filtro select{background:#0a0a0a;color:var(--txt);border:1px solid var(--borda);border-radius:6px;padding:.5rem .7rem;font-size:.9rem;cursor:pointer}
.sep-range{color:var(--txt-mut);font-size:.85rem}
.sep-resumo{display:grid;grid-template-columns:repeat(3,1fr);gap:.6rem;margin-bottom:1rem}
.sep-card{background:var(--card-2);border:1px solid var(--borda);border-radius:8px;padding:1rem;text-align:center}
.sep-card-num{color:var(--verde-claro);font-size:1.6rem;font-weight:700;line-height:1.1}
.sep-card-rot{color:var(--txt-mut);font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;margin-top:.3rem}
.emb-cards{display:flex;flex-direction:column;gap:.7rem}
.emb-card{background:var(--card-2);border:1px solid var(--borda);border-radius:8px;padding:1rem}
.emb-card-pronta{border-left:3px solid var(--verde);opacity:.7}
.emb-card-head{display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap;margin-bottom:.7rem}
.emb-card-cli{flex:1;min-width:200px}
.emb-card-nome{font-weight:600;color:var(--txt);font-size:1rem}
.emb-card-end{color:var(--txt-mut);font-size:.85rem;margin-top:.25rem}
.emb-card-cep{color:#7a7a7a;font-size:.78rem;margin-top:.15rem}
.emb-card-acoes{display:flex;gap:.4rem;align-items:flex-start;flex-wrap:wrap}
.emb-btn-marcar{background:#15241d;color:var(--verde-claro);border:1px solid var(--verde-claro);border-radius:6px;padding:.5rem .85rem;cursor:pointer;font-size:.88rem;font-weight:500;white-space:nowrap}
.emb-btn-marcar:hover{background:#1d2e25}
.emb-btn-etiqueta{background:var(--card-2);color:var(--txt-mut);border:1px solid var(--borda);border-radius:6px;padding:.5rem .85rem;text-decoration:none;font-size:.88rem;white-space:nowrap}
.emb-btn-etiqueta:hover{border-color:var(--verde-claro);color:var(--verde-claro)}
.emb-pronto{color:var(--verde);font-weight:600;font-size:.88rem;text-align:center;line-height:1.3}
.emb-pronto small{color:#7a7a7a;font-size:.72rem;font-weight:400}
.emb-btn-desfazer{background:transparent;color:#7a7a7a;border:1px solid var(--borda);border-radius:6px;padding:.35rem .6rem;cursor:pointer;font-size:.78rem}
.emb-btn-desfazer:hover{color:#c66;border-color:#3a2020}
.emb-itens{display:flex;flex-wrap:wrap;gap:.4rem;padding-top:.6rem;border-top:1px dashed var(--borda)}
.emb-item{color:var(--txt-mut);font-size:.82rem;background:#0a0a0a;padding:.25rem .55rem;border-radius:4px}
.emb-item strong{color:var(--txt)}
.ped-vazio{text-align:center;padding:3rem 1rem;color:var(--txt-mut)}
@media (max-width:520px){
  .sep-resumo{grid-template-columns:1fr;gap:.4rem}
  .emb-card-head{flex-direction:column}
  .emb-card-acoes{width:100%}
  .emb-btn-marcar,.emb-btn-etiqueta{flex:1;text-align:center}
}
</style>
{% endblock %}"""

_ETIQUETA_FORN = """<!doctype html>
<html lang="pt-br"><head>
<meta charset="utf-8">
<title>Etiqueta — {{ 'Avulso' if e.tipo == 'avulso' else 'Cesta' }} #{{ e.id }}</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#fff;color:#000;margin:0;padding:1rem}
.etiqueta{width:100mm;max-width:100%;border:2px solid #000;border-radius:6px;padding:1rem 1.2rem;page-break-after:always}
.etq-topo{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:1px solid #000;padding-bottom:.4rem;margin-bottom:.6rem}
.etq-marca{font-size:1.1rem;font-weight:700;letter-spacing:-.02em}
.etq-data{font-size:.85rem;color:#444}
.etq-cli{font-size:1.4rem;font-weight:700;margin-bottom:.3rem;line-height:1.15}
.etq-end{font-size:.95rem;color:#222;line-height:1.4}
.etq-cep{font-size:.8rem;color:#444;margin-top:.2rem}
.etq-cesta{margin-top:.8rem;padding-top:.5rem;border-top:1px dashed #888;font-size:1.05rem}
.etq-cesta b{font-size:1.2rem}
.etq-rodape{margin-top:.6rem;font-size:.75rem;color:#666;text-align:right}
.etq-itens{margin-top:.7rem;padding-top:.5rem;border-top:1px dashed #888}
.etq-itens-t{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:#555;margin-bottom:.35rem}
.etq-item{font-size:.95rem;padding:.18rem 0;line-height:1.3}
.etq-check{font-size:1.1rem;color:#000;margin-right:.3rem}
.etq-acoes{margin-top:1rem;padding-top:1rem;border-top:1px solid #ddd;display:flex;gap:.5rem}
.etq-acoes button{padding:.6rem 1rem;border-radius:6px;border:1px solid #000;cursor:pointer;background:#000;color:#fff;font-size:.9rem}
.etq-acoes .sec{background:#fff;color:#000}
@media print{
  .etq-acoes{display:none}
  body{padding:0}
  .etiqueta{border:1px solid #000;page-break-after:always}
}
</style></head>
<body>
<div class="etiqueta">
  <div class="etq-topo">
    <div class="etq-marca">{% if e.tipo == 'avulso' %}📦{% else %}🧺{% endif %} {{ e.fornecedor_nome or 'Zaq Fornecedor' }}</div>
    {% if e.data_entrega %}<div class="etq-data">Entrega: {{ e.data_entrega }}</div>{% endif %}
  </div>
  <div class="etq-cli">{{ e.cliente_nome }}</div>
  {% if e.endereco %}<div class="etq-end">{{ e.endereco }}</div>{% endif %}
  {% if e.cep %}<div class="etq-cep">CEP {{ e.cep }}</div>{% endif %}
  <div class="etq-cesta">{% if e.tipo == 'avulso' %}Avulso · <b>{{ e.qtd_itens }} ite{{ 'm' if e.qtd_itens == 1 else 'ns' }}</b>{% if e.forma_label %} · {{ e.forma_label }}{% endif %}{% else %}Cesta <b>{{ e.tamanho_nome }}</b>{% endif %}</div>
  {% if e.itens %}<div class="etq-itens"><div class="etq-itens-t">Separar</div>{% for it in e.itens %}<div class="etq-item"><span class="etq-check">&#9744;</span> <b>{{ "%g"|format(it.quantidade) }} {{ it.unidade }}</b> {{ it.produto_nome }}</div>{% endfor %}</div>{% endif %}
  <div class="etq-rodape">Pedido #{{ e.id }}</div>
</div>
<div class="etq-acoes">
  <button onclick="window.print()">🖨️ Imprimir</button>
  <button class="sec" onclick="window.close()">Fechar</button>
</div>
</body></html>"""

_ROTAS_FORN = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <a href="/painel/fornecedor" class="ped-back">← voltar</a>
  <h2 style="margin:.2rem 0 1.2rem">📋 Pedidos</h2>

  <!-- SUB-ABAS -->
  <div class="ped-abas">
    <a href="/painel/fornecedor/pedidos" class="ped-aba">Lista</a>
    <a href="/painel/fornecedor/separacao" class="ped-aba">Separação</a>
    <a href="/painel/fornecedor/embalagem" class="ped-aba">Embalagem</a>
    <a href="/painel/fornecedor/rotas" class="ped-aba ativa">Rotas</a>
  </div>

  <!-- FILTRO -->
  <form method="get" action="/painel/fornecedor/rotas" class="sep-filtro">
    <select name="periodo" onchange="this.form.submit()">
      <option value="esta_semana"    {% if periodo=='esta_semana' %}selected{% endif %}>Esta semana</option>
      <option value="proxima_semana" {% if periodo=='proxima_semana' %}selected{% endif %}>Próxima semana</option>
      <option value="proxima"        {% if periodo=='proxima' %}selected{% endif %}>Próximas entregas</option>
      <option value="mes"            {% if periodo=='mes' %}selected{% endif %}>Próximos 30 dias</option>
    </select>
    {% if dados.data_de and dados.data_ate %}
    <span class="sep-range">{{ dados.data_de }} → {{ dados.data_ate }}</span>
    {% endif %}
  </form>

  <!-- RESUMO -->
  <div class="sep-resumo" style="grid-template-columns:repeat(4,1fr)">
    <div class="sep-card">
      <div class="sep-card-num">{{ dados.qtd_grupos }}</div>
      <div class="sep-card-rot">bairro{% if dados.qtd_grupos != 1 %}s{% endif %}</div>
    </div>
    <div class="sep-card">
      <div class="sep-card-num">{{ dados.qtd_total }}</div>
      <div class="sep-card-rot">cesta{% if dados.qtd_total != 1 %}s{% endif %}</div>
    </div>
    <div class="sep-card">
      <div class="sep-card-num" style="color:#e0a83d">{{ dados.qtd_pendentes }}</div>
      <div class="sep-card-rot">pendentes</div>
    </div>
    <div class="sep-card">
      <div class="sep-card-num" style="color:var(--verde)">{{ dados.qtd_entregues }}</div>
      <div class="sep-card-rot">entregues</div>
    </div>
  </div>

  {% if dados.qtd_nao_embaladas %}
  <div class="sep-alerta">
    ⚠️ <strong>{{ dados.qtd_nao_embaladas }}</strong> cesta{% if dados.qtd_nao_embaladas != 1 %}s ainda não foram{% else %} ainda não foi{% endif %} embalada{% if dados.qtd_nao_embaladas != 1 %}s{% endif %}.
    <a href="/painel/fornecedor/embalagem" class="sep-alerta-link">embalar →</a>
  </div>
  {% endif %}

  {% if dados.grupos %}
  {% for g in dados.grupos %}
  <div class="rot-grupo">
    <h3 class="rot-grupo-tit">
      📍 {{ g.bairro }}
      <span class="rot-grupo-meta">{{ g.qtd }} cesta{% if g.qtd != 1 %}s{% endif %}{% if g.qtd_pendentes %} · <span style="color:#e0a83d">{{ g.qtd_pendentes }} pendente{% if g.qtd_pendentes != 1 %}s{% endif %}</span>{% endif %}</span>
    </h3>
    <div class="rot-cestas">
      {% for c in g.cestas %}
      <div class="rot-cesta {% if c.esta_entregue %}rot-c-entregue{% elif not c.esta_embalada %}rot-c-nemb{% endif %}">
        <div class="rot-c-info">
          <div class="rot-c-nome">
            🧺 {{ c.tamanho_nome }} — {{ c.cliente_nome }}
            {% if not c.esta_embalada and not c.esta_entregue %}<span class="rot-tag-nemb">⚠ não embalada</span>{% endif %}
          </div>
          {% if c.endereco %}<div class="rot-c-end">{{ c.endereco }}</div>{% endif %}
          <div class="rot-c-meta">
            {% if c.cep %}📮 {{ c.cep }}{% endif %}
            {% if c.qtd_itens %} · {{ c.qtd_itens }} item{% if c.qtd_itens != 1 %}s{% endif %}{% endif %}
            {% if c.whatsapp %} · 📱 {{ c.whatsapp }}{% endif %}
          </div>
        </div>
        <div class="rot-c-acoes">
          {% if c.esta_entregue %}
            <div class="rot-pronto">✓ entregue<br><small>{{ c.entregue_em }}</small></div>
            <form method="post" action="/painel/fornecedor/rotas/{{ c.id }}/desmarcar" style="display:inline">
              <input type="hidden" name="periodo" value="{{ periodo }}">
              <button class="emb-btn-desfazer" title="desfazer">↶</button>
            </form>
          {% else %}
            <form method="post" action="/painel/fornecedor/rotas/{{ c.id }}/entregar" style="display:inline">
              <input type="hidden" name="periodo" value="{{ periodo }}">
              <button class="rot-btn-entregar">✓ Entregue</button>
            </form>
          {% endif %}
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endfor %}

  {% else %}
  <div class="ped-vazio">
    <div style="font-size:2.5rem;opacity:.4;margin-bottom:.5rem">🚚</div>
    <p>Sem entregas pra esse período.</p>
    <p class="mut" style="margin-top:.5rem;font-size:.9rem">Cestas confirmadas viram a rota de entrega.</p>
  </div>
  {% endif %}
</div>

<style>
.ped-back{color:var(--verde-claro);display:inline-block;margin-bottom:.8rem;font-size:.9rem;text-decoration:none}
.ped-back:hover{text-decoration:underline}
.ped-abas{display:flex;gap:0;border-bottom:1px solid var(--borda);margin-bottom:1rem;overflow-x:auto}
.ped-aba{padding:.55rem 1rem;color:var(--txt-mut);border-bottom:2px solid transparent;text-decoration:none;font-size:.92rem;white-space:nowrap}
.ped-aba:hover{color:var(--txt)}
.ped-aba.ativa{color:var(--verde-claro);border-color:var(--verde-claro);font-weight:600}
.ped-aba.off{color:#5a5a5a;font-size:.88rem;cursor:default}
.sep-filtro{display:flex;align-items:center;gap:.8rem;margin-bottom:1rem;padding:.7rem .9rem;background:var(--card-2);border:1px solid var(--borda);border-radius:8px;flex-wrap:wrap}
.sep-filtro select{background:#0a0a0a;color:var(--txt);border:1px solid var(--borda);border-radius:6px;padding:.5rem .7rem;font-size:.9rem;cursor:pointer}
.sep-range{color:var(--txt-mut);font-size:.85rem}
.sep-resumo{display:grid;gap:.6rem;margin-bottom:1rem}
.sep-card{background:var(--card-2);border:1px solid var(--borda);border-radius:8px;padding:.9rem;text-align:center}
.sep-card-num{color:var(--verde-claro);font-size:1.5rem;font-weight:700;line-height:1.1}
.sep-card-rot{color:var(--txt-mut);font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;margin-top:.3rem}
.sep-alerta{background:#3a2d12;border:1px solid #6b5320;border-radius:8px;padding:.8rem 1rem;margin-bottom:1rem;color:#e0a83d;font-size:.9rem}
.sep-alerta-link{color:#e0a83d;text-decoration:underline;margin-left:.4rem;white-space:nowrap}
.rot-grupo{margin-bottom:1.5rem}
.rot-grupo-tit{color:var(--verde-claro);font-size:1rem;margin:0 0 .6rem;padding-bottom:.4rem;border-bottom:1px solid var(--borda);display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:.5rem}
.rot-grupo-meta{color:var(--txt-mut);font-size:.82rem;font-weight:400}
.rot-cestas{display:flex;flex-direction:column;gap:.5rem}
.rot-cesta{display:flex;justify-content:space-between;gap:1rem;background:var(--card-2);border:1px solid var(--borda);border-radius:6px;padding:.9rem 1rem;align-items:flex-start;flex-wrap:wrap}
.rot-c-entregue{border-left:3px solid var(--verde);opacity:.65}
.rot-c-nemb{border-left:3px solid #e0a83d}
.rot-c-info{flex:1;min-width:200px}
.rot-c-nome{color:var(--txt);font-weight:600;font-size:.95rem}
.rot-tag-nemb{display:inline-block;color:#e0a83d;background:#3a2d12;font-size:.7rem;font-weight:500;padding:.15rem .4rem;border-radius:4px;margin-left:.4rem;vertical-align:middle}
.rot-c-end{color:var(--txt-mut);font-size:.85rem;margin-top:.25rem}
.rot-c-meta{color:#7a7a7a;font-size:.78rem;margin-top:.2rem}
.rot-c-acoes{display:flex;gap:.4rem;align-items:flex-start}
.rot-btn-entregar{background:#15241d;color:var(--verde-claro);border:1px solid var(--verde-claro);border-radius:6px;padding:.5rem .9rem;cursor:pointer;font-size:.88rem;font-weight:500;white-space:nowrap}
.rot-btn-entregar:hover{background:#1d2e25}
.rot-pronto{color:var(--verde);font-weight:600;font-size:.85rem;text-align:center;line-height:1.3}
.rot-pronto small{color:#7a7a7a;font-size:.7rem;font-weight:400}
.emb-btn-desfazer{background:transparent;color:#7a7a7a;border:1px solid var(--borda);border-radius:6px;padding:.4rem .55rem;cursor:pointer;font-size:.9rem}
.emb-btn-desfazer:hover{color:#c66;border-color:#3a2020}
.ped-vazio{text-align:center;padding:3rem 1rem;color:var(--txt-mut)}
@media (max-width:520px){
  .sep-resumo{grid-template-columns:repeat(2,1fr) !important;gap:.4rem}
  .rot-cesta{flex-direction:column}
  .rot-c-acoes{width:100%}
  .rot-btn-entregar{flex:1}
}
</style>
{% endblock %}"""

_ESQUECI_SENHA = """{% extends "base" %}{% block conteudo %}
<div class="card">
  <h1>Recuperar Senha</h1>
  {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
  {% if enviado %}
  <div class="ok">
    <p>✅ Se esse e-mail tem uma conta no Zaq, enviamos um link de recuperação.</p>
    <p style="font-size:.9rem;color:var(--txt-mut);margin-top:.8rem">
      Confira sua caixa de entrada (e a pasta de spam). O link é válido por 1 hora.
    </p>
  </div>
  <p style="text-align:center;margin-top:1.5rem">
    <a href="/login" style="color:var(--verde-claro);font-size:.88rem;text-decoration:none">Voltar ao login</a>
  </p>
  {% else %}
  <p style="color:var(--txt-mut);margin-bottom:1.2rem">
    Digite o e-mail da sua conta e enviaremos um link pra redefinir sua senha.
  </p>
  <form method="post" action="/esqueci-senha">
    <label>E-mail</label>
    <input name="email" type="email" required>
    <button>Enviar link</button>
  </form>
  <p style="text-align:center;margin-top:.8rem">
    <a href="/login" style="color:var(--verde-claro);font-size:.88rem;text-decoration:none">Voltar ao login</a>
  </p>
  {% endif %}
</div>
{% endblock %}"""

_REDEFINIR_SENHA = """{% extends "base" %}{% block conteudo %}
<div class="card">
  <h1>Redefinir Senha</h1>
  {% if not valido %}
  <div class="erro">
    <p>❌ Link inválido ou expirado.</p>
    <p style="font-size:.9rem;margin-top:.5rem">
      Os links de recuperação são válidos por 1 hora. Peça um novo em <a href="/esqueci-senha" style="color:var(--verde-claro)">esqueci minha senha</a>.
    </p>
  </div>
  {% else %}
  {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
  <form method="post" action="/redefinir-senha">
    <input type="hidden" name="token" value="{{ token }}">
    <label>Nova Senha</label>
    <input name="senha" type="password" required minlength="8"
           placeholder="Mínimo 8 caracteres">
    <button>Redefinir Senha</button>
  </form>
  {% endif %}
</div>
{% endblock %}"""

_PEDIDO_ENVIADO = """{% extends "base" %}{% block conteudo %}
<div class="card" style="max-width:420px;margin:0 auto;text-align:center">
  <div style="width:58px;height:58px;border-radius:50%;background:#15301f;border:1px solid var(--verde);display:flex;align-items:center;justify-content:center;margin:0 auto 1rem">
    <span style="font-size:28px;color:var(--verde)">✓</span>
  </div>
  <h2>Pedido enviado!</h2>
  <p class="mut" style="font-size:.85rem">O fornecedor vai confirmar seu pedido e avisar você pelo WhatsApp.</p>

  <div style="text-align:left;margin:1.2rem 0">
    <div style="display:flex;align-items:center;gap:11px;margin:.4rem 0"><div style="width:28px;height:28px;border-radius:50%;background:var(--verde);display:flex;align-items:center;justify-content:center;color:#fff">✓</div><span style="font-size:.82rem;color:var(--txt)">Pedido recebido</span></div>
    <div style="display:flex;align-items:center;gap:11px;margin:.4rem 0"><div style="width:28px;height:28px;border-radius:50%;background:#1c1c1f;border:1px solid #888780;display:flex;align-items:center;justify-content:center;color:#888780">⏳</div><span style="font-size:.82rem;color:#888780">Aguardando confirmação</span></div>
    <div style="display:flex;align-items:center;gap:11px;margin:.4rem 0"><div style="width:28px;height:28px;border-radius:50%;background:#1c1c1f;border:1px solid var(--borda);display:flex;align-items:center;justify-content:center;color:#5f5e5a">💳</div><span style="font-size:.82rem;color:#5f5e5a">Pagamento (após confirmar)</span></div>
    <div style="display:flex;align-items:center;gap:11px;margin:.4rem 0"><div style="width:28px;height:28px;border-radius:50%;background:#1c1c1f;border:1px solid var(--borda);display:flex;align-items:center;justify-content:center;color:#5f5e5a">🚚</div><span style="font-size:.82rem;color:#5f5e5a">A caminho</span></div>
  </div>

  {% if carrinho %}
  <div style="background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem;text-align:left;margin-bottom:1rem">
    <div style="display:flex;justify-content:space-between;font-size:.8rem"><span class="mut">Pedido #{{ carrinho.id }}</span><span style="color:var(--verde-claro);background:#15301f;padding:2px 9px;border-radius:20px;font-size:.72rem">aguardando</span></div>
    <div style="display:flex;justify-content:space-between;border-top:1px solid var(--borda);margin-top:6px;padding-top:6px;font-size:.85rem"><span style="color:var(--txt)">Total</span><span style="color:var(--verde-claro);font-weight:600">R$ {{ (carrinho.total_centavos/100)|n2 }}</span></div>
  </div>
  {% endif %}

  {% if carrinho and carrinho.endereco_entrega %}
  <div style="background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem;text-align:left;margin-bottom:1rem">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:.5rem">
      <div style="font-size:.82rem"><span class="mut">Entregar em</span><br><span style="color:var(--txt)">{{ carrinho.endereco_entrega }}</span></div>
      {% if carrinho.status in ['rascunho','aguardando','confirmado'] %}<button type="button" onclick="var f=document.getElementById('ped-end-form');f.style.display=(f.style.display==='none'?'block':'none');" style="width:auto;margin:0;padding:.35rem .8rem;font-size:.78rem;background:transparent;border:1px solid #333;color:var(--verde-claro)">editar</button>{% endif %}
    </div>
    {% if carrinho.status in ['rascunho','aguardando','confirmado'] %}
    <form id="ped-end-form" method="post" action="/pedido/{{ carrinho.id }}/endereco" style="display:none;margin-top:.6rem">
      <input name="endereco" value="{{ carrinho.endereco_entrega }}" required style="width:100%;margin-bottom:.5rem">
      <button style="width:auto;margin:0;padding:.45rem 1rem;font-size:.85rem">Salvar endereco</button>
    </form>
    {% else %}
    <div class="mut" style="font-size:.72rem;margin-top:.4rem">Ja saiu pra entrega — fale com o fornecedor pra mudar.</div>
    {% endif %}
  </div>
  {% endif %}

  <a href="/painel" style="display:block;background:#1c1c1f;color:var(--txt);border:1px solid var(--borda);border-radius:10px;padding:.7rem;text-decoration:none;font-size:.85rem;margin-bottom:.5rem">Ver meus pedidos</a>
  <a href="/f/{{ fornecedor_slug or '' }}" style="color:var(--verde-claro);font-size:.8rem;text-decoration:none">Voltar à loja</a>
</div>
{% endblock %}"""

_REVISAR = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Seu pedido · {{ loja }}</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>body{margin:0;background:#eef0ec;font-family:'Poppins',system-ui,sans-serif;color:#23301f}*{box-sizing:border-box}
.rz{max-width:640px;margin:0 auto;padding:18px 14px 50px}
.rz-card{background:#fff;border:1px solid #e8ece5;border-radius:16px;padding:20px;margin-bottom:14px;box-shadow:0 1px 3px rgba(20,40,15,.05)}
.rz-tit{font-size:12px;font-weight:700;color:#31772f;text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px}
.rz input,.rz textarea{width:100%;padding:11px 13px;background:#f8faf6;border:1px solid #e2e7dd;color:#23301f;border-radius:10px;font-size:13.5px;font-family:inherit;outline:none}
.rz input:focus,.rz textarea:focus{border-color:#2f7d32}
.rz-fp{display:flex;align-items:center;gap:11px;border:1.5px solid #e7eae5;border-radius:12px;padding:12px 14px;margin-bottom:8px;cursor:pointer;font-size:13.5px}
.rz-fp input{width:auto}
.rz-fp.on{border-color:#2f7d32;background:#eef6ea}
</style></head><body><div class="rz">
<a href="/f/{{ slug }}" style="display:inline-block;color:#31772f;text-decoration:none;font-size:13px;font-weight:500;margin-bottom:12px">← Voltar à loja</a>
<h2 style="margin:0 0 4px;font-size:21px">Seu pedido</h2>
<div style="font-size:13px;color:#6b7669;margin-bottom:16px">{{ loja }}</div>

<div class="rz-card">
  <div class="rz-tit">Itens</div>
  {% for i in car["itens"] %}
  <div style="display:flex;align-items:center;gap:9px;padding:8px 0;border-bottom:1px solid #f0f2ee;font-size:13px">
    <span style="min-width:46px;text-align:center;background:#eef6ea;color:#31772f;font-weight:700;font-size:11px;border-radius:7px;padding:3px 6px">{{ i["quantidade"] }}{{ i["unidade"] }}</span>
    <span style="flex:1">{{ i["nome"] }}</span>
    <span style="font-weight:600">R$ {{ (i["total_centavos"]/100)|n2 }}</span>
  </div>
  {% endfor %}
  <div style="display:flex;justify-content:space-between;font-size:13px;color:#6b7669;padding:9px 0 0">
    <span>Subtotal</span><span>R$ {{ (car["subtotal_centavos"]/100)|n2 }}</span></div>
  <div style="display:flex;justify-content:space-between;font-size:13px;color:#6b7669;padding:4px 0">
    <span>Entrega</span><span>R$ {{ (car["taxa_centavos"]/100)|n2 }}</span></div>
  <div style="display:flex;justify-content:space-between;font-size:15px;font-weight:700;border-top:1px solid #e7eae5;margin-top:6px;padding-top:10px">
    <span>Total</span><span style="color:#2f7d32">R$ {{ (car["total_centavos"]/100)|n2 }}</span></div>
  {% if pix_pct %}<div style="display:flex;justify-content:space-between;font-size:12.5px;color:#31772f;padding-top:2px">
    <span>no Pix na entrega (−{{ "%.0f"|format(pix_pct) }}%)</span><span style="font-weight:700">R$ {{ (car["total_centavos"]*(1-pix_pct/100)/100)|n2 }}</span></div>{% endif %}
</div>

<form method="post" action="/f/{{ slug }}/carrinho/checkout" id="rzform" onsubmit="return rzCompose()">
  <div class="rz-card">
    <div class="rz-tit">Entrega</div>
    {% if endereco_pre %}<div style="background:#eef6ea;border:1px solid #cfe6d4;border-radius:10px;padding:10px 12px;margin-bottom:12px;font-size:12.5px;color:#23301f">📍 Entregar no meu endereço salvo:<div style="color:#31772f;margin:3px 0 8px">{{ endereco_pre }}</div><button type="button" onclick="usarSalvo()" style="background:#0f7d5c;color:#fff;border:0;border-radius:8px;padding:8px 14px;font-size:12.5px;font-weight:600;cursor:pointer">Usar este endereço</button><input type="hidden" id="rzsalvo" value="{{ endereco_pre }}"></div><div style="text-align:center;font-size:12px;color:#8a938a;margin-bottom:12px">ou informe outro endereço:</div>{% endif %}
    <button type="button" id="rzgps" style="width:100%;background:#0f7d5c;color:#fff;border:0;border-radius:10px;padding:12px;font-size:13.5px;font-weight:600;cursor:pointer;margin-bottom:12px">📍 <span id="rzgpstxt">Usar minha localização</span></button>
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;color:#8a938a;font-size:12px"><div style="flex:1;height:1px;background:#e7eae5"></div>ou digite o CEP<div style="flex:1;height:1px;background:#e7eae5"></div></div>
    <label style="font-size:12.5px;color:#3a463a;font-weight:500">CEP</label>
    <input id="rzcep" inputmode="numeric" maxlength="9" placeholder="64000-000" autocomplete="off" value="{{ cep_pre|default('') }}" style="margin:5px 0 12px">
    <div style="display:flex;gap:8px">
      <div style="flex:1"><label style="font-size:12.5px;color:#3a463a;font-weight:500">Rua</label>
        <input id="rzrua" placeholder="Rua" style="margin:5px 0 12px"></div>
      <div style="width:96px"><label style="font-size:12.5px;color:#3a463a;font-weight:500">Número</label>
        <input id="rznum" inputmode="numeric" placeholder="123" style="margin:5px 0 12px"></div>
    </div>
    <div style="display:flex;gap:8px">
      <div style="flex:1"><label style="font-size:12.5px;color:#3a463a;font-weight:500">Bairro</label>
        <input id="rzbairro" placeholder="Bairro" style="margin:5px 0 12px"></div>
      <div style="flex:1"><label style="font-size:12.5px;color:#3a463a;font-weight:500">Complemento</label>
        <input id="rzcompl" placeholder="apto, bloco…" style="margin:5px 0 12px"></div>
    </div>
    <div id="rzcidade" style="font-size:12px;color:#31772f;margin-bottom:8px"></div>
    <label style="font-size:12.5px;color:#3a463a;font-weight:500">Observação (opcional)</label>
    <textarea name="obs" rows="2" placeholder="Ex: deixar na portaria" style="margin-top:5px;resize:none">{{ obs_pre }}</textarea>
    <input type="hidden" name="endereco" id="rzendereco">
    <input type="hidden" name="cep" id="rzcephidden" value="{{ cep_pre|default('') }}">
    <input type="hidden" name="bairro" id="rzbairro_h">
  </div>
  <div class="rz-card">
    <div class="rz-tit">Pagamento</div>
    <label class="rz-fp on"><input type="radio" name="forma" value="entrega_pix" checked onchange="fp(this)"> 💠 Pix na entrega{% if pix_pct %} <span style="color:#31772f;font-weight:700;margin-left:auto">R$ {{ (car["total_centavos"]*(1-pix_pct/100)/100)|n2 }}</span>{% endif %}</label>
    <label class="rz-fp"><input type="radio" name="forma" value="entrega_cartao" onchange="fp(this)"> 💳 Cartão na entrega</label>
    <label class="rz-fp"><input type="radio" name="forma" value="entrega_dinheiro" onchange="fp(this)"> 💵 Dinheiro na entrega</label>
    <label class="rz-fp"><input type="radio" name="forma" value="pagar_agora" onchange="fp(this)"> ⚡ Pagar agora <span style="color:#8a938a;font-size:12px;margin-left:6px">Pix ou cartão online</span></label>
  </div>
  <button type="submit" style="width:100%;background:#f48b22;color:#fff;border:0;border-radius:12px;padding:15px;font-size:14.5px;font-weight:700;cursor:pointer;font-family:inherit">
    {% if anon %}Continuar{% else %}Confirmar pedido{% endif %}</button>
  {% if anon %}<div style="text-align:center;font-size:12.5px;color:#6b7669;margin-top:10px">
    No próximo passo você entra ou cria sua conta rapidinho — o carrinho vai junto. Já tem conta?
    <a href="/login?next=/f/{{ slug }}/carrinho/materializar" style="color:#2f7d32;font-weight:600">Entrar</a></div>
  {% else %}<div style="text-align:center;font-size:12px;color:#8a938a;margin-top:10px">Você recebe a confirmação do fornecedor em seguida.</div>{% endif %}
</form>
<script>
function fp(r){document.querySelectorAll('.rz-fp').forEach(function(l){l.classList.toggle('on', l.contains(r) && r.checked);});}
(function(){
  var $=function(id){return document.getElementById(id);};
  function maskCep(v){v=(v||'').replace(/[^0-9]/g,'').slice(0,8);return v.length>5?v.slice(0,5)+'-'+v.slice(5):v;}
  function setCidade(c,uf,cep){var t=c?(c+(uf?' - '+uf:'')):'';if(cep){t+=(t?'  ·  ':'')+cep;}$('rzcidade').textContent=t;}
  function fill(d){
    if(d.rua){$('rzrua').value=d.rua;}
    if(d.bairro){$('rzbairro').value=d.bairro;}
    var cepfmt=d.cep?maskCep(d.cep):'';
    setCidade(d.cidade,d.uf,cepfmt);
    if(d.cep){$('rzcephidden').value=(''+d.cep).replace(/[^0-9]/g,'');$('rzcep').value=cepfmt;}
    if(d.rua){$('rznum').focus();}else{$('rzrua').focus();}
  }
  var cep=$('rzcep');
  if(cep){cep.addEventListener('input',function(){
    cep.value=maskCep(cep.value);var digs=cep.value.replace(/[^0-9]/g,'');
    $('rzcephidden').value=digs;
    if(digs.length<8){return;}
    fetch('/api/cep/'+digs).then(function(r){return r.json();}).then(function(d){
      if(d&&d.ok){fill(d);}
    }).catch(function(){});
  });}
  var gps=$('rzgps');
  if(gps){gps.addEventListener('click',function(){
    var txt=$('rzgpstxt');
    if(!navigator.geolocation){txt.textContent='GPS indisponível — digite o CEP';return;}
    txt.textContent='Obtendo localização…';gps.disabled=true;
    navigator.geolocation.getCurrentPosition(function(pos){
      fetch('/api/geo?lat='+pos.coords.latitude+'&lng='+pos.coords.longitude)
        .then(function(r){return r.json();}).then(function(d){
          gps.disabled=false;
          if(d&&d.ok){txt.textContent='Localização encontrada ✓';fill(d);}
          else{txt.textContent='Não achei — digite o CEP';}
        }).catch(function(){gps.disabled=false;txt.textContent='Erro — digite o CEP';});
    },function(err){
      gps.disabled=false;
      txt.textContent=(err&&err.code===1)?'Permissão negada — digite o CEP':'Não consegui — digite o CEP';
    },{enableHighAccuracy:true,timeout:8000,maximumAge:0});
  });}
  window.usarSalvo=function(){var s=document.getElementById('rzsalvo'),e=document.getElementById('rzendereco');if(s&&e){e.value=s.value;}document.getElementById('rzform').submit();};
    window.rzCompose=function(){
    var rua=($('rzrua').value||'').trim(),num=($('rznum').value||'').trim(),
        bairro=($('rzbairro').value||'').trim(),compl=($('rzcompl').value||'').trim();
    if(!rua){alert('Preencha a rua da entrega.');$('rzrua').focus();return false;}
    var e=rua;if(num){e+=', '+num;}if(bairro){e+=', '+bairro;}if(compl){e+=' — '+compl;}
    $('rzendereco').value=e;
    var _bh=$('rzbairro_h');if(_bh){_bh.value=bairro;}
    if(!$('rzcephidden').value){$('rzcephidden').value=($('rzcep').value||'').replace(/[^0-9]/g,'');}
    return true;
  };
})();
</script>
</div></body></html>"""

_BLOCO_CONTA = """
<div class="card larga">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <h1 style="font-size:1.05rem;margin:0">Meus dados</h1>
    <button type="button" onclick="dadosEditar()" id="dados-btn" style="width:auto;margin:0;background:transparent;border:1px solid #2f5d4e;color:var(--verde-claro);border-radius:999px;padding:.35rem .9rem;font-size:.8rem;cursor:pointer">✏️ Editar</button>
  </div>
  {% if md_aviso %}<div class="ok" style="margin-top:.8rem">{{ md_aviso }}</div>{% endif %}
  {% if md_erro %}<div class="erro" style="margin-top:.8rem">{{ md_erro }}</div>{% endif %}
  <div id="dados-resumo">
    <div style="display:flex;align-items:center;gap:13px;margin:1rem 0 .3rem">
      <div class="avatar" style="width:46px;height:46px;font-size:1.1rem">{{ (md_nome or '?')[0]|upper }}</div>
      <div><div style="font-size:.98rem;color:var(--txt)">{{ md_nome or '-' }}</div><div class="mut" style="font-size:.78rem;margin-top:2px">titular{% if conta and conta[4] %} · {{ conta[4] }}{% endif %}</div></div>
    </div>
    <div style="margin-top:.7rem;border-top:1px solid var(--borda)">
      <div style="display:flex;align-items:center;gap:11px;padding:.6rem 0;border-bottom:1px solid #212122"><span style="color:var(--verde-claro)">✉️</span><span class="mut" style="flex:1;font-size:.82rem">E-mail</span><span style="font-size:.86rem;color:var(--txt)">{{ md_email or '-' }}{% if md_email_pendente %}<div style="font-size:.72rem;color:#e0a83d">a confirmar: {{ md_email_pendente }}</div>{% endif %}</span></div>
      <div style="display:flex;align-items:center;gap:11px;padding:.6rem 0;border-bottom:1px solid #212122"><span style="color:var(--verde-claro)">📱</span><span class="mut" style="flex:1;font-size:.82rem">WhatsApp</span><span style="font-size:.86rem;color:var(--txt)">{{ md_whatsapp or '-' }}</span></div>
      <div style="display:flex;align-items:center;gap:11px;padding:.6rem 0"><span style="color:var(--verde-claro)">🔒</span><span class="mut" style="flex:1;font-size:.82rem">Senha</span><a href="/senha" style="font-size:.86rem;color:var(--verde-claro);text-decoration:none">alterar</a></div>
    </div>
  </div>
  <div id="dados-form" style="display:none">
    <form method="post" action="/painel/dados">
      <label>Nome</label>
      <input name="nome" value="{{ md_nome or '' }}" required maxlength="80">
      <label>WhatsApp (com DDD)</label>
      <input name="whatsapp" value="{{ md_whatsapp or '' }}" required maxlength="20" placeholder="86 98888-7777">
      <label>E-mail</label>
      <input name="email" type="email" value="{{ md_email or '' }}" maxlength="120" placeholder="seu@email.com">
      <p class="mut" style="font-size:.76rem;margin:.6rem 0 0">Trocar o número reenvia o código pra reconectar no bot. Trocar o e-mail envia um link de confirmação — o novo só vale depois de confirmar.</p>
      <div style="display:flex;gap:.6rem;margin-top:1rem">
        <button style="width:auto;margin:0;padding:.6rem 1.4rem">Salvar</button>
        <button type="button" onclick="dadosCancelar()" style="width:auto;margin:0;padding:.6rem 1.4rem;background:transparent;border:1px solid #333;color:var(--txt-mut)">Cancelar</button>
      </div>
    </form>
  </div>
</div>

<div class="card larga">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <h1 style="font-size:1.05rem;margin:0">Meu endereço</h1>
    {% if md_endereco %}<button type="button" onclick="mdEditar()" id="md-btn" style="width:auto;margin:0;background:transparent;border:1px solid #2f5d4e;color:var(--verde-claro);border-radius:999px;padding:.35rem .9rem;font-size:.8rem;cursor:pointer">✏️ Editar</button>{% endif %}
  </div>
  {% if md_endereco %}
  <div id="md-atual" style="display:flex;align-items:flex-start;gap:13px;margin-top:1rem">
    <div style="width:42px;height:42px;border-radius:12px;background:#13251d;color:var(--verde-claro);display:flex;align-items:center;justify-content:center;flex:none;font-size:1.2rem">🏠</div>
    <div><div style="font-size:.9rem;color:var(--txt);line-height:1.45">{{ md_endereco }}</div>{% if md_cep %}<div class="mut" style="font-size:.8rem;margin-top:3px">CEP {{ md_cep }}</div>{% endif %}</div>
  </div>
  {% endif %}
  <div id="md-form-wrap"{% if md_endereco %} style="display:none;margin-top:1rem"{% endif %}>
    <p class="mut" style="margin:.2rem 0 .8rem;font-size:.83rem">Mudou de casa? Atualize aqui — vira o padrão nos próximos pedidos.</p>
    <form method="post" action="/painel/endereco" id="mdform" onsubmit="return mdCompose()">
      <button type="button" id="mdgps" style="margin:0;background:#0f7d5c">📍 <span id="mdgpstxt">Usar minha localização</span></button>
      <label>CEP</label>
      <input id="mdcep" inputmode="numeric" maxlength="9" placeholder="64000-000" autocomplete="off" value="{{ md_cep or '' }}">
      <div style="display:flex;gap:.6rem">
        <div style="flex:1"><label>Rua</label><input id="mdrua" placeholder="Rua"></div>
        <div style="width:92px"><label>Número</label><input id="mdnum" inputmode="numeric" placeholder="123"></div>
      </div>
      <div style="display:flex;gap:.6rem">
        <div style="flex:1"><label>Bairro</label><input id="mdbairro" placeholder="Bairro"></div>
        <div style="flex:1"><label>Complemento</label><input id="mdcompl" placeholder="apto, bloco…"></div>
      </div>
      <div id="mdcidade" style="font-size:.82rem;color:var(--verde-claro);margin-top:.5rem"></div>
      <input type="hidden" name="endereco" id="mdendereco">
      <input type="hidden" name="cep" id="mdcephidden" value="{{ md_cep or '' }}">
      <input type="hidden" name="bairro" id="mdbairro_h">
      <div style="display:flex;gap:.6rem;margin-top:1rem">
        <button style="width:auto;margin:0;padding:.6rem 1.4rem">Salvar endereço</button>
        {% if md_endereco %}<button type="button" onclick="mdCancelar()" style="width:auto;margin:0;padding:.6rem 1.4rem;background:transparent;border:1px solid #333;color:var(--txt-mut)">Cancelar</button>{% endif %}
      </div>
    </form>
  </div>
  <script>
  window.dadosEditar=function(){var f=document.getElementById('dados-form'),r=document.getElementById('dados-resumo'),b=document.getElementById('dados-btn');if(f)f.style.display='block';if(r)r.style.display='none';if(b)b.style.display='none';};
  window.dadosCancelar=function(){var f=document.getElementById('dados-form'),r=document.getElementById('dados-resumo'),b=document.getElementById('dados-btn');if(f)f.style.display='none';if(r)r.style.display='block';if(b)b.style.display='inline-block';};
  window.mdEditar=function(){var w=document.getElementById('md-form-wrap'),a=document.getElementById('md-atual'),b=document.getElementById('md-btn');if(w)w.style.display='block';if(a)a.style.display='none';if(b)b.style.display='none';};
  window.mdCancelar=function(){var w=document.getElementById('md-form-wrap'),a=document.getElementById('md-atual'),b=document.getElementById('md-btn');if(w)w.style.display='none';if(a)a.style.display='flex';if(b)b.style.display='inline-block';};
  (function(){
    var $=function(id){return document.getElementById(id);};
    function maskCep(v){v=(v||'').replace(/[^0-9]/g,'').slice(0,8);return v.length>5?v.slice(0,5)+'-'+v.slice(5):v;}
    function setCidade(c,uf,cep){var t=c?(c+(uf?' - '+uf:'')):'';if(cep){t+=(t?'  ·  ':'')+cep;}var e=$('mdcidade');if(e)e.textContent=t;}
    function fill(d){if(d.rua)$('mdrua').value=d.rua;if(d.bairro)$('mdbairro').value=d.bairro;var cf=d.cep?maskCep(d.cep):'';setCidade(d.cidade,d.uf,cf);if(d.cep){$('mdcephidden').value=(''+d.cep).replace(/[^0-9]/g,'');$('mdcep').value=cf;}if(d.rua){$('mdnum').focus();}else{$('mdrua').focus();}}
    var cep=$('mdcep');
    if(cep)cep.addEventListener('input',function(){cep.value=maskCep(cep.value);var digs=cep.value.replace(/[^0-9]/g,'');$('mdcephidden').value=digs;if(digs.length<8)return;fetch('/api/cep/'+digs).then(function(r){return r.json();}).then(function(d){if(d&&d.ok)fill(d);}).catch(function(){});});
    var gps=$('mdgps');
    if(gps)gps.addEventListener('click',function(){var t=$('mdgpstxt');if(!navigator.geolocation){t.textContent='GPS indisponível — digite o CEP';return;}t.textContent='Obtendo localização…';gps.disabled=true;navigator.geolocation.getCurrentPosition(function(pos){fetch('/api/geo?lat='+pos.coords.latitude+'&lng='+pos.coords.longitude).then(function(r){return r.json();}).then(function(d){gps.disabled=false;if(d&&d.ok){t.textContent='Localização encontrada ✓';fill(d);}else{t.textContent='Não achei — digite o CEP';}}).catch(function(){gps.disabled=false;t.textContent='Erro — digite o CEP';});},function(err){gps.disabled=false;t.textContent=(err&&err.code===1)?'Permissão negada — digite o CEP':'Não consegui — digite o CEP';},{enableHighAccuracy:true,timeout:8000,maximumAge:0});});
    window.mdCompose=function(){var rua=($('mdrua').value||'').trim(),num=($('mdnum').value||'').trim(),bairro=($('mdbairro').value||'').trim(),compl=($('mdcompl').value||'').trim();if(!rua){alert('Preencha a rua.');$('mdrua').focus();return false;}var e=rua;if(num)e+=', '+num;if(bairro)e+=', '+bairro;if(compl)e+=' — '+compl;$('mdendereco').value=e;var _mbh=document.getElementById('mdbairro_h');if(_mbh)_mbh.value=bairro;if(!$('mdcephidden').value)$('mdcephidden').value=($('mdcep').value||'').replace(/[^0-9]/g,'');return true;};
  })();
  </script>
</div>
"""


_AVULSOS_FORN = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.2rem">
    <h1 style="font-size:1.2rem;margin:0">Pedidos avulsos</h1>
    <a href="/painel/fornecedor/pedidos" style="color:var(--verde-claro);font-size:.85rem;text-decoration:none">Cestas &rsaquo;</a>
  </div>
  <p class="mut" style="margin:.3rem 0 1rem">Pedidos feitos direto na sua loja (fora das assinaturas de cesta).</p>
  {% set fstatus = status or '' %}
  <div style="display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:1rem">
    <a href="/painel/fornecedor/avulsos" class="tag" style="text-decoration:none{% if not fstatus %};background:var(--verde);color:#fff;border-color:var(--verde){% endif %}">Todos</a>
    <a href="/painel/fornecedor/avulsos?status=aguardando" class="tag" style="text-decoration:none{% if fstatus=='aguardando' %};background:var(--verde);color:#fff;border-color:var(--verde){% endif %}">Aguardando</a>
    <a href="/painel/fornecedor/avulsos?status=confirmado" class="tag" style="text-decoration:none{% if fstatus=='confirmado' %};background:var(--verde);color:#fff;border-color:var(--verde){% endif %}">Confirmado</a>
    <a href="/painel/fornecedor/avulsos?status=em_entrega" class="tag" style="text-decoration:none{% if fstatus=='em_entrega' %};background:var(--verde);color:#fff;border-color:var(--verde){% endif %}">Em entrega</a>
    <a href="/painel/fornecedor/avulsos?status=entregue" class="tag" style="text-decoration:none{% if fstatus=='entregue' %};background:var(--verde);color:#fff;border-color:var(--verde){% endif %}">Entregues</a>
  </div>
  {% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
  {% if not pedidos %}<p class="mut" style="text-align:center;padding:2rem 0">Nenhum pedido avulso{% if fstatus %} nesse status{% endif %} por aqui ainda.</p>{% endif %}
  {% set cor = {'aguardando':'#e0a83d','confirmado':'#3a78c2','em_entrega':'#5b8def','entregue':'var(--verde)','cancelado':'#8a3636'} %}
  {% for p in pedidos %}
  <div style="background:var(--bg);border:1px solid var(--borda);border-left:3px solid {{ cor.get(p.status,'var(--borda)') }};border-radius:12px;padding:.9rem 1rem;margin-bottom:.8rem{% if p.status in ['entregue','cancelado'] %};opacity:.7{% endif %}">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:.5rem">
      <div><b>{{ p.cliente_nome }}</b> <span class="mut" style="font-size:.78rem">#{{ p.id }}{% if p.criado_em %} &middot; {{ p.criado_em.strftime('%d/%m %H:%M') }}{% endif %}</span></div>
      <span class="tag" style="border-color:{{ cor.get(p.status,'#444') }};color:{{ cor.get(p.status,'#bbb') }}">{{ p.status }}{% if p.pago %} &middot; pago{% endif %}</span>
    </div>
    {% if p.endereco_entrega %}<div class="mut" style="font-size:.83rem;margin:.5rem 0">📍 {{ p.endereco_entrega }}{% if p.forma_label %} &middot; <span style="color:#c5c5c0">{{ p.forma_label }}</span>{% endif %}</div>{% endif %}
    {% if p.obs %}<div class="mut" style="font-size:.8rem;margin:.2rem 0 .3rem">Obs: {{ p.obs }}</div>{% endif %}
    <div style="display:flex;justify-content:space-between;align-items:center;border-top:1px solid #212122;padding-top:.6rem;margin-top:.5rem">
      <span style="color:var(--verde-claro);font-weight:600">R$ {{ (p.total_centavos/100)|n2 }}</span>
      <div style="display:flex;gap:.4rem">
        {% if p.status == 'aguardando' %}
          <form method="post" action="/painel/fornecedor/avulsos/{{ p.id }}/status" style="margin:0"><input type="hidden" name="novo" value="cancelado"><button style="width:auto;margin:0;padding:.4rem .8rem;font-size:.8rem;background:transparent;border:1px solid #6e2b2b;color:#e89a9a">Recusar</button></form>
          <form method="post" action="/painel/fornecedor/avulsos/{{ p.id }}/status" style="margin:0"><input type="hidden" name="novo" value="confirmado"><button style="width:auto;margin:0;padding:.4rem .9rem;font-size:.8rem">Confirmar</button></form>
        {% elif p.status == 'confirmado' %}
          <form method="post" action="/painel/fornecedor/avulsos/{{ p.id }}/status" style="margin:0"><input type="hidden" name="novo" value="cancelado"><button style="width:auto;margin:0;padding:.4rem .8rem;font-size:.8rem;background:transparent;border:1px solid #333;color:var(--txt-mut)">Cancelar</button></form>
          <form method="post" action="/painel/fornecedor/avulsos/{{ p.id }}/status" style="margin:0"><input type="hidden" name="novo" value="em_entrega"><button style="width:auto;margin:0;padding:.4rem .9rem;font-size:.8rem">Saiu pra entrega</button></form>
        {% elif p.status == 'em_entrega' %}
          <form method="post" action="/painel/fornecedor/avulsos/{{ p.id }}/status" style="margin:0"><input type="hidden" name="novo" value="entregue"><button style="width:auto;margin:0;padding:.4rem .9rem;font-size:.8rem">Marcar entregue</button></form>
        {% endif %}
      </div>
    </div>
  </div>
  {% endfor %}
  <a href="/painel/fornecedor" style="color:var(--verde-claro);text-decoration:none;font-size:.85rem">&larr; voltar</a>
</div>
{% endblock %}"""


_PEDIDOS_UNI = """{% extends "base" %}{% block conteudo %}
<style>
.ped-back{color:var(--verde-claro);display:inline-block;margin-bottom:.6rem;font-size:.9rem;text-decoration:none}
.ped-abas{display:flex;gap:.4rem;overflow-x:auto;padding-bottom:4px;margin:.2rem 0 1.1rem}
.ped-aba{flex:0 0 auto;padding:.5rem .95rem;border-radius:9px;border:1px solid var(--borda);color:var(--txt-mut);text-decoration:none;font-size:.88rem;white-space:nowrap}
.ped-aba.ativa{background:var(--card);color:var(--verde-claro);border-color:#2f5d4e;font-weight:500}
.uni-card{transition:border-color .15s}
</style>
{% macro card(p) %}
<div class="uni-card" data-tipo="{{ p.tipo }}" style="background:var(--card);border:1px solid var(--borda);border-left:3px solid {{ '#7fd4a8' if p.tipo=='cesta' else '#e0b877' }};border-radius:12px;padding:.8rem 1rem;margin-bottom:.7rem{% if p.esta_entregue %};opacity:.7{% endif %}">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:.5rem">
    <div>
      <span style="font-size:.72rem;padding:1px 8px;border-radius:999px;font-weight:600;{{ 'background:#1e2b23;color:#7fd4a8' if p.tipo=='cesta' else 'background:#2b2620;color:#e0b877' }}">{{ '🧺 cesta' if p.tipo=='cesta' else '📦 avulso' }}</span>
      <b style="margin-left:.4rem">{{ p.cliente_nome }}</b>
      <div class="mut" style="font-size:.78rem;margin-top:.2rem">{{ p.bairro or 'sem bairro' }}{% if p.qtd_itens %} · {{ p.qtd_itens }} itens{% endif %}</div>
    </div>
    <div style="text-align:right;white-space:nowrap">
      {% if p.tipo=='cesta' and p.tamanho_nome %}<span class="mut" style="font-size:.8rem">{{ p.tamanho_nome }}</span>{% endif %}
      {% if p.tipo=='avulso' %}<span style="color:var(--verde-claro);font-weight:600">R$ {{ (p.total_centavos/100)|n2 }}</span>{% endif %}
    </div>
  </div>
  {% if p.endereco %}<div class="mut" style="font-size:.78rem;margin:.4rem 0 0">📍 {{ p.endereco }}</div>{% endif %}
  {{ caller() }}
</div>
{% endmacro %}

<div class="card larga">
  <a href="/painel/fornecedor" class="ped-back">← voltar</a>
  <h2 style="margin:.2rem 0 1rem">📋 Pedidos</h2>
  <div class="ped-abas" style="overflow-x:auto">
    {% for f, lab in [('novos','Novos'),('separacao','Separação'),('embalagem','Embalagem'),('rotas','Rotas'),('entregues','Entregues')] %}
    <a href="/painel/fornecedor/pedidos?fase={{ f }}" class="ped-aba{% if fase==f %} ativa{% endif %}">{{ lab }}{% if f=='novos' and n_novos %} <span style="background:#e0a83d;color:#111;border-radius:999px;padding:0 6px;font-size:.7rem">{{ n_novos }}</span>{% endif %}</a>
    {% endfor %}
  </div>
  {% if erro %}<div class="erro" style="margin-top:1rem">{{ erro }}</div>{% endif %}
  {% if aviso %}<div class="ok" style="margin-top:1rem">{{ aviso }}</div>{% endif %}

  {% if fase in ['embalagem','rotas','entregues'] %}
  <div style="display:flex;gap:.4rem;margin:1rem 0 .4rem">
    <button type="button" class="tp-btn" data-tp="todos" onclick="filtTipo(this)" style="font-size:12px;padding:5px 13px;border-radius:999px;cursor:pointer;border:1px solid var(--verde);background:var(--verde);color:#fff">Todos</button>
    <button type="button" class="tp-btn" data-tp="cesta" onclick="filtTipo(this)" style="font-size:12px;padding:5px 13px;border-radius:999px;cursor:pointer;border:1px solid var(--borda);background:transparent;color:var(--txt-mut)">🧺 Cestas</button>
    <button type="button" class="tp-btn" data-tp="avulso" onclick="filtTipo(this)" style="font-size:12px;padding:5px 13px;border-radius:999px;cursor:pointer;border:1px solid var(--borda);background:transparent;color:var(--txt-mut)">📦 Avulsos</button>
  </div>
  {% endif %}

  {% if fase == 'novos' %}
    {% if not novos %}<p class="mut" style="text-align:center;padding:2rem 0">Nenhum pedido avulso aguardando aceite.</p>{% endif %}
    {% for p in novos %}
    {% call card(p) %}
    <div style="display:flex;justify-content:space-between;align-items:center;border-top:1px solid #212122;margin-top:.6rem;padding-top:.6rem">
      <span class="mut" style="font-size:.78rem">{{ p.forma_label }}{% if p.criado_em %} · {{ p.criado_em.strftime('%d/%m %H:%M') }}{% endif %} · <a href="/painel/fornecedor/embalagem/avulso/{{ p.id }}/etiqueta" target="_blank" style="color:var(--verde-claro);text-decoration:none">🏷️ etiqueta</a></span>
      <div style="display:flex;gap:.4rem">
        <form method="post" action="/painel/fornecedor/pedidos/avulso/{{ p.id }}/recusar" style="margin:0"><input type="hidden" name="fase" value="novos"><button style="width:auto;margin:0;padding:.4rem .8rem;font-size:.8rem;background:transparent;border:1px solid #6e2b2b;color:#e89a9a">Recusar</button></form>
        <form method="post" action="/painel/fornecedor/pedidos/avulso/{{ p.id }}/aceitar" style="margin:0"><input type="hidden" name="fase" value="novos"><button style="width:auto;margin:0;padding:.4rem .9rem;font-size:.8rem">Aceitar</button></form>
      </div>
    </div>
    {% endcall %}
    {% endfor %}

  {% elif fase == 'separacao' %}
    <form method="get" action="/painel/fornecedor/pedidos" style="margin:.4rem 0 .2rem">
      <input type="hidden" name="fase" value="separacao">
      <select name="periodo" onchange="this.form.submit()" style="background:var(--bg);border:1px solid #333;color:var(--txt);border-radius:8px;padding:.4rem .6rem;font-size:.85rem">
        <option value="esta_semana"{% if periodo=='esta_semana' %} selected{% endif %}>Esta semana</option>
        <option value="proxima_semana"{% if periodo=='proxima_semana' %} selected{% endif %}>Próxima semana</option>
        <option value="proxima"{% if periodo=='proxima' %} selected{% endif %}>Próximas entregas</option>
        <option value="mes"{% if periodo=='mes' %} selected{% endif %}>Próximos 30 dias</option>
      </select>
      {% if sep.data_de and sep.data_ate %}<span class="mut" style="margin-left:.5rem;font-size:.8rem">{{ sep.data_de }} — {{ sep.data_ate }}</span>{% endif %}
    </form>
    <div style="display:flex;gap:.6rem;flex-wrap:wrap;margin:.8rem 0">
      <div style="flex:1;min-width:96px;background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:.7rem;text-align:center"><div style="font-size:1.25rem;font-weight:600;color:var(--verde-claro)">{{ sep.qtd_cestas_confirmadas }}{% if sep.qtd_avulsos %}+{{ sep.qtd_avulsos }}{% endif %}</div><div class="mut" style="font-size:.7rem">cestas{% if sep.qtd_avulsos %}+avulsos{% endif %}</div></div>
      <div style="flex:1;min-width:96px;background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:.7rem;text-align:center"><div style="font-size:1.25rem;font-weight:600;color:var(--txt)">{{ sep.total_itens_distintos }}</div><div class="mut" style="font-size:.7rem">produtos</div></div>
      <div style="flex:1;min-width:96px;background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:.7rem;text-align:center"><div style="font-size:1.25rem;font-weight:600;color:var(--txt)">R$ {{ "%.0f"|format(sep.valor_total_reais) }}</div><div class="mut" style="font-size:.7rem">em cestas</div></div>
    </div>
    {% if sep.qtd_cestas_em_ajuste %}<div style="background:#2a2417;border:1px solid #6b5a2a;border-radius:10px;padding:.6rem .9rem;font-size:.82rem;color:#e0c07a;margin-bottom:.6rem">⚠️ {{ sep.qtd_cestas_em_ajuste }} cesta(s) ainda em ajuste pelos clientes — a lista pode mudar.</div>{% endif %}
    <div style="display:flex;gap:.5rem;margin:.2rem 0 .8rem">
      <button type="button" class="sep-tg" data-v="cliente" onclick="sepView(this)" style="font-size:12px;padding:6px 14px;border-radius:999px;cursor:pointer;border:1px solid var(--verde);background:var(--verde);color:#fff">Por cliente (separar)</button>
      <button type="button" class="sep-tg" data-v="total" onclick="sepView(this)" style="font-size:12px;padding:6px 14px;border-radius:999px;cursor:pointer;border:1px solid var(--borda);background:transparent;color:var(--txt-mut)">Total a comprar</button>
    </div>
    <div id="sep-cliente">
      {% if not ped_sep %}<p class="mut" style="text-align:center;padding:2rem 0">Nenhum pedido confirmado pra separar.</p>{% endif %}
      {% for p in ped_sep %}
      {% call card(p) %}
        {% if p.itens %}<div style="margin-top:.5rem;border-top:1px solid #212122;padding-top:.4rem{% if p.separada %};opacity:.55{% endif %}">{% for it in p.itens %}<div style="display:flex;justify-content:space-between;font-size:.83rem;padding:.24rem 0"><span>{{ it.produto_nome }}</span><span class="mut" style="white-space:nowrap">{{ "%g"|format(it.quantidade) }} {{ it.unidade }}</span></div>{% endfor %}</div>{% else %}<div class="mut" style="font-size:.78rem;margin-top:.4rem">sem itens</div>{% endif %}
        <div style="display:flex;justify-content:space-between;align-items:center;border-top:1px solid #212122;margin-top:.5rem;padding-top:.5rem">
          <span style="font-size:.8rem;color:{{ 'var(--verde-claro)' if p.separada else 'var(--txt-mut)' }}">{% if p.separada %}☑ separado{% else %}☐ a separar{% endif %}</span>
          <div style="display:flex;gap:.7rem;align-items:center">
            <a href="/painel/fornecedor/embalagem/{% if p.tipo=='avulso' %}avulso/{% endif %}{{ p.id }}/etiqueta" target="_blank" style="color:var(--verde-claro);font-size:.78rem;text-decoration:none">🏷️ etiqueta</a>
            <form method="post" action="/painel/fornecedor/pedidos/{{ p.tipo }}/{{ p.id }}/{{ 'desseparar' if p.separada else 'separar' }}" style="margin:0"><input type="hidden" name="fase" value="separacao"><button style="width:auto;margin:0;padding:.35rem .8rem;font-size:.78rem;{% if p.separada %}background:transparent;border:1px solid #333;color:var(--txt-mut){% else %}background:var(--verde);border:0;color:#fff{% endif %}">{% if p.separada %}desfazer{% else %}marcar separado{% endif %}</button></form>
          </div>
        </div>
      {% endcall %}
      {% endfor %}
    </div>
    <div id="sep-total" style="display:none">
      <p class="mut" style="margin:.2rem 0 .3rem;font-size:.82rem">Total a comprar — cestas + avulsos somados.</p>
      {% if not sep.grupos %}<p class="mut" style="text-align:center;padding:2rem 0">Nada confirmado.</p>{% endif %}
      {% for g in sep.grupos %}
      <div style="margin-top:1rem"><div class="mut" style="text-transform:uppercase;font-size:.72rem;letter-spacing:.04em;margin-bottom:.4rem">{{ g.grupo }}</div>
        {% for it in g.itens %}
        <div style="display:flex;justify-content:space-between;align-items:center;padding:.5rem .2rem;border-bottom:1px solid #1c1c1d">
          <span>{{ it.produto_nome }}</span>
          <span style="white-space:nowrap"><b>{{ "%g"|format(it.quantidade) }}</b> <span class="mut">{{ it.unidade }}</span>{% if it.get('suficiente') is not none %} <span style="font-size:.7rem;color:{{ 'var(--verde-claro)' if it.suficiente else '#e0a83d' }}">{{ '✓' if it.suficiente else 'falta ' ~ ("%g"|format(it.falta)) }}</span>{% endif %}</span>
        </div>
        {% endfor %}
      </div>
      {% endfor %}
    </div>
    <script>
    function sepView(b){var v=b.getAttribute('data-v');
      document.querySelectorAll('.sep-tg').forEach(function(x){x.style.background='transparent';x.style.color='var(--txt-mut)';x.style.borderColor='var(--borda)';});
      b.style.background='var(--verde)';b.style.color='#fff';b.style.borderColor='var(--verde)';
      document.getElementById('sep-cliente').style.display=(v==='cliente'?'':'none');
      document.getElementById('sep-total').style.display=(v==='total'?'':'none');
    }
    </script>

  {% elif fase == 'embalagem' %}
    <p class="mut" style="margin:.4rem 0">{{ n_falta }} a embalar de {{ itens|length }}.</p>
    {% if not itens %}<p class="mut" style="text-align:center;padding:2rem 0">Nada pra embalar.</p>{% endif %}
    {% for p in itens %}
    {% call card(p) %}
    {% if p.itens %}<details style="margin-top:.5rem"><summary style="cursor:pointer;color:var(--verde-claro);font-size:.8rem;list-style:none">ver {{ p.itens|length }} itens</summary><div style="margin-top:.4rem;padding-left:.2rem">{% for it in p.itens %}<div style="display:flex;justify-content:space-between;font-size:.82rem;padding:.28rem 0;border-bottom:1px solid #1c1c1d"><span>{{ it.produto_nome }}</span><span class="mut" style="white-space:nowrap">{{ "%g"|format(it.quantidade) }} {{ it.unidade }}</span></div>{% endfor %}</div></details>{% endif %}
    <div style="display:flex;justify-content:space-between;align-items:center;border-top:1px solid #212122;margin-top:.6rem;padding-top:.6rem">
      <span class="mut" style="font-size:.78rem">{% if p.esta_embalada %}✅ embalado {{ p.embalada_em }}{% else %}a embalar{% endif %}</span>
      <div style="display:flex;gap:.4rem">
        <a href="/painel/fornecedor/embalagem/{% if p.tipo=='avulso' %}avulso/{% endif %}{{ p.id }}/etiqueta" target="_blank" style="color:var(--verde-claro);font-size:.8rem;align-self:center;text-decoration:none">etiqueta</a>
        {% if p.esta_embalada %}
        <form method="post" action="/painel/fornecedor/pedidos/{{ p.tipo }}/{{ p.id }}/desembalar" style="margin:0"><input type="hidden" name="fase" value="embalagem"><button style="width:auto;margin:0;padding:.4rem .8rem;font-size:.8rem;background:transparent;border:1px solid #333;color:var(--txt-mut)">desfazer</button></form>
        {% else %}
        <form method="post" action="/painel/fornecedor/pedidos/{{ p.tipo }}/{{ p.id }}/embalar" style="margin:0"><input type="hidden" name="fase" value="embalagem"><button style="width:auto;margin:0;padding:.4rem .9rem;font-size:.8rem">Embalado</button></form>
        {% endif %}
      </div>
    </div>
    {% endcall %}
    {% endfor %}

  {% elif fase == 'rotas' %}
    {% if not grupos %}<p class="mut" style="text-align:center;padding:2rem 0">Nada pra rotear.</p>{% endif %}
    {% for g in grupos %}
    <div style="font-size:.85rem;color:var(--verde-claro);font-weight:600;margin:1.2rem 0 .5rem">📍 {{ g.bairro }} · {{ g.qtd }} pedido(s)</div>
    {% for p in g.pedidos %}
    {% call card(p) %}
    <div style="border-top:1px solid #212122;margin-top:.6rem;padding-top:.6rem">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span class="mut" style="font-size:.78rem">{% if not p.esta_embalada %}⚠ não embalado{% else %}pronto{% endif %}</span>
        {% if p.pago %}
        <form method="post" action="/painel/fornecedor/pedidos/{{ p.tipo }}/{{ p.id }}/entregar" style="margin:0"><input type="hidden" name="fase" value="rotas"><button style="width:auto;margin:0;padding:.4rem .9rem;font-size:.8rem">Entregue</button></form>
        {% endif %}
      </div>
      {% if not p.pago %}
      <details style="margin-top:.5rem">
        <summary style="list-style:none;cursor:pointer;background:var(--verde);color:#fff;border-radius:8px;padding:.42rem .9rem;font-size:.8rem;font-weight:500;display:inline-block">Entregue — como recebeu?</summary>
        <div style="margin-top:.5rem;background:#101011;border:1px dashed #3a5c4e;border-radius:9px;padding:.7rem">
          <div class="mut" style="font-size:.8rem;margin-bottom:.5rem">Como o cliente pagou{% if p.tipo=='avulso' %} R$ {{ (p.total_centavos/100)|n2 }}{% endif %}?</div>
          <div style="display:flex;gap:.4rem;flex-wrap:wrap">
            {% for fv, fl in [('entrega_dinheiro','💵 Dinheiro'),('entrega_cartao','💳 Cartão'),('entrega_pix','📱 Pix'),('pagar_agora','🔗 Pagou pelo link')] %}
            <form method="post" action="/painel/fornecedor/pedidos/{{ p.tipo }}/{{ p.id }}/receber" style="margin:0"><input type="hidden" name="fase" value="rotas"><input type="hidden" name="forma" value="{{ fv }}"><button style="width:auto;margin:0;padding:.35rem .7rem;font-size:.78rem;background:var(--card);border:1px solid var(--borda);color:var(--txt)">{{ fl }}</button></form>
            {% endfor %}
            <form method="post" action="/painel/fornecedor/pedidos/{{ p.tipo }}/{{ p.id }}/receber" style="margin:0"><input type="hidden" name="fase" value="rotas"><input type="hidden" name="forma" value="nao"><button style="width:auto;margin:0;padding:.35rem .7rem;font-size:.78rem;background:transparent;border:1px solid #444;color:var(--txt-mut)">Entregue, não recebi</button></form>
          </div>
        </div>
      </details>
      {% endif %}
    </div>
    {% endcall %}
    {% endfor %}
    {% endfor %}

  {% elif fase == 'entregues' %}
    {% if not entregues %}<p class="mut" style="text-align:center;padding:2rem 0">Nenhum pedido entregue ainda.</p>{% endif %}
    {% for p in entregues %}
    {% call card(p) %}
    <div style="display:flex;justify-content:space-between;align-items:center;border-top:1px solid #212122;margin-top:.6rem;padding-top:.6rem">
      <span class="mut" style="font-size:.78rem">✅ entregue{% if p.entregue_em %} {{ p.entregue_em }}{% endif %}</span>
      <form method="post" action="/painel/fornecedor/pedidos/{{ p.tipo }}/{{ p.id }}/desentregar" style="margin:0"><input type="hidden" name="fase" value="entregues"><button style="width:auto;margin:0;padding:.35rem .7rem;font-size:.78rem;background:transparent;border:1px solid #333;color:var(--txt-mut)">desfazer</button></form>
    </div>
    {% endcall %}
    {% endfor %}
  {% endif %}
</div>
<script>
function filtTipo(b){
  var t=b.getAttribute('data-tp');
  document.querySelectorAll('.tp-btn').forEach(function(x){x.style.background='transparent';x.style.color='var(--txt-mut)';x.style.borderColor='var(--borda)';});
  b.style.background='var(--verde)';b.style.color='#fff';b.style.borderColor='var(--verde)';
  document.querySelectorAll('.uni-card').forEach(function(c){
    c.style.display=(t==='todos'||c.getAttribute('data-tipo')===t)?'':'none';
  });
}
</script>
{% endblock %}"""


_FINANCEIRO_FORN = """{% extends "base" %}{% block conteudo %}
<style>
.fin-back{color:var(--verde-claro);font-size:.9rem;text-decoration:none;display:inline-block;margin-bottom:.4rem}
.fin-hero{border-radius:14px;padding:1.1rem 1.2rem;margin:.8rem 0 .7rem}
.fin-hero.pos{background:#13251d;border:1px solid #2f5d4e}
.fin-hero.neg{background:#2a1717;border:1px solid #6e2b2b}
.fin-hero-rot{font-size:.75rem;text-transform:uppercase;letter-spacing:.04em}
.fin-hero-num{font-size:2rem;font-weight:700;margin:.2rem 0}
.fin-hero-sub{font-size:.8rem;color:var(--txt-mut);margin-top:.3rem}
.fin-direto{display:flex;justify-content:space-between;align-items:center;background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem 1rem;margin-bottom:1.1rem}
.fin-prox{background:#12202a;border:1px solid #274a5e;border-radius:11px;padding:.65rem 1rem;margin-bottom:1.1rem;font-size:.9rem;color:#cfe3f0}
.fin-box{background:var(--card);border:1px solid var(--borda);border-radius:12px;padding:1rem 1.1rem;margin-bottom:1.1rem}
.fin-box-tit{font-size:.78rem;color:#6f6f6a;text-transform:uppercase;letter-spacing:.04em;margin-bottom:.6rem}
.fin-lin{display:flex;justify-content:space-between;padding:.3rem 0;font-size:.92rem}
.fin-lin.sub{font-size:.82rem;color:var(--txt-mut);padding-left:1rem}
.fin-lin.tot{border-top:1px solid var(--borda);margin-top:.3rem;padding-top:.5rem;font-weight:600}
.fin-origem{display:flex;gap:.6rem;margin-bottom:1.1rem;flex-wrap:wrap}
.fin-oc{flex:1;min-width:140px;background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:.7rem .9rem}
.fin-sec{font-size:.78rem;color:#6f6f6a;text-transform:uppercase;letter-spacing:.04em;margin:.4rem 0 .6rem}
.fin-row{display:flex;justify-content:space-between;align-items:center;background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.7rem .9rem;margin-bottom:.55rem}
.fin-badge{font-size:10px;padding:1px 7px;border-radius:999px;font-weight:600}
.b-cesta{background:#1e2b23;color:#7fd4a8}.b-avulso{background:#2b2620;color:#e0b877}
.fin-tag{font-size:10px;padding:1px 7px;border-radius:999px;border:1px solid #333;color:var(--txt-mut)}
.fin-tag.on{border-color:#3a78c2;color:#7ab0e8}
.fin-pill{font-size:10px;padding:2px 8px;border-radius:999px}
.p-on{background:#16202e;color:#7ab0e8;border:1px solid #3a78c2}
.p-dir{background:#15301f;color:var(--verde-claro);border:1px solid var(--verde)}
.p-pend{background:#2a2417;color:#e0c07a;border:1px solid #6b5a2a}
.green{color:var(--verde-claro)}.amber{color:#e0b877}.red{color:#e0857a}.mut{color:var(--txt-mut)}
</style>
<div class="card larga">
  <a href="/painel/fornecedor" class="fin-back">← voltar</a>
  <h2 style="margin:.2rem 0 .8rem">💰 Financeiro</h2>
  {% if aviso %}<div class="ok" style="margin-bottom:1rem">{{ aviso }}</div>{% endif %}
  {% if not resumo.comissao_configurada %}<div style="background:#2a2417;border:1px solid #6b5a2a;border-radius:10px;padding:.7rem .9rem;font-size:.83rem;color:#e0c07a;margin-bottom:1rem">⚠️ Comissão não configurada no cadastro deste fornecedor — o cálculo está usando 0%. Peça pro admin definir a comissão.</div>{% endif %}
  <form method="get" action="/painel/fornecedor/financeiro" style="margin-bottom:.6rem">
    <select name="periodo" onchange="this.form.submit()" style="background:var(--bg);border:1px solid #333;color:var(--txt);border-radius:8px;padding:.45rem .7rem;font-size:.9rem">
      <option value="mes"{% if resumo.periodo=='mes' %} selected{% endif %}>Este mês</option>
      <option value="mes_passado"{% if resumo.periodo=='mes_passado' %} selected{% endif %}>Mês passado</option>
      <option value="30dias"{% if resumo.periodo=='30dias' %} selected{% endif %}>Últimos 30 dias</option>
      <option value="tudo"{% if resumo.periodo=='tudo' %} selected{% endif %}>Tudo</option>
    </select>
  </form>

  {% if resumo.repasse_negativo %}
  <div class="fin-hero neg">
    <div class="fin-hero-rot" style="color:#e5a3a3">A pagar à plataforma</div>
    <div class="fin-hero-num red">R$ {{ ((0 - resumo.repasse_cent)/100)|n2 }}</div>
    <div class="fin-hero-sub">a comissão das vendas offline passou do repasse online do período</div>
  </div>
  {% else %}
  <div class="fin-hero pos">
    <div class="fin-hero-rot" style="color:#8fd3b3">A repassar (online)</div>
    <div class="fin-hero-num green">R$ {{ (resumo.repasse_cent/100)|n2 }}</div>
    <div class="fin-hero-sub">líquido do online — cai na sua wallet Asaas</div>
  </div>
  {% endif %}

  {% if not resumo.repasse_negativo %}<div class="fin-prox">🗓️ Próximo repasse: <b>{{ resumo.proximo_repasse_label }}</b> <span class="mut">· repasses semanais</span></div>{% endif %}

  <div class="fin-direto">
    <div><b>Recebido direto</b> <span class="fin-tag">na entrega</span><div class="mut" style="font-size:.76rem;margin-top:.2rem">dinheiro/cartão/Pix na entrega — já no seu caixa</div></div>
    <div class="green" style="font-weight:600;font-size:1.1rem">R$ {{ (resumo.recebido_direto_cent/100)|n2 }}</div>
  </div>

  <div class="fin-box">
    <div class="fin-box-tit">Faturamento do período</div>
    <div class="fin-lin"><span>Bruto ({{ resumo.qtd_pedidos }} pedido{% if resumo.qtd_pedidos != 1 %}s{% endif %})</span><span>R$ {{ (resumo.faturamento_cent/100)|n2 }}</span></div>
    <div class="fin-lin"><span>− Comissão da plataforma ({{ "%g"|format(resumo.comissao_pct) }}%)</span><span class="amber">− R$ {{ (resumo.comissao_cent/100)|n2 }}</span></div>
    <div class="fin-lin tot"><span>Líquido pra você</span><span class="green">R$ {{ (resumo.liquido_cent/100)|n2 }}</span></div>
    <div class="fin-lin sub"><span>├ recebido direto (na entrega)</span><span>R$ {{ (resumo.recebido_direto_cent/100)|n2 }}</span></div>
    <div class="fin-lin sub"><span>├ a repassar (online){% if resumo.repasse_negativo %} — negativo{% endif %}</span><span>R$ {{ (resumo.repasse_cent/100)|n2 }}</span></div>
    {% if resumo.pendente_cent %}<div class="fin-lin sub"><span>└ pendente (entregue, a receber)</span><span>R$ {{ (resumo.pendente_cent/100)|n2 }}</span></div>{% endif %}
  </div>

  <div class="fin-origem">
    <div class="fin-oc"><div style="font-weight:600">🧺 R$ {{ (resumo.cestas_cent/100)|n2 }}</div><div class="mut" style="font-size:.72rem">cestas · {{ resumo.cestas_qtd }} pedido{% if resumo.cestas_qtd != 1 %}s{% endif %}</div></div>
    <div class="fin-oc"><div style="font-weight:600">📦 R$ {{ (resumo.avulsos_cent/100)|n2 }}</div><div class="mut" style="font-size:.72rem">avulsos · {{ resumo.avulsos_qtd }} pedido{% if resumo.avulsos_qtd != 1 %}s{% endif %}</div></div>
  </div>

  <div class="fin-sec">Extrato de pedidos</div>
  {% if not extrato %}<p class="mut" style="text-align:center;padding:2rem 0">Nenhum pedido no período.</p>{% endif %}
  {% for e in extrato %}
  <div class="fin-row">
    <div>
      <b>{{ e.cliente_nome }}</b>
      <span class="fin-badge {{ 'b-cesta' if e.tipo=='cesta' else 'b-avulso' }}">{{ e.tipo }}</span>
      <span class="fin-tag{% if e.canal=='online' %} on{% endif %}">{{ 'online' if e.canal=='online' else 'na entrega' }}</span>
      <div class="mut" style="font-size:.76rem;margin-top:.2rem">{{ e.data }} · R$ {{ (e.valor_cent/100)|n2 }} − R$ {{ (e.comissao_cent/100)|n2 }} comissão</div>
    </div>
    <div style="text-align:right">
      <div class="{{ 'green' if e.pago else '' }}" style="font-weight:600">R$ {{ (e.liquido_cent/100)|n2 }}</div>
      {% if e.pendente and e.markable %}
      <form method="post" action="/painel/fornecedor/financeiro/{{ e.id }}/recebido" style="margin:.2rem 0 0"><input type="hidden" name="periodo" value="{{ resumo.periodo }}"><button style="width:auto;margin:0;padding:.25rem .6rem;font-size:.72rem">marcar recebido</button></form>
      {% elif e.pendente %}<span class="fin-pill p-pend">a cobrar</span>
      {% elif e.canal=='direto' %}<span class="fin-pill p-dir">recebido</span>
      {% else %}<span class="fin-pill p-on">online</span>{% endif %}
    </div>
  </div>
  {% endfor %}

  <div class="fin-sec" style="margin-top:1.4rem">Livro de repasses</div>
  <div class="fin-box">
    <div class="fin-lin"><span>Saldo a repassar (total)</span><span class="{{ 'green' if saldo_cent > 0 else 'mut' }}">R$ {{ (saldo_cent/100)|n2 }}</span></div>
    <div class="fin-lin sub"><span>total ja repassado</span><span>R$ {{ (repassado_cent/100)|n2 }}</span></div>
  </div>
  {% if not repasses %}<p class="mut" style="text-align:center;padding:1rem 0">Nenhum repasse registrado ainda. O admin registra quando paga.</p>{% endif %}
  {% for rp in repasses %}
  <div class="fin-row">
    <div><b>R$ {{ (rp.valor_cent/100)|n2 }}</b><div class="mut" style="font-size:.76rem;margin-top:.2rem">{{ rp.data }}{% if rp.obs %} · {{ rp.obs }}{% endif %}</div></div>
    <span class="fin-pill p-dir">repassado</span>
  </div>
  {% endfor %}
</div>
{% endblock %}"""


_env = Environment(loader=DictLoader({
    "base": _BASE, "cadastro": _CADASTRO, "login": _LOGIN, "bemvindo": _BEMVINDO, "painel": _PAINEL, "bloco_conta": _BLOCO_CONTA, "senha": _SENHA, "dash": _DASH, "compras": _COMPRAS, "fornecedor": _FORNECEDOR, "compra_revisar": _COMPRA_REVISAR, "loja": _LOJA, "loja_confirmar_novo": _LOJA_CONFIRMAR_NOVO, "revisar": _REVISAR, "painel_assinaturas": _PAINEL_ASSINATURAS, "meu_plano": _MEU_PLANO, "ativar_app": _ATIVAR_APP, "pagar_aviso": _PAGAR_AVISO, "cesta_ajuste": _CESTA_AJUSTE, "pedidos_forn": _PEDIDOS_FORN, "pedidos_uni": _PEDIDOS_UNI, "financeiro_forn": _FINANCEIRO_FORN, "avulsos_forn": _AVULSOS_FORN, "pedido_detalhe_forn": _PEDIDO_DETALHE_FORN, "separacao_forn": _SEPARACAO_FORN, "embalagem_forn": _EMBALAGEM_FORN, "etiqueta_forn": _ETIQUETA_FORN, "rotas_forn": _ROTAS_FORN, "esqueci_senha": _ESQUECI_SENHA, "redefinir_senha": _REDEFINIR_SENHA, "pedido_enviado": _PEDIDO_ENVIADO, "meus_pedidos": _MEUS_PEDIDOS, "promocoes_em_breve": _PROMOCOES_EM_BREVE, "empresa": _EMPRESA, "empresa_dados": _EMPRESA_DADOS, "produtos": _PRODUTOS, "abastecimento": _ABASTECIMENTO, "clientes": _CLIENTES, "cliente_detalhe": _CLIENTE_DETALHE, "pdv": _PDV, "venda_detalhe": _VENDA_DETALHE,
}), autoescape=select_autoescape())
_env.globals["brl"] = brl
_env.filters["brl"] = brl
_env.filters["n2"] = _n2
from finance.models import canonizar_categoria, categorias_de
_env.globals["canon"] = lambda c, t="despesa": canonizar_categoria(c, t)
_env.globals["categorias_de"] = categorias_de
# rotulo amigavel da forma de pagamento (canonica -> texto + icone). '' = sem info.
_FORMA_PAG_LABEL = {
    "pix": "Pix", "debito": "Débito", "credito": "Crédito",
    "especie": "Dinheiro", "boleto": "Boleto", "transferencia": "Transf.",
    "outro": "Outro",
}
_env.globals["forma_pag_label"] = lambda f: _FORMA_PAG_LABEL.get(f or "", "")
from finance import cidades as _cidades_mod
_env.globals["cidades"] = _cidades_mod.opcoes()
from finance import nichos as _nichos


def _carrinho_virtual_da_sessao(pool, fornecedor_id: int, cs: dict):
    """Monta carrinho virtual (deslogado) com preços do catálogo."""
    itens, subtotal = [], 0
    with pool.connection() as c:
        for it in cs.get("itens", []):
            p = c.execute(
                """select nome, unidade, preco_venda_centavos from catalogo_produtos
                   where id=%s and fornecedor_id=%s and ativo and disponivel""",
                (it["produto_id"], fornecedor_id),
            ).fetchone()
            if not p:
                continue
            tot = int(round(float(it.get("quantidade", 1)) * int(p[2] or 0)))
            subtotal += tot
            itens.append({
                "id": None, "produto_id": it["produto_id"], "nome": p[0],
                "unidade": p[1], "quantidade": float(it.get("quantidade", 1)),
                "preco_unit_centavos": int(p[2] or 0), "total_centavos": tot,
            })
        rf = c.execute(
            """select coalesce(taxa_entrega_centavos,0),
                      coalesce(pedido_minimo_centavos,0)
               from contas where id=%s""",
            (fornecedor_id,),
        ).fetchone()
    taxa = int(rf[0] or 0) if rf else 0
    minimo = int(rf[1] or 0) if rf else 0
    return {
        "id": None, "itens": itens, "subtotal_centavos": subtotal,
        "taxa_centavos": taxa, "total_centavos": subtotal + taxa,
        "pedido_minimo_centavos": minimo, "virtual": True,
    }


def _tem_assinatura_cesta(conta_id: int) -> bool:
    """Verifica se a conta tem assinatura de cesta ativa (pra mostrar link no menu)."""
    try:
        with get_pool().connection() as c:
            r = c.execute(
                """select 1 from assinaturas
                   where cliente_id = %s and status != 'cancelada' limit 1""",
                (conta_id,),
            ).fetchone()
        return bool(r)
    except Exception:
        return False


def _plano_aviso(conta_row, beta_ativo) -> dict | None:
    """Estado do plano pro banner do painel. None = nada a mostrar (em dia).

    NAO corta acesso — so' informa. Roda em cima da mesma verdade do
    contas.acesso_liberado: com o beta ligado o cliente continua usando, mas o
    aviso ainda aparece pra ele saber que venceu e que precisa pagar (senao
    perde o SAC e, quando o beta acabar, o proprio painel).

    Niveis:
      'vencido'  -> venceu (ou suspensa/cancelada) — banner vermelho.
      'avencer'  -> vence em ate 5 dias — banner amarelo.
    conta_row: a tupla do conta_logada ([5]=status, [6]=vencimento).
    """
    if not conta_row:
        return None
    status = conta_row[5]
    venc = conta_row[6]

    def _fmt(d):
        return d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else (str(d) if d else "")

    # suspensa/cancelada: cortado de verdade mesmo no beta (decisao explicita).
    if status in ("suspensa", "cancelada"):
        return {"nivel": "vencido", "status": status, "vencimento": venc,
                "venc_fmt": _fmt(venc), "dias": None, "beta": bool(beta_ativo),
                "cortado": True}
    if not hasattr(venc, "toordinal"):  # sem data valida -> nada a avisar
        return None
    dias = (venc - _date.today()).days
    if dias < 0:
        # Venceu por data: so' esta REALMENTE cortado se o beta estiver desligado.
        return {"nivel": "vencido", "status": status, "vencimento": venc,
                "venc_fmt": _fmt(venc), "dias": dias, "beta": bool(beta_ativo),
                "cortado": not beta_ativo}
    if dias <= 5:
        return {"nivel": "avencer", "status": status, "vencimento": venc,
                "venc_fmt": _fmt(venc), "dias": dias, "beta": bool(beta_ativo),
                "cortado": False}
    return None


def _render(nome: str, request: Request, **ctx) -> HTMLResponse:
    ctx.setdefault("logado", bool(request.session.get("conta_id")))
    ctx.setdefault("titulo", nome.capitalize())
    if "secao_ativa" not in ctx:
        _p = request.url.path
        _secs = [("caixa", "/painel/pdv"), ("abastecimento", "/painel/produtos/abastecimento"),
                 ("produtos", "/painel/produtos"), ("clientes", "/painel/clientes"),
                 ("servicos", "/painel/servicos"), ("prospeccao", "/painel/prospeccao"),
                 ("equipe", "/painel/equipe"),
                 ("financeiro", "/painel/financeiro"), ("empresa", "/painel/empresa"),
                 ("fornecedor", "/painel/fornecedor"), ("assinaturas", "/painel/assinaturas"),
                 ("pedidos", "/painel/meus-pedidos"), ("compras", "/painel/compras"),
                 ("painel", "/painel")]
        ctx["secao_ativa"] = next((k for k, pre in _secs if _p == pre or _p.startswith(pre + "/")), "")
    # Papel + capacidades do operador logado (dono/gestor/vendedor/financeiro).
    # Esconde do menu o que o papel não acessa (o gate no app.py é a trava dura).
    if "caps" not in ctx:
        from contas import equipe as _equipe
        _papel = request.session.get("papel", "dono")
        ctx.setdefault("papel", _papel)
        ctx["caps"] = _equipe.caps_do_papel(_papel)
    ctx.setdefault("n_contextos", len(request.session.get("contextos") or []))
    # Injeta conta + gating (o conta_logada ja traz tudo numa query so: conta[11..15])
    if request.session.get("conta_id"):
        if "conta" not in ctx:
            ctx["conta"] = conta_logada(request)
        _c = ctx.get("conta")
        if "tem_cesta" not in ctx:
            ctx["tem_cesta"] = bool(_c and _c[15])
        if "tem_pj" not in ctx:
            ctx["tem_pj"] = bool(_c and _c[11])
        if "vende_produto" not in ctx:
            ctx["vende_produto"] = bool(_c and _c[13])
            ctx["vende_servico"] = bool(_c and _c[14])
    if "beta_gratis" not in ctx:
        try:
            from finance import config_app as _cfg
            ctx["beta_gratis"] = _cfg.beta_gratis_ativo(get_pool())
        except Exception:
            ctx["beta_gratis"] = True
    if "plano_aviso" not in ctx:
        try:
            ctx["plano_aviso"] = _plano_aviso(ctx.get("conta"), ctx.get("beta_gratis"))
        except Exception:
            ctx["plano_aviso"] = None
    return HTMLResponse(_env.get_template(nome).render(**ctx))


# ---------- rotas ----------

@router.get("/painel/pagar")
def painel_pagar(request: Request):
    """Botao 'Pagar agora' do banner de vencimento: gera (ou reusa) o link de
    pagamento avulso do plano no Asaas e redireciona o cliente pra la'.

    Reusa a mesma peca do comando 'pagar' do bot (finance.asaas.criar_link_pagamento),
    entao Pix/boleto/cartao caem na tela hospedada do Asaas e o webhook reativa a
    conta pelo externalReference. Se a conta nao tem plano SaaS (ex: so' cesta) ou
    o Asaas falha, cai num aviso claro em vez de engolir o erro.
    """
    conta = conta_logada(request)
    if not conta:
        return RedirectResponse("/login", status_code=303)
    codigo_plano = conta[4]
    if not codigo_plano or codigo_plano == "zaq_cesta":
        return _render("pagar_aviso", request,
                       msg="Sua conta não tem um plano mensal pra pagar por aqui. "
                           "Fale com a gente no WhatsApp que a gente resolve.")
    with get_pool().connection() as c:
        prow = c.execute(
            "select nome, preco_base_centavos from planos where codigo=%s",
            (codigo_plano,),
        ).fetchone()
    if not prow:
        return _render("pagar_aviso", request,
                       msg="Não achei o preço do seu plano. Fale com a gente no "
                           "WhatsApp que a gente gera o pagamento pra você.")
    try:
        from finance.asaas import criar_link_pagamento
        link = criar_link_pagamento(
            conta_id=conta[0], nome_plano=prow[0],
            valor_reais=(prow[1] or 0) / 100.0,
        )
        return RedirectResponse(link["url"], status_code=303)
    except Exception as e:  # nao engole: mostra a verdade (mesma linha do assinaturas)
        logging.error("Erro ao gerar link de pagamento (conta %s): %s", conta[0], e)
        return _render("pagar_aviso", request,
                       msg="Não consegui gerar o pagamento agora. Tente de novo em "
                           "instantes ou fale com a gente no WhatsApp.")


@router.get("/cadastro", response_class=HTMLResponse)
def cadastro_form(request: Request):
    q = request.query_params
    return _render("cadastro", request, planos=_planos(), erro=None,
                   pre_nome=q.get("nome", ""), pre_email=q.get("email", ""),
                   pre_whatsapp=q.get("whatsapp", ""))


@router.post("/cadastro", response_class=HTMLResponse)
def cadastro_envia(request: Request, background: BackgroundTasks,
                   plano: str = Form("cesta"), nome: str = Form(""),
                   email: str = Form(...), senha: str = Form(...),
                   documento: str = Form(""), whatsapp: str = Form(...),
                   cep: str = Form(""), endereco: str = Form(""),
                   interesse: str = Form(""),
                   next: str = Form("")):
    pool = get_pool()
    email = email.strip().lower()
    # Se nome vazio, usar parte do email como nome provisório
    if not nome or not nome.strip():
        nome = email.split("@")[0].capitalize()
    zap = _normalizar_zap(whatsapp)

    # Validar plano: "cesta" é a opção grátis (assinante de cesta),
    # outros planos vêm de _planos()
    eh_cesta = (plano == "cesta")
    if not eh_cesta:
        planos_ok = {p[0]: p for p in _planos()}
        if plano not in planos_ok:
            return _render("cadastro", request, planos=_planos(), erro="Plano inválido.")
        tipo = planos_ok[plano][2]
        plano_gravar = plano
    else:
        tipo = "pf"  # assinante de cesta é pessoa física; a flag eh_assinante_cesta marca o papel
        plano_gravar = "zaq_cesta"  # plano grátis, escondido do cadastro do app

    with pool.connection() as c:
        ja = c.execute("select 1 from contas where lower(email)=%s", (email,)).fetchone()
        zap_ja = c.execute("select 1 from membros where whatsapp_id=%s", (zap,)).fetchone()
    if ja:
        return _render("cadastro", request, planos=_planos(),
                       erro="Ja existe uma conta com esse e-mail. Tente entrar.")
    if zap_ja:
        return _render("cadastro", request, planos=_planos(),
                       erro="Esse WhatsApp ja esta cadastrado em outra conta.")

    doc = "".join(ch for ch in documento if ch.isdigit()) or None
    from finance import cidades as _cid
    from finance import cep as _cep
    _info = _cep.consultar(cep)
    regiao = _info["regiao"] if _info else None

    # Preparar valores (antes da transação)
    endereco_val = (endereco or "").strip() or None
    cep_val = (cep or "").strip() or None
    limite_msg = 20 if eh_cesta else 50
    limite_cupom = 0 if eh_cesta else 5
    senha_hash_val = hash_senha(senha)

    # TUDO NUMA TRANSAÇÃO ATÔMICA: criar conta + membro de uma vez
    try:
        with pool.connection() as c:
            # 1. Criar conta (completa, com email/senha/limites)
            conta_id = ct.criar_conta(
                pool, tipo, nome.strip(), plano=plano_gravar, documento=doc,
                cidade=_cid.valida(regiao), email=email, senha_hash=senha_hash_val,
                eh_assinante_cesta=eh_cesta, endereco=endereco_val, cep=cep_val,
                limite_mensagens_dia=limite_msg, limite_cupons_dia=limite_cupom,
                conn=c  # <-- usa a transação da rota
            )
            # 2. Adicionar membro (dono) na mesma transação
            c.execute(
                """insert into membros (conta_id, nome, papel, whatsapp_id)
                   values (%s, %s, 'dono', %s)""",
                (conta_id, nome.strip(), zap),
            )
            if interesse.strip():
                c.execute("update contas set interesse_modulos=%s where id=%s",
                          (interesse.strip()[:200], conta_id))
            c.commit()
    except Exception as e:  # captura violação de unique constraint
        msg_lower = str(e).lower()
        if "email" in msg_lower or "idx_contas_email" in str(e):
            return _render("cadastro", request, planos=_planos(),
                           erro="Já existe uma conta com esse e-mail. Tente entrar.")
        if "whatsapp" in msg_lower or "membros" in msg_lower:
            return _render("cadastro", request, planos=_planos(),
                           erro="Esse WhatsApp já está cadastrado. Tente entrar.")
        raise

    # FIX 2 (Opção B): registra/atualiza lead pra o funil contar TODOS os cadastros.
    # Se a pessoa testou (lead existe), só marca virou_conta. Se cadastrou direto,
    # cria lead já como cadastro com gastos_usados=0 (não infla "testaram").
    try:
        with pool.connection() as c:
            c.execute(
                """insert into leads (canal, identificador, virou_conta, conta_id, gastos_usados)
                   values ('whatsapp', %s, true, %s, 0)
                   on conflict (canal, identificador) do update
                     set virou_conta = true, conta_id = excluded.conta_id,
                         ultimo_em = now()""",
                (zap, conta_id),
            )
            c.commit()
    except Exception:  # noqa: BLE001
        pass  # nunca quebra o cadastro por causa do funil
    codigo_dono = ct.gerar_convite_dono(pool, conta_id)  # codigo pro dono conectar Telegram
    request.session["conta_id"] = conta_id
    # email de boas-vindas (tolerante a falha - nunca quebra o cadastro)
    try:
        from finance.email_sender import enviar_boas_vindas
        background.add_task(enviar_boas_vindas, email, nome.strip(), codigo_dono)
    except Exception:  # noqa: BLE001
        pass
    # Aplicar carrinho_intencao se houver (cliente tentou adicionar item deslogado)
    intencao = request.session.pop("carrinho_intencao", None)
    if intencao:
        try:
            from finance import carrinho as car_mod
            forn_id = None
            with pool.connection() as c:
                r = c.execute(
                    "select id from contas where fornecedor_slug=%s and eh_fornecedor",
                    (intencao.get("slug"),),
                ).fetchone()
                forn_id = r[0] if r else None
            if forn_id:
                cid = car_mod.obter_ou_criar(pool, conta_id, forn_id)
                car_mod.adicionar_item(pool, cid, intencao["produto_id"],
                                      intencao.get("quantidade", 1))
                request.session["carrinho_id"] = cid
        except Exception:
            pass  # ignora erros, segue o fluxo mesmo sem carrinho
    # Se veio da loja (/f/{slug}/carrinho ou /f/{slug}), segue pra lá
    # Senão, vai pro /bem-vindo (fluxo normal do app)
    if next and next.startswith("/f/"):
        return RedirectResponse(next, status_code=303)
    return RedirectResponse("/bem-vindo", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    nxt = request.query_params.get("next")
    if nxt and nxt.startswith("/"):
        request.session["next"] = nxt
    return _render("login", request, erro=None, aviso=None)


@router.post("/login", response_class=HTMLResponse)
def login_envia(request: Request, email: str = Form(...), senha: str = Form(...)):
    pool = get_pool()
    # Identidade unificada por e-mail: a pessoa pode ter a conta dela + ser membro
    # de várias empresas. Uma senha, vários contextos.
    from contas import equipe as _equipe
    ctxs = _equipe.contextos_de_login(pool, email, senha)
    if not ctxs:
        return _render("login", request, erro="E-mail ou senha incorretos.", aviso=None)
    request.session["contextos"] = ctxs
    if len(ctxs) > 1:
        # mais de um lugar pra trabalhar — deixa escolher.
        request.session.pop("papel", None)
        request.session.pop("conta_id", None)
        return RedirectResponse("/trocar", status_code=303)
    _equipe.aplicar_contexto(request.session, ctxs[0])
    # daqui pra baixo: contexto único aplicado. Fluxo de carrinho/next só faz
    # sentido pra quem entrou na PRÓPRIA conta (cliente/dono).
    if not (ctxs[0]["papel"] == "dono" and ctxs[0]["membro_id"] is None):
        return RedirectResponse("/painel", status_code=303)
    row = (request.session["conta_id"],)   # compat com o bloco de carrinho abaixo
    # Aplicar carrinho_intencao se houver (cliente tentou adicionar item deslogado)
    intencao = request.session.pop("carrinho_intencao", None)
    if intencao:
        try:
            from finance import carrinho as car_mod
            forn_id = None
            with pool.connection() as c:
                r = c.execute(
                    "select id from contas where fornecedor_slug=%s and eh_fornecedor",
                    (intencao.get("slug"),),
                ).fetchone()
                forn_id = r[0] if r else None
            if forn_id:
                cid = car_mod.obter_ou_criar(pool, row[0], forn_id)
                car_mod.adicionar_item(pool, cid, intencao["produto_id"],
                                      intencao.get("quantidade", 1))
                request.session["carrinho_id"] = cid
                # Redireciona pra loja com carrinho, não pro painel
                return RedirectResponse(f"/f/{intencao.get('slug')}", status_code=303)
        except Exception:
            pass  # ignora erros, segue pra loja/painel mesmo sem carrinho
    next_url = request.session.pop("next", None)
    if next_url:
        if next_url.startswith("/f/") and "/carrinho/materializar" in next_url:
            # materialização do carrinho da sessão — continua
            return RedirectResponse(next_url, status_code=303)
        elif next_url.startswith("/f/"):
            return RedirectResponse(next_url, status_code=303)
    return RedirectResponse("/painel", status_code=303)


@router.get("/sair")
def sair(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/esqueci-senha", response_class=HTMLResponse)
def esqueci_senha_form(request: Request):
    return _render("esqueci_senha", request, enviado=False, erro=None)


@router.post("/esqueci-senha", response_class=HTMLResponse)
def esqueci_senha_envia(request: Request, background: BackgroundTasks,
                        email: str = Form(...)):
    import secrets
    from datetime import datetime, timedelta, timezone
    from finance.email_sender import enviar_recuperacao_senha
    pool = get_pool()
    email = email.strip().lower()
    with pool.connection() as c:
        row = c.execute("select id from contas where lower(email)=%s", (email,)).fetchone()
        if row:
            token = secrets.token_urlsafe(32)
            expira = datetime.now(timezone.utc) + timedelta(hours=1)
            c.execute("""insert into tokens_reset_senha (token, conta_id, expira_em)
                         values (%s, %s, %s)""", (token, row[0], expira))
            c.commit()
            link = f"{os.environ.get('APP_URL', 'https://app.zaq-ia.com')}/redefinir-senha?token={token}"
            background.add_task(enviar_recuperacao_senha, email, link)
    return _render("esqueci_senha", request, enviado=True, erro=None)


@router.get("/redefinir-senha", response_class=HTMLResponse)
def redefinir_senha_form(request: Request, token: str = ""):
    pool = get_pool()
    from datetime import datetime, timezone
    with pool.connection() as c:
        row = c.execute("""select conta_id from tokens_reset_senha
                           where token=%s and not usado and expira_em > now()""",
                        (token,)).fetchone()
    if not row:
        return _render("redefinir_senha", request, token=token, valido=False, erro=None)
    return _render("redefinir_senha", request, token=token, valido=True, erro=None)


@router.post("/redefinir-senha", response_class=HTMLResponse)
def redefinir_senha_envia(request: Request, token: str = Form(...),
                          senha: str = Form(...)):
    pool = get_pool()
    with pool.connection() as c:
        row = c.execute("""select conta_id from tokens_reset_senha
                           where token=%s and not usado and expira_em > now()""",
                        (token,)).fetchone()
        if not row:
            return _render("redefinir_senha", request, token=token, valido=False, erro="Link inválido ou expirado.")
        if len(senha) < 8:
            return _render("redefinir_senha", request, token=token, valido=True,
                           erro="A senha precisa de ao menos 8 caracteres.")
        c.execute("update contas set senha_hash=%s where id=%s",
                  (hash_senha(senha), row[0]))
        c.execute("update tokens_reset_senha set usado=true where token=%s", (token,))
        c.commit()
    request.session["aviso"] = "Senha redefinida! Faça login."
    return RedirectResponse("/login", status_code=303)


@router.get("/bem-vindo", response_class=HTMLResponse)
def bem_vindo(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        row = c.execute(
            "select nome, codigo_convite, telegram_id from membros "
            "where conta_id=%s and papel='dono'", (conta[0],)).fetchone()
    nome = (row[0] if row else None) or "tudo certo"
    codigo = row[1] if row else None
    ja_conectado = bool(row and row[2])
    whatsapp_bot_num = (os.environ.get("TWILIO_WHATSAPP_FROM") or "").replace("whatsapp:+", "")
    return _render("bemvindo", request, nome_pessoa=nome, codigo=codigo,
                   ja_conectado=ja_conectado, bot="clawaladdin_bot",
                   whatsapp_bot_num=whatsapp_bot_num)


# Rampa ordinal azul (validada p/ superfície escura #161617) + cinza p/ "perdido".
_FUNIL_ORDEM = ["rascunho", "enviado", "negociando", "aprovada", "fechado", "perdido"]
_FUNIL_ROTULO = {"rascunho": "Rascunho", "enviado": "Enviado",
                 "negociando": "Negociando", "aprovada": "Aprovada",
                 "fechado": "Fechada", "perdido": "Perdida"}
_FUNIL_COR = {"rascunho": "#9ec5f4", "enviado": "#6da7ec", "negociando": "#3987e5",
              "aprovada": "#256abf", "fechado": "#184f95", "perdido": "#5f5f5a"}


def _painel_dashboard(pool, conta, vende_servico=False):
    """Monta os dados do dashboard do /painel (KPIs financeiros + funil de vendas).

    Reusa os helpers do módulo Empresa (mesmos de /painel/empresa). Tolerante:
    conta sem títulos/orçamentos vira dashboard zerado, sem quebrar.

    Segmentação por nicho: o financeiro é universal p/ todo PJ, mas o funil de
    propostas só faz sentido pra quem VENDE SERVIÇO (conta[14]). Conta de produto
    puro não roda a query nem vê a pizza — `tem_funil` fica False.
    """
    from finance import empresa as emp
    hoje = _date.today()
    res = emp.resumo_titulos(pool, conta[0], dias=30)
    fluxo = emp.fluxo_projetado(pool, conta[0], semanas=4)
    dre = emp.dre_mes(pool, conta[0], hoje.year, hoje.month)

    # MRR: receita recorrente ainda em aberto.
    with pool.connection() as c:
        mrr_row = c.execute(
            "select coalesce(sum(valor_centavos),0), count(*) from titulos "
            "where conta_id=%s and tipo='receber' and recorrente and status='aberto'",
            (conta[0],)).fetchone()
    mrr_centavos = int(mrr_row[0] or 0) if mrr_row else 0

    # Funil de propostas por status — SÓ pra quem vende serviço (segmento).
    contagem = {s: 0 for s in _FUNIL_ORDEM}
    setup = {s: 0 for s in _FUNIL_ORDEM}
    mensal = {s: 0 for s in _FUNIL_ORDEM}
    if vende_servico:
        try:
            with pool.connection() as c:
                for st, n, s, m in c.execute(
                    "select status, count(*), coalesce(sum(setup_centavos),0), "
                    "coalesce(sum(mensal_centavos),0) from orcamentos "
                    "where conta_id=%s group by status", (conta[0],)).fetchall():
                    if st in contagem:
                        contagem[st] = int(n or 0)
                        setup[st] = int(s or 0)
                        mensal[st] = int(m or 0)
        except Exception:
            pass  # sem tabela orcamentos → funil vazio

    total = sum(contagem.values())
    fechadas = contagem["fechado"]
    conversao = round(100.0 * fechadas / total) if total else 0
    valor_previsto = sum(setup[s] + 12 * mensal[s]
                         for s in _FUNIL_ORDEM if s != "perdido")
    novo_recorrente = mensal["fechado"]

    # Legenda (só status presentes) + conic-gradient com fresta de 1.2° na superfície.
    ativos = [s for s in _FUNIL_ORDEM if contagem[s] > 0]
    legenda = [{"rotulo": _FUNIL_ROTULO[s], "n": contagem[s], "cor": _FUNIL_COR[s],
                "mut": s == "perdido"} for s in ativos]
    partes, acc, gap, k = [], 0.0, 1.2, len(ativos)
    for i, s in enumerate(ativos):
        span = 360.0 * contagem[s] / total
        a, b = acc, acc + span
        corte = max(a, b - gap) if k > 1 else b
        partes.append(f"{_FUNIL_COR[s]} {a:.2f}deg {corte:.2f}deg")
        if k > 1:
            partes.append(f"#161617 {corte:.2f}deg {b:.2f}deg")
        acc = b
    gradiente = "conic-gradient(" + ", ".join(partes) + ")" if partes else ""

    # Fluxo projetado → série p/ SVG (saldo hoje + 4 semanas).
    serie = [fluxo["saldo_atual_centavos"]] + [p["saldo_centavos"] for p in fluxo["pontos"]]
    lo, hi = min(serie), max(serie)
    rng = (hi - lo) or 1
    n = len(serie)
    xs = [20 + (560 * i / (n - 1)) for i in range(n)] if n > 1 else [300]
    ys = [100 - ((v - lo) / rng) * 78 for v in serie]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    area = ("M" + f"{xs[0]:.1f},{ys[0]:.1f}"
            + "".join(f" L{x:.1f},{y:.1f}" for x, y in zip(xs[1:], ys[1:]))
            + f" L{xs[-1]:.1f},112 L{xs[0]:.1f},112 Z")

    return {
        "res": res, "dre": dre, "fluxo": fluxo,
        "mrr_centavos": mrr_centavos,
        "tem_funil": bool(vende_servico),
        "funil_total": total, "funil_conversao": conversao,
        "funil_previsto": valor_previsto, "funil_recorrente": novo_recorrente,
        "funil_legenda": legenda, "funil_gradiente": gradiente,
        "fluxo_poly": poly, "fluxo_area": area,
        "fluxo_end_x": xs[-1], "fluxo_end_y": ys[-1],
    }


@router.get("/painel", response_class=HTMLResponse)
def painel(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    # Assinante PURO de cesta (sem plano de app): home é a cesta.
    # Misto (cesta + app) NÃO é redirecionado — vê o painel financeiro.
    eh_cesta_puro = conta[10] and (not conta[4] or conta[4] == 'zaq_cesta')
    if eh_cesta_puro:
        return RedirectResponse("/painel/assinaturas", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        membros = c.execute(
            "select nome, papel, whatsapp_id, ativo, id, codigo_convite, telegram_id from membros where conta_id=%s order by id",
            (conta[0],),
        ).fetchall()
    ativos, inclusos, pode_extra = _limite_membros(conta)
    pode_adicionar = pode_extra or ativos < inclusos
    # numero do WhatsApp bot (sem "whatsapp:+")
    whatsapp_from = (os.environ.get("TWILIO_WHATSAPP_FROM") or "").replace("whatsapp:+", "")
    # Dashboard de negócio: só p/ conta PJ e operador com acesso financeiro.
    from contas import equipe as _equipe
    _caps = _equipe.caps_do_papel(request.session.get("papel", "dono"))
    dash = None
    if conta[11] and _caps.get("financeiro"):
        try:
            dash = _painel_dashboard(pool, conta, vende_servico=bool(conta[14]))
        except Exception:
            dash = None  # nunca deixa o dashboard derrubar o painel
    return _render("painel", request, conta=conta, membros=membros, titulo="Painel",
                   **_dados_bloco(pool, conta, request),
                   dash=dash,
                   ativos=ativos, inclusos=inclusos, extra_pago=pode_extra,
                   pode_adicionar=pode_adicionar,
                   whatsapp_bot_num=whatsapp_from,
                   erro=request.session.pop("erro", None),
                   aviso=request.session.pop("aviso", None))


# ========== LOJA PÚBLICA (FASE 3) ==========

@router.get("/f/{slug}", response_class=HTMLResponse)
def loja_fornecedor(request: Request, slug: str):
    from finance import cestas as cestas_mod, catalogo as cat_mod, carrinho as car_mod
    pool = get_pool()
    with pool.connection() as c:
        forn = c.execute(
            """select id, nome, fornecedor_slug, taxa_entrega_centavos,
                      pedido_minimo_centavos, banner_url, banner_cor,
                      logo_url, bio, sobre, whatsapp_loja, verificado,
                      desconto_pix_pct, frete_gratis_acima_centavos
               from contas where fornecedor_slug = %s and eh_fornecedor""",
            (slug,),
        ).fetchone()
    if forn is None:
        return HTMLResponse("<h1>Loja não encontrada</h1>", status_code=404)
    end = c.execute(
        "select endereco, razao_social from contas where id = %s", (forn[0],)
    ).fetchone()
    fornecedor = {"id": forn[0], "nome": (end[1] if end and end[1] else forn[1]), "slug": forn[2],
                  "taxa_entrega_centavos": int(forn[3] or 0),
                  "pedido_minimo_centavos": int(forn[4] or 0),
                  "banner_url": forn[5], "banner_cor": forn[6],
                  "logo_url": forn[7], "bio": forn[8], "sobre": forn[9],
                  "whatsapp_loja": forn[10], "verificado": forn[11],
                  "desconto_pix_pct": float(forn[12]) if forn[12] is not None else 0,
                  "frete_gratis_acima_centavos": int(forn[13] or 0),
                  "endereco": end[0] if end else None}
    conta = conta_logada(request)
    tamanhos = cestas_mod.listar_tamanhos(pool, forn[0], so_ativos=True)
    produtos = cat_mod.listar_produtos(pool, forn[0], so_disponiveis=True)
    try:
        sazonais = [p for p in produtos if p.get("sazonal")]
    except Exception:
        sazonais = []
    mais_vendidos = []
    pedidos = []
    if conta is not None:
        try:
            pedidos = car_mod.pedidos_do_cliente(pool, conta[0], forn[0])
        except Exception:
            pedidos = []
    escolha = request.session.get("loja_escolha", {})
    escolha = {
        "tamanho_id": escolha.get("tamanho_id"),
        "frequencia": escolha.get("frequencia", "semanal"),
        "restricoes": escolha.get("restricoes", []) or [],
    }
    grupos = {}
    for p in produtos:
        cat = ((p.get("categoria") or "").strip() or "outros").lower()
        grupos.setdefault(cat, []).append(p)
    ordem = ["fruta", "legume", "verdura", "tempero", "ovos", "outros"]
    extras = [c for c in sorted(grupos) if c not in ordem]
    secoes = [(c, grupos[c]) for c in ordem if c in grupos]
    secoes += [(c, grupos[c]) for c in extras]

    carrinho = None
    carrinho_json = None
    if conta is not None:
        cid = request.session.get("carrinho_id")
        if cid:
            carrinho = car_mod.ver(pool, cid)
            if carrinho:
                carrinho_json = json.dumps({
                    "itens": [{"produto_id": i["produto_id"], "nome": i["nome"],
                               "unidade": i["unidade"], "qtd": i["quantidade"],
                               "preco_unit": i["preco_unit_centavos"],
                               "total": i["total_centavos"]} for i in carrinho["itens"]],
                    "subtotal": carrinho["subtotal_centavos"],
                    "taxa": carrinho["taxa_centavos"],
                    "total": carrinho["total_centavos"],
                    "minimo": carrinho["pedido_minimo_centavos"],
                    "virtual": False
                })
    else:
        cs = request.session.get("carrinho_sessao")
        if cs and cs.get("slug") == fornecedor["slug"] and cs.get("itens"):
            carrinho = _carrinho_virtual_da_sessao(pool, fornecedor["id"], cs)
            if carrinho:
                carrinho_json = json.dumps({
                    "itens": [{"produto_id": i["produto_id"], "nome": i["nome"],
                               "unidade": i["unidade"], "qtd": i["quantidade"],
                               "preco_unit": i["preco_unit_centavos"],
                               "total": i["total_centavos"]} for i in carrinho["itens"]],
                    "subtotal": carrinho["subtotal_centavos"],
                    "taxa": carrinho["taxa_centavos"],
                    "total": carrinho["total_centavos"],
                    "minimo": carrinho["pedido_minimo_centavos"],
                    "virtual": True
                })

    if not carrinho_json:
        carrinho_json = json.dumps({"itens": [], "subtotal": 0, "taxa": 0, "total": 0, "minimo": fornecedor.get("pedido_minimo_centavos", 0), "virtual": conta is None})

    return _render("loja", request, fornecedor=fornecedor, tamanhos=tamanhos,
                   produtos=produtos, escolha=escolha, secoes=secoes, carrinho=carrinho,
                   carrinho_json=carrinho_json, sazonais=sazonais,
                   mais_vendidos=mais_vendidos, pedidos=pedidos,
                   erro_carrinho=request.session.pop("erro_carrinho", None))


@router.post("/f/{slug}/assinar")
def loja_assinar(request: Request, slug: str,
                tamanho_id: int = Form(...),
                frequencia: str = Form("semanal"),
                restricoes: list[int] = Form(default=[])):
    request.session["loja_escolha"] = {
        "slug": slug, "tamanho_id": tamanho_id,
        "frequencia": frequencia, "restricoes": restricoes or [],
    }
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse(f"/f/{slug}/confirmar", status_code=303)
    # já logado: cria direto
    return _criar_assinatura_da_sessao(request, conta, slug)


@router.get("/f/{slug}/confirmar", response_class=HTMLResponse)
def loja_confirmar(request: Request, slug: str):
    conta = conta_logada(request)
    esc = request.session.get("loja_escolha", {})
    if esc.get("slug") != slug:
        return RedirectResponse(f"/f/{slug}", status_code=303)
    if conta is None:
        # sem conta: mostra cadastro/login
        return _render("loja_confirmar_novo", request, slug=slug, erro=None)
    # logado: cria a assinatura
    return _criar_assinatura_da_sessao(request, conta, slug)


def _criar_assinatura_da_sessao(request, conta, slug):
    from finance import assinaturas as assin_mod
    esc = request.session.get("loja_escolha")
    if not esc or esc.get("slug") != slug:
        return RedirectResponse("/painel", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        forn = c.execute(
            "select id, nome from contas where fornecedor_slug=%s and eh_fornecedor",
            (slug,)).fetchone()
    if forn is None:
        return RedirectResponse("/painel", status_code=303)
    try:
        r = assin_mod.criar_assinatura(
            pool, cliente_id=conta[0], fornecedor_id=forn[0],
            tamanho_id=esc["tamanho_id"], frequencia=esc["frequencia"],
            restricoes_produto_ids=esc.get("restricoes", []),
        )
        assinatura_id = r["assinatura_id"]
        preco_reais = r["preco_centavos"] / 100
        frequencia = esc["frequencia"]

        # Gera a subscription recorrente no Asaas (tenta; se falhar, assina mesmo assim)
        try:
            from finance import asaas
            import os

            # Pega documento da conta (CPF/CNPJ)
            with pool.connection() as c:
                doc_row = c.execute(
                    "select documento from contas where id=%s", (conta[0],)
                ).fetchone()
            documento = doc_row[0] if doc_row and doc_row[0] else None

            # Sandbox: usa CPF de teste se não tem documento
            if not documento and (os.environ.get("ASAAS_AMBIENTE", "sandbox") or "sandbox").startswith("sand"):
                documento = "24971563792"  # CPF de teste válido (sandbox only)

            # Cria ou recupera o customer no Asaas
            cust = asaas.criar_cliente(
                nome=conta[1] or f"Cliente {conta[0]}",
                email=conta[6] or None,  # conta[6] = email
                cpf_cnpj=documento,  # Asaas exige CPF/CNPJ
                telefone=None,
                conta_id=conta[0],
            )
            # Cria a subscription recorrente (ciclo semanal/mensal/quinzenal)
            tamanho_nome = esc.get("tamanho_nome", "")
            sub = asaas.criar_assinatura_recorrente(
                asaas_customer_id=cust["id"],
                valor_reais=preco_reais,
                ciclo=frequencia,
                assinatura_id=assinatura_id,
                descricao=f"Cesta {tamanho_nome} - {forn[1]}",
            )
            # Guarda o subscription_id na assinatura
            with pool.connection() as c:
                c.execute(
                    "update assinaturas set asaas_subscription_id=%s where id=%s",
                    (sub["id"], assinatura_id),
                )
                c.commit()
        except Exception as asaas_err:
            # Assina mesmo assim; o cliente paga depois (log do erro)
            import logging
            err_msg = str(asaas_err)[:200]
            logging.getLogger(__name__).warning(
                f"Asaas: erro ao criar subscription {assinatura_id}: {err_msg}")
            # Durante testes: mostra erro na tela (tirar em produção se necessário)
            request.session["erro_asaas"] = err_msg

        request.session.pop("loja_escolha", None)
        request.session["aviso"] = "✓ Assinatura criada! Em breve o pagamento."
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/assinaturas", status_code=303)


@router.get("/f/{slug}/promocoes", response_class=HTMLResponse)
def loja_promocoes(request: Request, slug: str):
    pool = get_pool()
    with pool.connection() as c:
        forn = c.execute(
            "select id, nome from contas where fornecedor_slug=%s and eh_fornecedor",
            (slug,),
        ).fetchone()
    if not forn:
        return HTMLResponse("<h1>Loja não encontrada</h1>", status_code=404)
    return _render("promocoes_em_breve", request,
                   fornecedor={"nome": forn[1], "slug": slug})


# ========== CARRINHO AVULSO (PEÇA 3) ==========

@router.post("/f/{slug}/carrinho/add")
def carrinho_add(request: Request, slug: str, dados: dict = Body(...)):
    from finance import carrinho as car_mod
    produto_id = int(dados.get("produto_id"))
    quantidade = float(dados.get("quantidade", 1))
    pool = get_pool()
    with pool.connection() as c:
        forn = c.execute(
            "select id from contas where fornecedor_slug=%s and eh_fornecedor",
            (slug,),
        ).fetchone()
    if forn is None:
        return JSONResponse({"ok": False, "erro": "fornecedor não encontrado"}, status_code=404)
    conta = conta_logada(request)
    if conta is None:
        cs = request.session.get("carrinho_sessao") or {"slug": slug, "itens": []}
        if cs.get("slug") != slug:
            cs = {"slug": slug, "itens": []}
        achou = False
        for it in cs["itens"]:
            if it["produto_id"] == produto_id:
                it["quantidade"] = float(it["quantidade"]) + float(quantidade)
                achou = True
                break
        if not achou:
            cs["itens"].append({"produto_id": produto_id, "quantidade": float(quantidade)})
        request.session["carrinho_sessao"] = cs
        car = _carrinho_virtual_da_sessao(pool, forn[0], cs)
        return JSONResponse({
            "itens": [{"produto_id": i["produto_id"], "nome": i["nome"],
                       "unidade": i["unidade"], "qtd": i["quantidade"],
                       "total": i["total_centavos"]} for i in car["itens"]],
            "subtotal": car["subtotal_centavos"], "taxa": car["taxa_centavos"],
            "total": car["total_centavos"], "minimo": car["pedido_minimo_centavos"]
        })
    cid = car_mod.obter_ou_criar(pool, conta[0], forn[0])
    car_mod.adicionar_item(pool, cid, produto_id, quantidade)
    request.session["carrinho_id"] = cid
    car = car_mod.ver(pool, cid)
    return JSONResponse({
        "itens": [{"produto_id": i["produto_id"], "nome": i["nome"], "unidade": i["unidade"],
                   "qtd": i["quantidade"], "preco_unit": i["preco_unit_centavos"],
                   "total": i["total_centavos"]} for i in car["itens"]],
        "subtotal": car["subtotal_centavos"], "taxa": car["taxa_centavos"],
        "total": car["total_centavos"], "minimo": car["pedido_minimo_centavos"], "virtual": False
    })


@router.post("/f/{slug}/carrinho/qtd")
def carrinho_qtd(request: Request, slug: str, dados: dict = Body(...)):
    from finance import carrinho as car_mod
    produto_id = int(dados.get("produto_id"))
    quantidade = float(dados.get("quantidade", 0))
    pool = get_pool()
    with pool.connection() as c:
        forn = c.execute(
            "select id from contas where fornecedor_slug=%s and eh_fornecedor",
            (slug,),
        ).fetchone()
    if forn is None:
        return JSONResponse({"erro": "fornecedor"}, status_code=404)
    conta = conta_logada(request)
    if conta is None:
        cs = request.session.get("carrinho_sessao")
        if cs and cs.get("slug") == slug:
            novos = []
            for it in cs["itens"]:
                if it["produto_id"] == produto_id:
                    if quantidade > 0:
                        it["quantidade"] = quantidade
                        novos.append(it)
                else:
                    novos.append(it)
            cs["itens"] = novos
            request.session["carrinho_sessao"] = cs
        car = _carrinho_virtual_da_sessao(pool, forn[0], cs) if cs and cs.get("itens") else None
        if car:
            return JSONResponse({
                "itens": [{"produto_id": i["produto_id"], "nome": i["nome"],
                           "unidade": i["unidade"], "qtd": i["quantidade"],
                           "total": i["total_centavos"]} for i in car["itens"]],
                "subtotal": car["subtotal_centavos"], "taxa": car["taxa_centavos"],
                "total": car["total_centavos"], "minimo": car["pedido_minimo_centavos"]
            })
        rf = None
        with pool.connection() as c:
            rf = c.execute(
                "select coalesce(taxa_entrega_centavos,0), coalesce(pedido_minimo_centavos,0) from contas where id=%s",
                (forn[0],)
            ).fetchone()
        return JSONResponse({
            "itens": [], "subtotal": 0, "taxa": int(rf[0] or 0),
            "total": 0, "minimo": int(rf[1] or 0)
        })
    cid = request.session.get("carrinho_id")
    if cid:
        car = car_mod.ver(pool, cid)
        item = next((i for i in car["itens"] if i["produto_id"] == produto_id), None) if car else None
        if item:
            car_mod.atualizar_quantidade(pool, cid, item["id"], quantidade)
        car = car_mod.ver(pool, cid)
        return JSONResponse({
            "itens": [{"produto_id": i["produto_id"], "nome": i["nome"],
                       "unidade": i["unidade"], "qtd": i["quantidade"],
                       "total": i["total_centavos"]} for i in car["itens"]],
            "subtotal": car["subtotal_centavos"], "taxa": car["taxa_centavos"],
            "total": car["total_centavos"], "minimo": car["pedido_minimo_centavos"]
        })
    return JSONResponse({
        "itens": [], "subtotal": 0, "taxa": 0, "total": 0, "minimo": 0
    })


@router.post("/f/{slug}/carrinho/limpar")
def carrinho_limpar(request: Request, slug: str):
    """Esvazia o carrinho INTEIRO em 1 chamada (sessao ou logado)."""
    from finance import carrinho as car_mod
    pool = get_pool()
    with pool.connection() as c:
        forn = c.execute(
            "select id, coalesce(taxa_entrega_centavos,0), coalesce(pedido_minimo_centavos,0) "
            "from contas where fornecedor_slug=%s and eh_fornecedor",
            (slug,),
        ).fetchone()
    if forn is None:
        return JSONResponse({"erro": "fornecedor"}, status_code=404)
    vazio = {"itens": [], "subtotal": 0, "taxa": int(forn[1] or 0),
             "total": 0, "minimo": int(forn[2] or 0)}
    conta = conta_logada(request)
    if conta is None:
        cs = request.session.get("carrinho_sessao")
        if cs and cs.get("slug") == slug:
            cs["itens"] = []
            request.session["carrinho_sessao"] = cs
        return JSONResponse(vazio)
    cid = request.session.get("carrinho_id")
    if cid:
        try:
            car_mod.esvaziar(pool, cid)
        except Exception:
            pass
    return JSONResponse(vazio)


@router.post("/f/{slug}/carrinho-sessao/item")
def carrinho_sessao_item(request: Request, slug: str,
                         produto_id: int = Form(...), quantidade: float = Form(...)):
    from finance import carrinho as car_mod
    pool = get_pool()
    with pool.connection() as c:
        forn = c.execute(
            "select id from contas where fornecedor_slug=%s and eh_fornecedor",
            (slug,),
        ).fetchone()
    if forn is None:
        return JSONResponse({"ok": False}, status_code=404)
    cs = request.session.get("carrinho_sessao")
    if cs and cs.get("slug") == slug:
        novos = []
        for it in cs["itens"]:
            if it["produto_id"] == produto_id:
                if float(quantidade) > 0:
                    it["quantidade"] = float(quantidade)
                    novos.append(it)
            else:
                novos.append(it)
        cs["itens"] = novos
        request.session["carrinho_sessao"] = cs
    car = _carrinho_virtual_da_sessao(pool, forn[0], cs or {"itens": []})
    return JSONResponse({
        "itens": car["itens"], "subtotal": car["subtotal_centavos"],
        "taxa": car["taxa_centavos"], "total": car["total_centavos"],
        "minimo": car["pedido_minimo_centavos"], "virtual": True
    })


@router.post("/f/{slug}/carrinho/item/{item_id}")
def carrinho_item(request: Request, slug: str, item_id: int,
                  quantidade: float = Form(...)):
    from finance import carrinho as car_mod
    conta = conta_logada(request)
    cid = request.session.get("carrinho_id")
    if conta and cid:
        car_mod.atualizar_quantidade(pool := get_pool(), cid, item_id, quantidade)
    return RedirectResponse(f"/f/{slug}#carrinho", status_code=303)


@router.post("/f/{slug}/carrinho/checkout")
def carrinho_checkout(request: Request, slug: str,
                      endereco: str = Form(""), obs: str = Form(""),
                      forma: str = Form(""), cep: str = Form(""),
                      bairro: str = Form("")):
    from finance import carrinho as car_mod
    formas_ok = {"entrega_pix", "entrega_cartao", "entrega_dinheiro", "pagar_agora"}
    if forma not in formas_ok:
        forma = "entrega_pix"
    conta = conta_logada(request)
    if conta is None:
        cs = request.session.get("carrinho_sessao") or {}
        cs["endereco"] = endereco
        cs["obs"] = obs
        cs["forma"] = forma
        cs["cep"] = cep
        cs["bairro"] = bairro
        request.session["carrinho_sessao"] = cs
        return _render("loja_confirmar_novo", request, slug=slug, erro=None,
                       contexto="carrinho", endereco_pre=endereco, cep_pre=cep)
    cid = request.session.get("carrinho_id")
    if not cid:
        return RedirectResponse(f"/f/{slug}", status_code=303)
    pool = get_pool()
    r = car_mod.fechar(pool, cid, endereco_entrega=endereco or None, obs=obs or None,
                       cep=cep or None, bairro=bairro or None)
    if not r["ok"]:
        if r.get("faltam_centavos"):
            request.session["erro_carrinho"] = (
                f"Faltam R$ {r['faltam_centavos']/100:.2f} pro pedido mínimo.")
        else:
            request.session["erro_carrinho"] = r.get("erro", "Não foi possível enviar.")
        return RedirectResponse(f"/f/{slug}#carrinho", status_code=303)
    try:
        car_mod.registrar_pagamento(pool, cid, forma)
    except Exception:
        pass
    cid_feito = cid
    request.session.pop("carrinho_id", None)
    if forma == "pagar_agora":
        return RedirectResponse(f"/pedido/{cid_feito}/pagar", status_code=303)
    return RedirectResponse(f"/pedido-enviado/{cid_feito}", status_code=303)


@router.get("/pedido/{carrinho_id}/pagar")
def pedido_pagar(request: Request, carrinho_id: int):
    """Gera (ou reaproveita) o link de pagamento Asaas do pedido e redireciona."""
    from finance import carrinho as car_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    car = car_mod.ver(pool, carrinho_id)
    if not car or car.get("cliente_id") != conta[0]:
        return RedirectResponse("/painel", status_code=303)
    try:
        with pool.connection() as c:
            r0 = c.execute("select pagamento_link_url from carrinhos where id=%s",
                           (carrinho_id,)).fetchone()
        if r0 and r0[0]:
            return RedirectResponse(r0[0], status_code=303)
    except Exception:
        pass  # coluna ainda sem migracao: gera link novo (degrada sem quebrar)
    try:
        from finance import asaas
        link = asaas.criar_link_pagamento(
            conta_id=f"pedido:{carrinho_id}",
            nome_plano=f"Pedido #{carrinho_id}",
            valor_reais=car["total_centavos"] / 100,
            descricao=f"Pedido #{carrinho_id} — pagamento online",
        )
        url = link.get("url")
        if url:
            car_mod.registrar_pagamento(pool, carrinho_id, "pagar_agora", url)
            return RedirectResponse(url, status_code=303)
    except Exception:
        pass
    request.session["erro_carrinho"] = (
        "Não consegui gerar o pagamento online agora — combine o pagamento na entrega.")
    return RedirectResponse(f"/pedido-enviado/{carrinho_id}", status_code=303)


@router.post("/f/{slug}/carrinho/fechar")
def carrinho_fechar(request: Request, slug: str,
                    endereco: str = Form(""), obs: str = Form("")):
    return carrinho_checkout(request, slug, endereco, obs)


@router.get("/f/{slug}/carrinho/materializar")
def carrinho_materializar(request: Request, slug: str):
    from finance import carrinho as car_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse(f"/f/{slug}", status_code=303)
    pool = get_pool()
    cs = request.session.get("carrinho_sessao")
    if not cs or cs.get("slug") != slug or not cs.get("itens"):
        return RedirectResponse(f"/f/{slug}", status_code=303)
    with pool.connection() as c:
        forn = c.execute(
            "select id from contas where fornecedor_slug=%s and eh_fornecedor",
            (slug,),
        ).fetchone()
    if not forn:
        return RedirectResponse(f"/f/{slug}", status_code=303)
    cid = car_mod.obter_ou_criar(pool, conta[0], forn[0])
    for it in cs["itens"]:
        car_mod.adicionar_item(pool, cid, it["produto_id"], it.get("quantidade", 1))
    r = car_mod.fechar(pool, cid, endereco_entrega=cs.get("endereco"),
                       obs=cs.get("obs"), cep=cs.get("cep"), bairro=cs.get("bairro"))
    request.session.pop("carrinho_sessao", None)
    if not r["ok"]:
        request.session["carrinho_id"] = cid
        if r.get("faltam_centavos"):
            request.session["erro_carrinho"] = f"Faltam R$ {r['faltam_centavos']/100:.2f} pro mínimo."
        return RedirectResponse(f"/f/{slug}#carrinho", status_code=303)
    forma = (cs.get("forma") or "").strip()
    if forma:
        try:
            car_mod.registrar_pagamento(pool, cid, forma)
        except Exception:
            pass
    if forma == "pagar_agora":
        return RedirectResponse(f"/pedido/{cid}/pagar", status_code=303)
    return RedirectResponse(f"/pedido-enviado/{cid}", status_code=303)


@router.get("/f/{slug}/carrinho/revisar", response_class=HTMLResponse)
def carrinho_revisar(request: Request, slug: str):
    from finance import carrinho as car_mod
    pool = get_pool()
    with pool.connection() as c:
        forn = c.execute(
            "select id, nome, coalesce(desconto_pix_pct,0) from contas "
            "where fornecedor_slug=%s and eh_fornecedor",
            (slug,),
        ).fetchone()
    if not forn:
        return HTMLResponse("<h1>Loja não encontrada</h1>", status_code=404)
    conta = conta_logada(request)
    if conta is None:
        # ANONIMO: mostra a revisao do carrinho de SESSAO (nao expulsa mais)
        cs = request.session.get("carrinho_sessao")
        if not cs or cs.get("slug") != slug or not cs.get("itens"):
            return RedirectResponse(f"/f/{slug}", status_code=303)
        car = _carrinho_virtual_da_sessao(pool, forn[0], cs)
        if not car or not car["itens"]:
            return RedirectResponse(f"/f/{slug}", status_code=303)
        return _render("revisar", request, slug=slug, loja=forn[1],
                       pix_pct=float(forn[2] or 0), car=car, anon=True,
                       endereco_pre=cs.get("endereco") or "",
                       obs_pre=cs.get("obs") or "")
    # LOGADO: se sobrou carrinho de sessao (encheu deslogado e entrou), migra pro banco
    cs = request.session.get("carrinho_sessao")
    if cs and cs.get("slug") == slug and cs.get("itens"):
        cid_m = car_mod.migrar_sessao(pool, conta[0], forn[0], cs["itens"])
        request.session["carrinho_id"] = cid_m
        request.session.pop("carrinho_sessao", None)
    cid = request.session.get("carrinho_id")
    if not cid:
        return RedirectResponse(f"/f/{slug}", status_code=303)
    car = car_mod.ver(pool, cid)
    if not car or not car["itens"]:
        return RedirectResponse(f"/f/{slug}", status_code=303)
    end_pre = car.get("endereco_entrega") or ""
    if not end_pre:
        with pool.connection() as c:
            r = c.execute("select coalesce(endereco,'') from contas where id=%s",
                          (conta[0],)).fetchone()
            end_pre = (r[0] or "") if r else ""
    return _render("revisar", request, slug=slug, loja=forn[1],
                   pix_pct=float(forn[2] or 0), car=car, anon=False,
                   endereco_pre=end_pre, obs_pre=car.get("obs") or "")


@router.get("/api/cep/{cep}")
def api_cep(cep: str):
    """CEP -> endereco (autopreenchimento do checkout). JSON tolerante a falha."""
    from finance import cep as _cep
    info = _cep.consultar(cep)
    if not info:
        return JSONResponse({"ok": False})
    return JSONResponse({
        "ok": True, "rua": info.get("rua", ""), "bairro": info.get("bairro", ""),
        "cidade": info["cidade"], "uf": info["uf"],
    })


@router.get("/api/geo")
def api_geo(lat: float = 0.0, lng: float = 0.0):
    """lat/lng -> endereco ("usar minha localizacao"). JSON tolerante a falha."""
    from finance import geo as _geo
    info = _geo.reverse(lat, lng)
    if not info:
        return JSONResponse({"ok": False})
    return JSONResponse({
        "ok": True, "rua": info.get("rua", ""), "bairro": info.get("bairro", ""),
        "cidade": info["cidade"], "uf": info["uf"], "cep": info.get("cep", ""),
    })


@router.get("/pedido-enviado/{carrinho_id}", response_class=HTMLResponse)
def pedido_enviado(request: Request, carrinho_id: int):
    from finance import carrinho as car_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    car = car_mod.ver(pool, carrinho_id)
    if car is None:
        return RedirectResponse("/painel", status_code=303)
    with pool.connection() as c:
        forn_slug = c.execute(
            "select fornecedor_slug from contas where id=%s",
            (car["fornecedor_id"],),
        ).fetchone()
    forn_slug = forn_slug[0] if forn_slug else ""
    return _render("pedido_enviado", request, carrinho=car, fornecedor_slug=forn_slug)


def _dados_bloco(pool, conta, request):
    """Contexto do bloco 'Meus dados + Meu endereco' (Painel e Minhas cestas)."""
    with pool.connection() as c:
        row = c.execute("select endereco, cep, email_pendente from contas where id=%s", (conta[0],)).fetchone()
        zap = c.execute(
            "select whatsapp_id from membros where conta_id=%s and papel=%s order by id limit 1",
            (conta[0], "dono")).fetchone()
    return {
        "md_nome": conta[2], "md_email": conta[3],
        "md_whatsapp": (zap[0] if zap else "") or "",
        "md_endereco": (row[0] if row else "") or "",
        "md_cep": (row[1] if row else "") or "",
        "md_email_pendente": (row[2] if row and len(row) > 2 else "") or "",
        "md_aviso": request.session.pop("md_aviso", None),
        "md_erro": request.session.pop("md_erro", None),
    }


@router.post("/painel/dados")
def painel_dados(request: Request, background: BackgroundTasks, nome: str = Form(""), whatsapp: str = Form(""), email: str = Form("")):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    nome_val = (nome or "").strip()
    if not nome_val:
        request.session["md_erro"] = "O nome nao pode ficar vazio."
        return RedirectResponse(request.headers.get("referer") or "/painel", status_code=303)
    zap_novo = _normalizar_zap(whatsapp) if whatsapp else None
    with pool.connection() as c:
        dono = c.execute(
            "select id, whatsapp_id from membros where conta_id=%s and papel=%s order by id limit 1",
            (conta[0], "dono")).fetchone()
        if zap_novo and dono and zap_novo != (dono[1] or ""):
            ja = c.execute("select 1 from membros where whatsapp_id=%s and conta_id<>%s",
                           (zap_novo, conta[0])).fetchone()
            if ja:
                request.session["md_erro"] = "Esse WhatsApp ja esta em outra conta."
                return RedirectResponse(request.headers.get("referer") or "/painel", status_code=303)
        c.execute("update contas set nome=%s where id=%s", (nome_val, conta[0]))
        if dono:
            if zap_novo and zap_novo != (dono[1] or ""):
                c.execute("update membros set nome=%s, whatsapp_id=%s where id=%s",
                          (nome_val, zap_novo, dono[0]))
            else:
                c.execute("update membros set nome=%s where id=%s", (nome_val, dono[0]))
        c.commit()
    email_val = (email or "").strip().lower()
    email_msg = ""
    if email_val and email_val != (conta[3] or "").strip().lower():
        import secrets
        from datetime import datetime, timedelta, timezone
        with pool.connection() as c:
            ja_email = c.execute("select 1 from contas where lower(email)=%s and id<>%s",
                                 (email_val, conta[0])).fetchone()
        if ja_email:
            request.session["md_erro"] = "Esse e-mail ja esta em outra conta."
            return RedirectResponse(request.headers.get("referer") or "/painel", status_code=303)
        tok = secrets.token_urlsafe(32)
        expira = datetime.now(timezone.utc) + timedelta(hours=24)
        with pool.connection() as c:
            c.execute("insert into tokens_email (token, conta_id, email_novo, expira_em) values (%s,%s,%s,%s)",
                      (tok, conta[0], email_val, expira))
            c.execute("update contas set email_pendente=%s where id=%s", (email_val, conta[0]))
            c.commit()
        link = f"{os.environ.get('APP_URL', 'https://app.zaq-ia.com')}/confirmar-email?token={tok}"
        try:
            from finance.email_sender import enviar_confirmacao_email
            background.add_task(enviar_confirmacao_email, email_val, link, nome_val)
        except Exception:  # noqa: BLE001
            pass
        email_msg = " Enviei um link pro novo e-mail; ele so vale depois de confirmar."
    if zap_novo and dono and zap_novo != (dono[1] or ""):
        try:
            ct.gerar_convite_dono(pool, conta[0])
        except Exception:  # noqa: BLE001
            pass
        request.session["md_aviso"] = "Dados salvos. WhatsApp trocado — reconecte pelo bot com o codigo." + email_msg
    else:
        request.session["md_aviso"] = "Dados salvos." + email_msg
    return RedirectResponse(request.headers.get("referer") or "/painel", status_code=303)


@router.get("/confirmar-email", response_class=HTMLResponse)
def confirmar_email(request: Request):
    from datetime import datetime, timezone
    token = (request.query_params.get("token") or "").strip()
    if not token:
        return HTMLResponse("<h1>Link invalido</h1>", status_code=400)
    pool = get_pool()
    with pool.connection() as c:
        row = c.execute(
            "select conta_id, email_novo, expira_em, usado from tokens_email where token=%s",
            (token,),
        ).fetchone()
        if not row or row[3] or row[2] < datetime.now(timezone.utc):
            return HTMLResponse("<h1>Link invalido ou expirado</h1>", status_code=400)
        conta_id, email_novo = row[0], row[1]
        ja = c.execute("select 1 from contas where lower(email)=%s and id<>%s",
                       (email_novo.lower(), conta_id)).fetchone()
        if ja:
            return HTMLResponse("<h1>Esse e-mail ja esta em uso por outra conta</h1>", status_code=400)
        c.execute(
            "update contas set email=%s, email_pendente=null, email_confirmado_em=now() where id=%s",
            (email_novo, conta_id),
        )
        c.execute("update tokens_email set usado=true where token=%s", (token,))
        c.commit()
    request.session["md_aviso"] = "E-mail confirmado!"
    if request.session.get("conta_id") == conta_id:
        return RedirectResponse("/painel", status_code=303)
    return HTMLResponse(
        "<h1 style='font-family:sans-serif'>E-mail confirmado! Ja pode entrar com o novo e-mail.</h1>")


@router.post("/pedido/{carrinho_id}/endereco")
def pedido_editar_endereco(request: Request, carrinho_id: int, endereco: str = Form("")):
    from finance import carrinho as car_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    r = car_mod.atualizar_endereco_entrega(get_pool(), carrinho_id, conta[0], endereco)
    if not r["ok"]:
        request.session["erro_pedido"] = r.get("erro", "Nao foi possivel alterar o endereco.")
    return RedirectResponse(f"/pedido-enviado/{carrinho_id}", status_code=303)


@router.post("/painel/endereco")
def painel_endereco(request: Request, endereco: str = Form(""), cep: str = Form(""), bairro: str = Form("")):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    endereco_val = (endereco or "").strip() or None
    cep_val = "".join(ch for ch in (cep or "") if ch.isdigit()) or None
    bairro_val = (bairro or "").strip() or None
    with pool.connection() as c:
        try:
            c.execute("update contas set endereco=%s, cep=%s, bairro=%s where id=%s",
                      (endereco_val, cep_val, bairro_val, conta[0]))
        except Exception:
            c.rollback()
            c.execute("update contas set endereco=%s, cep=%s where id=%s",
                      (endereco_val, cep_val, conta[0]))
        c.commit()
    request.session["md_aviso"] = "Endereco atualizado."
    return RedirectResponse(request.headers.get("referer") or "/painel", status_code=303)


@router.get("/painel/assinaturas", response_class=HTMLResponse)
def painel_assinaturas(request: Request):
    from finance import assinaturas as assin_mod
    from finance import cestas as cestas_mod
    pool = get_pool()
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    assinaturas = assin_mod.listar_assinaturas_cliente(pool, conta[0])
    # Monta dict de tamanhos disponíveis por fornecedor
    tamanhos_por_fornecedor = {}
    for a in assinaturas:
        forn_id = a["fornecedor_id"]
        if forn_id not in tamanhos_por_fornecedor:
            tamanhos_por_fornecedor[forn_id] = cestas_mod.listar_tamanhos(pool, forn_id)
    return _render("painel_assinaturas", request, conta=conta, assinaturas=assinaturas,
                   **_dados_bloco(pool, conta, request),
                   tamanhos_por_fornecedor=tamanhos_por_fornecedor,
                   erro=request.session.pop("erro", None),
                   aviso=request.session.pop("aviso", None))


@router.post("/painel/assinaturas/{assinatura_id}/trocar-tamanho", response_class=HTMLResponse)
def trocar_tamanho_assinatura(request: Request, assinatura_id: int,
                              novo_tamanho_id: int = Form(...)):
    from finance import assinaturas as assin_mod
    pool = get_pool()
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    try:
        resultado = assin_mod.trocar_tamanho(pool, conta[0], assinatura_id, novo_tamanho_id)
        if resultado["ok"]:
            preco = resultado["preco_centavos"] / 100
            request.session["aviso"] = f"✓ Tamanho alterado! Nova cesta será R$ {preco:.2f} (a partir da próxima entrega)."
        else:
            request.session["erro"] = f"Erro: {resultado.get('erro', 'tamanho inválido')}"
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/assinaturas", status_code=303)


@router.get("/painel/meu-plano", response_class=HTMLResponse)
def painel_meu_plano(request: Request):
    # "Meu plano" foi absorvido por "Minhas cestas" (trocar/pagar/pausar ja estao la).
    return RedirectResponse("/painel/assinaturas", status_code=303)


def _painel_meu_plano_legado(request: Request):
    from finance import assinaturas as assin_mod
    from finance import cestas as cestas_mod
    pool = get_pool()
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    assinaturas = assin_mod.listar_assinaturas_cliente(pool, conta[0])
    # Monta dict de tamanhos disponíveis por fornecedor
    tamanhos_por_forn = {}
    for a in assinaturas:
        fid = a["fornecedor_id"]
        if fid not in tamanhos_por_forn:
            tamanhos_por_forn[fid] = cestas_mod.listar_tamanhos(pool, fid, so_ativos=True)
    return _render("meu_plano", request, conta=conta, assinaturas=assinaturas,
                   tamanhos_por_forn=tamanhos_por_forn,
                   aviso=request.session.pop("aviso", None),
                   erro=request.session.pop("erro", None))


@router.post("/painel/meu-plano/trocar", response_class=HTMLResponse)
def painel_meu_plano_trocar(request: Request,
                            assinatura_id: int = Form(...),
                            novo_tamanho_id: int = Form(...)):
    from finance import assinaturas as assin_mod
    pool = get_pool()
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    try:
        r = assin_mod.trocar_tamanho(pool, conta[0], assinatura_id, novo_tamanho_id)
        if r["ok"]:
            request.session["aviso"] = "Plano alterado! Vale a partir da próxima entrega."
        else:
            request.session["erro"] = r.get("erro", "Não foi possível trocar.")
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/meu-plano", status_code=303)


@router.get("/painel/ativar-app", response_class=HTMLResponse)
def painel_ativar_app(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    # Planos pagos do app (não o zaq_cesta)
    planos = [p for p in _planos() if p[0] != "zaq_cesta"]
    return _render("ativar_app", request, conta=conta, planos=planos,
                   aviso=request.session.pop("aviso", None))


@router.post("/painel/ativar-app", response_class=HTMLResponse)
def painel_ativar_app_envia(request: Request, plano: str = Form(...)):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    planos_ok = {p[0]: p for p in _planos() if p[0] != "zaq_cesta"}
    if plano not in planos_ok:
        request.session["aviso"] = "Plano inválido."
        return RedirectResponse("/painel/ativar-app", status_code=303)

    p = planos_ok[plano]
    nome_plano = p[1]
    preco_cent = int(p[3] or 0)
    tipo_novo = p[2]  # 'pf' ou 'pj' — sincroniza o tipo da conta com o plano

    pool = get_pool()
    with pool.connection() as c:
        # Muda plano + TIPO + limites (status fica trial até o Asaas confirmar).
        # Sincronizar o tipo é essencial: sem isso, quem troca pra pj_base fica
        # com tipo='pf' e não vê as features PJ (ex: separação no Financeiro).
        c.execute(
            """update contas set plano=%s, tipo=%s,
                   limite_mensagens_dia=50, limite_cupons_dia=5 where id=%s""",
            (plano, tipo_novo, conta[0]),
        )
        c.commit()
    ct.registrar_evento(pool, conta[0], "upgrade_app",
                       f"plano={plano} (aguardando pagamento Asaas)")

    # Gera o link de pagamento do app (externalReference = conta_id -> webhook ativa)
    from finance import asaas
    try:
        link = asaas.criar_link_pagamento(
            conta_id=conta[0],
            nome_plano=nome_plano,
            valor_reais=preco_cent / 100,
            descricao=f"App financeiro Zaq — {nome_plano}",
        )
        url = link.get("url")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"Asaas: erro ao criar link app conta {conta[0]}: {e}")
        request.session["erro"] = f"Plano escolhido, mas não consegui gerar o pagamento: {e}"
        return RedirectResponse("/painel/meu-plano", status_code=303)

    if url:
        return RedirectResponse(url, status_code=303)
    request.session["erro"] = "Plano escolhido, mas o link de pagamento não foi gerado. Tente de novo."
    return RedirectResponse("/painel/meu-plano", status_code=303)


@router.get("/painel/assinaturas/{assinatura_id}/pagar")
def painel_assinatura_pagar(request: Request, assinatura_id: int):
    """Garante a cobrança no Asaas (cria se faltar) e leva o cliente pro link."""
    from finance import assinaturas as assin_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    r = assin_mod.garantir_link_pagamento(get_pool(), conta[0], assinatura_id)
    if r.get("ok") and r.get("url"):
        return RedirectResponse(r["url"], status_code=303)
    if r.get("ok") and r.get("ja_pago"):
        request.session["aviso"] = "Pagamento confirmado! Sua assinatura está ativa. 🧺"
    elif r.get("ok") and r.get("pendente"):
        request.session["erro"] = "Cobrança sendo gerada no Asaas. Tente de novo em alguns segundos."
    else:
        request.session["erro"] = r.get("erro", "Não foi possível gerar o link de pagamento.")
    return RedirectResponse("/painel/assinaturas", status_code=303)


@router.post("/painel/assinaturas/{assinatura_id}/status")
def painel_assinatura_status(request: Request, assinatura_id: int,
                            novo_status: str = Form(...)):
    from finance import assinaturas as assin_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    try:
        assin_mod.alterar_status(get_pool(), conta[0], assinatura_id, novo_status)
        request.session["aviso"] = f"Assinatura {novo_status}."
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/assinaturas", status_code=303)


# ===== FASE 6: Janela semanal (ajuste da cesta) =====
@router.get("/cesta/{cesta_id}", response_class=HTMLResponse)
def ver_cesta(request: Request, cesta_id: int):
    from finance import janela
    conta = conta_logada(request)
    if conta is None:
        request.session["next"] = f"/cesta/{cesta_id}"
        return RedirectResponse("/login", status_code=303)
    cesta = janela.obter_cesta(get_pool(), cesta_id, cliente_id=conta[0])
    if cesta is None:
        return HTMLResponse("<h1>Cesta não encontrada</h1>", status_code=404)
    return _render("cesta_ajuste", request, cesta=cesta,
                   aviso=request.session.pop("aviso", None),
                   erro=request.session.pop("erro", None))


@router.post("/cesta/{cesta_id}/remover")
def cesta_remover_item(request: Request, cesta_id: int, item_id: int = Form(...)):
    from finance import janela
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    janela.remover_item(get_pool(), cesta_id, item_id, conta[0])
    request.session["aviso"] = "Item removido da sua cesta."
    return RedirectResponse(f"/cesta/{cesta_id}", status_code=303)


@router.post("/cesta/{cesta_id}/confirmar")
def cesta_confirmar(request: Request, cesta_id: int):
    from finance import janela
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    janela.confirmar_cesta(get_pool(), cesta_id, conta[0])
    request.session["aviso"] = "Cesta confirmada! 🧺"
    return RedirectResponse(f"/cesta/{cesta_id}", status_code=303)


# ===== Botões de teste (manual) — abrir/fechar janela =====
@router.post("/painel/teste/abrir-janela")
def teste_abrir_janela(request: Request, assinatura_id: int = Form(...)):
    from finance import janela
    conta = conta_logada(request)
    if conta is None or not conta[8]:  # eh_fornecedor
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        r = janela.abrir_janela_semana(get_pool(), apenas_assinatura_id=assinatura_id)
        msg = f"✓ Montadas {r['montadas']} cestas"
        if r['erros']:
            msg += f" | ⚠️ {len(r['erros'])} erros"
        request.session["aviso"] = msg
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.post("/painel/teste/fechar-janela")
def teste_fechar_janela(request: Request, data_entrega: str = Form(...)):
    from finance import janela
    from datetime import datetime
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        data = datetime.strptime(data_entrega, "%Y-%m-%d").date()
        r = janela.fechar_janela(get_pool(), data)
        msg = f"✓ Confirmadas {r['confirmadas']} cestas (modo confiança)"
        request.session["aviso"] = msg
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.get("/painel/fornecedor", response_class=HTMLResponse)
def painel_fornecedor(request: Request):
    from finance import catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:  # eh_fornecedor
        return RedirectResponse("/painel", status_code=303)
    pool = get_pool()
    # Uma conexao so: dados da conta (fiscal + identidade, mesma tabela) + compras.
    with pool.connection() as c:
        crow = c.execute(
            """select razao_social, documento, endereco,
                      margem_alvo_pct, logo_url, banner_url, banner_cor, bio, sobre,
                      whatsapp_loja, desconto_pix_pct, frete_gratis_acima_centavos
                 from contas where id=%s""",
            (conta[0],),
        ).fetchone()
        compras_rows = c.execute(
            """select id, data_compra, total_centavos, fonte, status,
                      (select nome from origem_compra where id = compras_fornecedor.origem_id) as origem_nome
               from compras_fornecedor where fornecedor_id=%s order by criado_em desc limit 20""",
            (conta[0],),
        ).fetchall()
    fiscal = {
        "razao_social": crow[0] if crow else None,
        "cnpj": crow[1] if crow else None,
        "endereco": crow[2] if crow else None,
    } if crow else {"razao_social": None, "cnpj": None, "endereco": None}
    if crow:
        margem_alvo = float(crow[3]) if crow[3] else 60.0
        identidade = {
            "logo_url": crow[4],
            "banner_url": crow[5],
            "banner_cor": crow[6],
            "bio": crow[7],
            "sobre": crow[8],
            "whatsapp_loja": crow[9],
            "desconto_pix_pct": float(crow[10]) if crow[10] is not None else 0,
            "frete_gratis_acima_centavos": int(crow[11] or 0),
        }
    else:
        margem_alvo = 60.0
        identidade = {}
    compras_raw = [{"id": r[0], "data_compra": r[1], "total_centavos": r[2], "fonte": r[3], "status": r[4], "origem_nome": r[5]} for r in compras_rows]
    produtos = cat_mod.listar_produtos(pool, conta[0])
    from finance import categorias as cat_sug
    categorias_padrao = cat_sug.CATEGORIAS_PADRAO
    _usadas = { (p.get("categoria") or "").strip().lower()
                for p in produtos if (p.get("categoria") or "").strip() }
    categorias_usadas = sorted(_usadas - set(categorias_padrao))
    origens = cat_mod.listar_origens(pool, conta[0])
    from finance import cestas as cestas_mod
    tamanhos = cestas_mod.listar_tamanhos(pool, conta[0], so_ativos=False)
    return _render("fornecedor", request, conta=conta, fiscal=fiscal, identidade=identidade, produtos=produtos, origens=origens, compras=compras_raw, tamanhos=tamanhos, margem_alvo=margem_alvo,
                   categorias_padrao=categorias_padrao, categorias_usadas=categorias_usadas,
                   erro=request.session.pop("erro", None),
                   aviso=request.session.pop("aviso", None))


@router.get("/painel/fornecedor/financeiro", response_class=HTMLResponse)
def painel_fornecedor_financeiro(request: Request, periodo: str = "mes"):
    from finance import financeiro_forn as fin
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:
        return RedirectResponse("/painel", status_code=303)
    pool = get_pool()
    fid = conta[0]
    resumo = fin.resumo_financeiro(pool, fid, periodo)
    extrato = fin.extrato_financeiro(pool, fid, periodo)
    saldo_cent = fin.saldo_a_repassar(pool, fid)
    repassado_cent = fin.total_repassado(pool, fid)
    repasses = fin.listar_repasses(pool, fid)
    return _render("financeiro_forn", request, conta=conta,
                   resumo=resumo, extrato=extrato,
                   saldo_cent=saldo_cent, repassado_cent=repassado_cent,
                   repasses=repasses,
                   aviso=request.session.pop("aviso_fin", None))


@router.post("/painel/fornecedor/financeiro/{carrinho_id}/recebido")
def painel_fornecedor_marcar_recebido(request: Request, carrinho_id: int,
                                      periodo: str = Form("mes")):
    from finance import carrinho as car_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:
        return RedirectResponse("/painel", status_code=303)
    ok = car_mod.registrar_recebimento(get_pool(), conta[0], carrinho_id, "", True)
    request.session["aviso_fin"] = ("Recebimento confirmado."
                                    if ok else "Nao foi possivel confirmar.")
    return RedirectResponse(f"/painel/fornecedor/financeiro?periodo={periodo}",
                            status_code=303)


@router.get("/painel/fornecedor/avulsos", response_class=HTMLResponse)
def painel_fornecedor_avulsos(request: Request, status: str = ""):
    return RedirectResponse("/painel/fornecedor/pedidos?fase=novos", status_code=303)  # unificado
    from finance import carrinho as car_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:
        return RedirectResponse("/painel", status_code=303)
    st = status if status in ("aguardando", "confirmado", "em_entrega",
                              "entregue", "cancelado") else None
    pedidos = car_mod.listar_para_fornecedor(get_pool(), conta[0], st)
    return _render("avulsos_forn", request, titulo="Pedidos avulsos", conta=conta,
                   pedidos=pedidos, status=status,
                   aviso=request.session.pop("aviso_avulso", None))


@router.post("/painel/fornecedor/avulsos/{carrinho_id}/status")
def painel_fornecedor_avulso_status(request: Request, carrinho_id: int,
                                    novo: str = Form("")):
    from finance import carrinho as car_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:
        return RedirectResponse("/painel", status_code=303)
    r = car_mod.mudar_status(get_pool(), conta[0], carrinho_id, novo)
    request.session["aviso_avulso"] = (
        "Pedido atualizado." if r.get("ok") else r.get("erro", "Nao foi possivel atualizar."))
    return RedirectResponse(
        request.headers.get("referer") or "/painel/fornecedor/avulsos", status_code=303)


_FASES_PED = ("novos", "separacao", "embalagem", "rotas", "entregues")


@router.get("/painel/fornecedor/pedidos", response_class=HTMLResponse)
def painel_fornecedor_pedidos(request: Request, fase: str = "",
                              periodo: str = "proxima_semana"):
    from finance import pedidos as pedidos_mod
    from finance import carrinho as car_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:
        return RedirectResponse("/painel", status_code=303)
    pool = get_pool()
    fid = conta[0]
    fase = (fase or "novos").strip()
    if fase not in _FASES_PED:
        fase = "novos"
    per = (periodo or "proxima_semana").strip() or "proxima_semana"

    novos_lst = car_mod.avulsos_novos(pool, fid)
    ctx = {
        "fase": fase, "periodo": per, "n_novos": len(novos_lst),
        "erro": request.session.pop("erro_ped", None),
        "aviso": request.session.pop("aviso_ped", None),
    }

    if fase == "novos":
        ctx["novos"] = novos_lst

    elif fase == "separacao":
        sep = pedidos_mod.consolidar_separacao(pool, fid, periodo=per)
        idx = {}
        for g in sep["grupos"]:
            for it in g["itens"]:
                idx[(g["grupo"], it["produto_nome"], it["unidade"])] = it
        for a in car_mod.avulsos_itens_separacao(pool, fid):
            key = (a["grupo"], a["produto_nome"], a["unidade"])
            if key in idx:
                idx[key]["quantidade"] = round(
                    float(idx[key]["quantidade"]) + a["quantidade"], 3)
            else:
                grp = next((g for g in sep["grupos"] if g["grupo"] == a["grupo"]), None)
                if grp is None:
                    grp = {"grupo": a["grupo"], "itens": []}
                    sep["grupos"].append(grp)
                novo = {"produto_nome": a["produto_nome"], "unidade": a["unidade"],
                        "quantidade": a["quantidade"], "grupo": a["grupo"]}
                grp["itens"].append(novo)
                idx[key] = novo
        _ord = {"fruta": 0, "legume": 1, "verdura": 2, "tempero": 3, "outros": 4}
        sep["grupos"].sort(key=lambda g: _ord.get(g["grupo"], 9))
        for g in sep["grupos"]:
            g["itens"].sort(key=lambda x: x["produto_nome"])
        sep["total_itens_distintos"] = sum(len(g["itens"]) for g in sep["grupos"])
        _ped = pedidos_mod.listar_para_embalagem(pool, fid, periodo=per)["cestas"]
        for _x in _ped:
            _x["tipo"] = "cesta"
            _x["qtd_itens"] = len(_x.get("itens") or [])
        _av = car_mod.avulsos_para_embalagem(pool, fid)
        _ped_sep = _ped + _av
        _ped_sep.sort(key=lambda x: (x.get("cliente_nome") or ""))
        sep["qtd_avulsos"] = len(_av)
        _sc = pedidos_mod.separadas_cesta(pool, fid, [x["id"] for x in _ped])
        _sa = car_mod.separadas_avulso(pool, fid, [x["id"] for x in _av])
        for _x in _ped_sep:
            _x["separada"] = (_x["id"] in _sc) if _x["tipo"] == "cesta" else (_x["id"] in _sa)
        sep["qtd_separadas"] = sum(1 for _x in _ped_sep if _x.get("separada"))
        ctx["sep"] = sep
        ctx["ped_sep"] = _ped_sep

    elif fase == "embalagem":
        cestas = pedidos_mod.listar_para_embalagem(pool, fid, periodo=per)["cestas"]
        for x in cestas:
            x["tipo"] = "cesta"
            x["qtd_itens"] = len(x.get("itens") or [])
            x["esta_entregue"] = False
        av = car_mod.avulsos_para_embalagem(pool, fid)
        itens = cestas + av
        itens.sort(key=lambda x: (x["esta_embalada"], (x["cliente_nome"] or "")))
        ctx["itens"] = itens
        ctx["n_falta"] = sum(1 for x in itens if not x["esta_embalada"])

    elif fase == "rotas":
        dados = pedidos_mod.listar_para_rotas(pool, fid, periodo=per)
        buckets = {}
        for g in dados["grupos"]:
            for cst in g["cestas"]:
                if cst.get("esta_entregue"):
                    continue
                cst["tipo"] = "cesta"
                cst["pago"] = True
                buckets.setdefault(g["bairro"], []).append(cst)
        _avs = [a for a in car_mod.avulsos_para_rotas(pool, fid)
                if not a.get("esta_entregue")]
        _info = car_mod.avulsos_pago_info(pool, fid, [a["id"] for a in _avs])
        for a in _avs:
            a["pago"] = _info.get(a["id"], {}).get("pago", False)
            buckets.setdefault(a["bairro"] or "(sem bairro)", []).append(a)
        bairros = sorted(k for k in buckets if k != "(sem bairro)")
        if "(sem bairro)" in buckets:
            bairros.append("(sem bairro)")
        ctx["grupos"] = [{"bairro": b, "qtd": len(buckets[b]), "pedidos": buckets[b]}
                         for b in bairros]

    elif fase == "entregues":
        ent = []
        dados = pedidos_mod.listar_para_rotas(pool, fid, periodo=per)
        for g in dados["grupos"]:
            for cst in g["cestas"]:
                if cst.get("esta_entregue"):
                    cst["tipo"] = "cesta"
                    ent.append(cst)
        for a in car_mod.avulsos_para_rotas(pool, fid):
            if a.get("esta_entregue"):
                ent.append(a)
        ent.sort(key=lambda x: (x.get("entregue_em") or ""), reverse=True)
        ctx["entregues"] = ent

    return _render("pedidos_uni", request, conta=conta, **ctx)


@router.post("/painel/fornecedor/pedidos/{tipo}/{item_id}/{acao}")
def painel_fornecedor_pedido_acao(request: Request, tipo: str, item_id: int,
                                  acao: str, fase: str = Form("novos"),
                                  forma: str = Form("")):
    from finance import pedidos as pedidos_mod
    from finance import carrinho as car_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:
        return RedirectResponse("/painel", status_code=303)
    pool = get_pool()
    fid = conta[0]
    ok = True
    if tipo == "cesta":
        if acao == "embalar":
            ok = pedidos_mod.marcar_embalada(pool, fid, item_id)
        elif acao == "desembalar":
            pedidos_mod.desmarcar_embalada(pool, fid, item_id)
        elif acao == "separar":
            ok = pedidos_mod.marcar_separada(pool, fid, item_id)
        elif acao == "desseparar":
            pedidos_mod.desmarcar_separada(pool, fid, item_id)
        elif acao == "entregar":
            ok = pedidos_mod.marcar_entregue(pool, fid, item_id)
        elif acao == "desentregar":
            pedidos_mod.desmarcar_entregue(pool, fid, item_id)
    elif tipo == "avulso":
        if acao == "aceitar":
            ok = car_mod.mudar_status(pool, fid, item_id, "confirmado").get("ok", False)
        elif acao == "recusar":
            ok = car_mod.mudar_status(pool, fid, item_id, "cancelado").get("ok", False)
        elif acao == "embalar":
            ok = car_mod.marcar_embalada(pool, fid, item_id)
        elif acao == "desembalar":
            car_mod.desmarcar_embalada(pool, fid, item_id)
        elif acao == "separar":
            ok = car_mod.marcar_separada(pool, fid, item_id)
        elif acao == "desseparar":
            ok = car_mod.desmarcar_separada(pool, fid, item_id)
        elif acao == "entregar":
            ok = car_mod.mudar_status(pool, fid, item_id, "entregue").get("ok", False)
        elif acao == "receber":
            _recebido = forma != "nao"
            ok = car_mod.registrar_recebimento(
                pool, fid, item_id, forma if _recebido else "", _recebido)
        elif acao == "desentregar":
            car_mod.mudar_status(pool, fid, item_id, "confirmado").get("ok", False)
    if not ok:
        request.session["erro_ped"] = "Nao foi possivel atualizar o pedido."
    else:
        request.session["aviso_ped"] = "Pedido atualizado."
    if fase not in _FASES_PED:
        fase = "novos"
    return RedirectResponse(f"/painel/fornecedor/pedidos?fase={fase}", status_code=303)


@router.get("/painel/fornecedor/pedidos/{pedido_id}", response_class=HTMLResponse)
def painel_fornecedor_pedido_detalhe(request: Request, pedido_id: int):
    from finance import pedidos as pedidos_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:
        return RedirectResponse("/painel", status_code=303)
    p = pedidos_mod.detalhe_pedido(get_pool(), conta[0], pedido_id)
    if p is None:
        request.session["erro"] = "Pedido não encontrado."
        return RedirectResponse("/painel/fornecedor/pedidos", status_code=303)
    return _render("pedido_detalhe_forn", request, conta=conta, p=p)


@router.get("/painel/fornecedor/separacao", response_class=HTMLResponse)
def painel_fornecedor_separacao(request: Request, periodo: str = "proxima_semana"):
    from finance import pedidos as pedidos_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:  # eh_fornecedor
        return RedirectResponse("/painel", status_code=303)
    return RedirectResponse("/painel/fornecedor/pedidos?fase=separacao", status_code=303)


@router.get("/painel/fornecedor/embalagem", response_class=HTMLResponse)
def painel_fornecedor_embalagem(request: Request, periodo: str = "proxima_semana"):
    from finance import pedidos as pedidos_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:  # eh_fornecedor
        return RedirectResponse("/painel", status_code=303)
    return RedirectResponse("/painel/fornecedor/pedidos?fase=embalagem", status_code=303)


@router.post("/painel/fornecedor/embalagem/{cesta_id}/marcar")
def painel_fornecedor_marcar_embalada(request: Request, cesta_id: int,
                                       periodo: str = Form("proxima_semana")):
    from finance import pedidos as pedidos_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:
        return RedirectResponse("/painel", status_code=303)
    ok = pedidos_mod.marcar_embalada(get_pool(), conta[0], cesta_id)
    if not ok:
        request.session["erro"] = "Cesta não encontrada ou não está confirmada."
    return RedirectResponse(
        f"/painel/fornecedor/embalagem?periodo={periodo}", status_code=303,
    )


@router.post("/painel/fornecedor/embalagem/{cesta_id}/desmarcar")
def painel_fornecedor_desmarcar_embalada(request: Request, cesta_id: int,
                                          periodo: str = Form("proxima_semana")):
    from finance import pedidos as pedidos_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:
        return RedirectResponse("/painel", status_code=303)
    pedidos_mod.desmarcar_embalada(get_pool(), conta[0], cesta_id)
    return RedirectResponse(
        f"/painel/fornecedor/embalagem?periodo={periodo}", status_code=303,
    )


@router.get("/painel/fornecedor/embalagem/{cesta_id}/etiqueta", response_class=HTMLResponse)
def painel_fornecedor_etiqueta(request: Request, cesta_id: int):
    from finance import pedidos as pedidos_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:
        return RedirectResponse("/painel", status_code=303)
    e = pedidos_mod.dados_etiqueta(get_pool(), conta[0], cesta_id)
    if e is None:
        request.session["erro"] = "Cesta não encontrada."
        return RedirectResponse("/painel/fornecedor/embalagem", status_code=303)
    e["itens"] = pedidos_mod.itens_da_cesta(get_pool(), conta[0], cesta_id)
    return HTMLResponse(_env.get_template("etiqueta_forn").render(e=e))


@router.get("/painel/fornecedor/embalagem/avulso/{carrinho_id}/etiqueta", response_class=HTMLResponse)
def painel_fornecedor_etiqueta_avulso(request: Request, carrinho_id: int):
    from finance import carrinho as car_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:
        return RedirectResponse("/painel", status_code=303)
    e = car_mod.dados_etiqueta_avulso(get_pool(), conta[0], carrinho_id)
    if e is None:
        request.session["erro"] = "Pedido nao encontrado."
        return RedirectResponse("/painel/fornecedor/pedidos?fase=embalagem", status_code=303)
    e["itens"] = car_mod.itens_do_avulso(get_pool(), conta[0], carrinho_id)
    return HTMLResponse(_env.get_template("etiqueta_forn").render(e=e))


@router.get("/painel/fornecedor/rotas", response_class=HTMLResponse)
def painel_fornecedor_rotas(request: Request, periodo: str = "proxima_semana"):
    from finance import pedidos as pedidos_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:  # eh_fornecedor
        return RedirectResponse("/painel", status_code=303)
    return RedirectResponse("/painel/fornecedor/pedidos?fase=rotas", status_code=303)


@router.post("/painel/fornecedor/rotas/{cesta_id}/entregar")
def painel_fornecedor_marcar_entregue(request: Request, cesta_id: int,
                                       periodo: str = Form("proxima_semana")):
    from finance import pedidos as pedidos_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:
        return RedirectResponse("/painel", status_code=303)
    ok = pedidos_mod.marcar_entregue(get_pool(), conta[0], cesta_id)
    if not ok:
        request.session["erro"] = "Cesta não encontrada ou status inválido."
    return RedirectResponse(
        f"/painel/fornecedor/rotas?periodo={periodo}", status_code=303,
    )


@router.post("/painel/fornecedor/rotas/{cesta_id}/desmarcar")
def painel_fornecedor_desmarcar_entregue(request: Request, cesta_id: int,
                                          periodo: str = Form("proxima_semana")):
    from finance import pedidos as pedidos_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:
        return RedirectResponse("/painel", status_code=303)
    pedidos_mod.desmarcar_entregue(get_pool(), conta[0], cesta_id)
    return RedirectResponse(
        f"/painel/fornecedor/rotas?periodo={periodo}", status_code=303,
    )


@router.post("/painel/fornecedor/margem-alvo")
def salvar_margem_alvo(request: Request, margem_alvo: str = Form("60")):
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        s = margem_alvo.replace("%", "").strip()
        valor = float(s)
        if valor < 0 or valor > 100:
            valor = 60.0
    except ValueError:
        valor = 60.0
    with get_pool().connection() as c:
        c.execute(
            "update contas set margem_alvo_pct = %s where id = %s",
            (valor, conta[0]),
        )
        c.commit()
    request.session["aviso"] = f"Margem alvo salva: {valor}%"
    return RedirectResponse("/painel/empresa/dados", status_code=303)


@router.post("/painel/fornecedor/montar-cesta")
def montar_cesta_teste(request: Request, assinatura_id: int = Form(...)):
    from finance import montador
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        r = montador.montar_cesta(get_pool(), assinatura_id, persistir=True)
        msg = f"✓ Cesta montada: {len(r['itens'])} itens, custo R$ {r['custo_total_centavos']/100:.2f}"
        if r['avisos']:
            msg += " | ⚠️ " + "; ".join(r['avisos'])
        request.session["aviso"] = msg
    except Exception as e:
        request.session["erro"] = f"Erro ao montar cesta: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.post("/painel/fornecedor/logo")
async def salvar_logo_fornecedor(request: Request, arquivo: UploadFile = File(...)):
    from finance import upload_foto
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    try:
        conteudo = await arquivo.read()
        url = upload_foto.subir_foto(conteudo, arquivo.filename or "",
                                     arquivo.content_type or "image/jpeg",
                                     aspecto=1.0)
    except ValueError as e:
        return JSONResponse({"erro": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"erro": f"Erro: {str(e)}"}, status_code=500)
    with get_pool().connection() as c:
        c.execute("update contas set logo_url=%s where id=%s", (url, conta[0]))
        c.commit()
    return JSONResponse({"ok": True, "logo_url": url})


@router.post("/painel/fornecedor/banner")
async def salvar_banner_fornecedor(request: Request, arquivo: UploadFile = File(...)):
    from finance import upload_foto
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    try:
        conteudo = await arquivo.read()
        url = upload_foto.subir_foto(conteudo, arquivo.filename or "",
                                     arquivo.content_type or "image/jpeg",
                                     aspecto=7.0)
    except ValueError as e:
        return JSONResponse({"erro": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"erro": f"Erro: {str(e)}"}, status_code=500)
    with get_pool().connection() as c:
        c.execute("update contas set banner_url=%s where id=%s", (url, conta[0]))
        c.commit()
    return JSONResponse({"ok": True, "banner_url": url})


@router.post("/painel/fornecedor/cestas/criar")
def painel_cestas_criar(request: Request,
                       nome: str = Form(...),
                       preco: str = Form("0"),
                       qtd_frutas: int = Form(0),
                       qtd_legumes: int = Form(0),
                       qtd_verduras: int = Form(0),
                       qtd_temperos: int = Form(0),
                       descricao: str = Form("")):
    from finance import cestas as cestas_mod
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    s = preco.replace("R$", "").strip()
    s = s.replace(".", "").replace(",", ".") if "," in s else s
    try:
        preco_cent = int(round(float(s) * 100))
    except ValueError:
        preco_cent = 0
    try:
        cestas_mod.criar_tamanho(get_pool(), conta[0], nome, preco_cent,
                                 qtd_frutas, qtd_legumes, qtd_verduras, qtd_temperos,
                                 descricao or None)
        request.session["aviso"] = "Tamanho de cesta criado."
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.get("/static/modelo_planilha_produtos.csv")
def modelo_planilha():
    from fastapi.responses import FileResponse
    import os
    arquivo = os.path.join(os.path.dirname(__file__), "..", "db", "modelos", "modelo_planilha_produtos.csv")
    return FileResponse(arquivo, media_type="text/csv", filename="modelo_planilha_produtos.csv")


@router.post("/assinar")
def assinar(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        plano_row = c.execute(
            "select codigo, nome, preco_base_centavos from planos where codigo=%s",
            (conta[4],),
        ).fetchone()
    if not plano_row:
        request.session["erro"] = "Plano não encontrado"
        return RedirectResponse("/painel", status_code=303)
    try:
        from finance.asaas import criar_link_pagamento
        valor_reais = plano_row[2] / 100.0
        link_data = criar_link_pagamento(conta_id=conta[0], nome_plano=plano_row[1],
                                         valor_reais=valor_reais)
        return RedirectResponse(link_data["url"], status_code=303)
    except Exception as e:
        log.error(f"Erro ao criar link Asaas: {e}")
        request.session["erro"] = "Erro ao gerar link de pagamento. Tente novamente."
        return RedirectResponse("/painel", status_code=303)


@router.get("/painel/meus-pedidos", response_class=HTMLResponse)
def meus_pedidos(request: Request):
    from finance import carrinho as car_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pedidos = car_mod.listar_do_cliente(get_pool(), conta[0])
    return _render("meus_pedidos", request, pedidos=pedidos)


@router.get("/painel/produtos/abastecimento", response_class=HTMLResponse)
def painel_abastecimento(request: Request):
    from finance import empresa as emp, catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    with pool.connection() as c:
        rows = c.execute(
            """select id, data_compra, total_centavos, fonte, status,
                      (select nome from origem_compra where id = compras_fornecedor.origem_id) as origem_nome
               from compras_fornecedor where fornecedor_id=%s order by criado_em desc limit 20""",
            (conta[0],),
        ).fetchall()
    compras = [{"id": r[0], "data_compra": r[1], "total_centavos": r[2], "fonte": r[3], "status": r[4], "origem_nome": r[5]} for r in rows]
    origens = cat_mod.listar_origens(pool, conta[0])
    return _render("abastecimento", request, conta=conta, compras=compras, origens=origens,
                   erro=request.session.pop("erro", None), aviso=request.session.pop("aviso", None))


@router.post("/painel/produtos/abastecimento/criar")
def painel_abastecimento_criar(request: Request, data_compra: str = Form(""), origem_id: int = Form(None)):
    from finance import empresa as emp, catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    try:
        compra_id = cat_mod.criar_compra(pool, conta[0], origem_id, data_compra, fonte="manual")
        request.session["aviso"] = f"Compra #{compra_id} criada em rascunho. Adicione itens."
        return RedirectResponse(f"/painel/produtos/abastecimento/{compra_id}", status_code=303)
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/produtos/abastecimento", status_code=303)


@router.post("/painel/produtos/abastecimento/origem")
def painel_abastecimento_origem(request: Request, nome: str = Form(""), contato: str = Form("")):
    from finance import empresa as emp, catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    try:
        cat_mod.criar_origem(pool, conta[0], nome, contato)
        request.session["aviso"] = f"Origem '{nome}' criada!"
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/produtos/abastecimento", status_code=303)


@router.get("/painel/produtos/abastecimento/{compra_id}", response_class=HTMLResponse)
def painel_abastecimento_revisar(request: Request, compra_id: int):
    from finance import empresa as emp, catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    try:
        itens = cat_mod.listar_itens_compra(pool, conta[0], compra_id)
    except Exception:
        request.session["erro"] = "Compra não encontrada."
        return RedirectResponse("/painel/produtos/abastecimento", status_code=303)
    produtos = cat_mod.listar_produtos(pool, conta[0])
    with pool.connection() as c:
        cab = c.execute(
            """select id, data_compra, total_centavos, status,
                      (select nome from origem_compra where id = origem_id)
               from compras_fornecedor where id=%s and fornecedor_id=%s""",
            (compra_id, conta[0]),
        ).fetchone()
    if cab is None:
        request.session["erro"] = "Compra não encontrada."
        return RedirectResponse("/painel/produtos/abastecimento", status_code=303)
    compra = {"id": cab[0], "data": cab[1], "total_centavos": cab[2], "status": cab[3], "origem_nome": cab[4]}
    return _render("compra_revisar", request, conta=conta, compra=compra, itens=itens, produtos=produtos,
                   erro=request.session.pop("erro", None), aviso=request.session.pop("aviso", None))


@router.post("/painel/produtos/abastecimento/{compra_id}/item")
def painel_abastecimento_add_item(request: Request, compra_id: int,
                                  produto_id: int = Form(...), quantidade: float = Form(...),
                                  custo_unit: str = Form(...)):
    from finance import empresa as emp, catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    s2 = custo_unit.replace("R$", "").strip().replace(".", "").replace(",", ".") if "," in custo_unit else custo_unit.replace("R$", "").strip()
    try:
        custo_centavos = int(round(float(s2) * 100))
    except ValueError:
        custo_centavos = 0
    try:
        prods = {p["id"]: p["nome"] for p in cat_mod.listar_produtos(pool, conta[0])}
        desc = prods.get(produto_id, "produto")
        with pool.connection() as c:
            urow = c.execute("select unidade from catalogo_produtos where id=%s", (produto_id,)).fetchone()
        unidade = urow[0] if urow else "kg"
        cat_mod.adicionar_item_compra(pool, conta[0], compra_id, desc, quantidade, custo_centavos, unidade=unidade, produto_id=produto_id)
        request.session["aviso"] = "Item adicionado."
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse(f"/painel/produtos/abastecimento/{compra_id}", status_code=303)


@router.post("/painel/produtos/abastecimento/{compra_id}/confirmar")
def painel_abastecimento_confirmar(request: Request, compra_id: int):
    from finance import empresa as emp, catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    try:
        r = cat_mod.confirmar_compra(pool, conta[0], compra_id)
        request.session["aviso"] = f"✓ Compra confirmada! {r['itens']} item(ns) deram entrada no estoque."
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/produtos/abastecimento", status_code=303)


@router.post("/painel/produtos/vender")
async def painel_produtos_vender(request: Request):
    from finance import empresa as emp, pdv
    conta = conta_logada(request)
    if conta is None:
        return JSONResponse({"ok": False, "erro": "nao autenticado"}, status_code=401)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return JSONResponse({"ok": False, "erro": "sem acesso"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "erro": "payload invalido"}, status_code=400)
    itens = body.get("itens") or []
    try:
        r = pdv.registrar_venda_balcao(
            pool, conta[0], itens,
            cliente_id=body.get("cliente_id"),
            cliente_nome=body.get("cliente_nome"),
            cliente_telefone=body.get("cliente_telefone"),
            cliente_cpf=body.get("cliente_cpf"),
            pagamento=body.get("pagamento") or "dinheiro",
            vencimento=body.get("vencimento"),
            desconto_centavos=int(body.get("desconto_centavos") or 0),
        )
        return JSONResponse({"ok": True, **r})
    except ValueError as e:
        return JSONResponse({"ok": False, "erro": str(e)}, status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "erro": "erro ao registrar venda"}, status_code=500)


@router.get("/painel/clientes", response_class=HTMLResponse)
def painel_clientes(request: Request, busca: str = ""):
    from finance import empresa as emp, clientes as cli
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    lista = cli.listar_clientes(pool, conta[0], busca=busca or None)
    total = cli.contar_clientes(pool, conta[0])
    return _render("clientes", request, conta=conta, clientes=lista, total=total,
                   busca=busca or "",
                   erro=request.session.pop("erro", None),
                   aviso=request.session.pop("aviso", None))


@router.post("/painel/clientes/novo")
def painel_clientes_novo(request: Request, nome: str = Form(...),
                         telefone: str = Form(""), cpf: str = Form(""),
                         email: str = Form(""), aniversario: str = Form(""),
                         obs: str = Form("")):
    from finance import empresa as emp, clientes as cli
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    try:
        cli.criar_cliente(pool, conta[0], nome, telefone=(telefone or None),
                          cpf=(cpf or None), email=(email or None),
                          aniversario=(aniversario or None), obs=(obs or None))
        request.session["aviso"] = "Cliente cadastrado."
    except ValueError as e:
        request.session["erro"] = str(e)
    return RedirectResponse("/painel/clientes", status_code=303)


@router.get("/painel/clientes/buscar")
def painel_clientes_buscar(request: Request, q: str = ""):
    from finance import empresa as emp, clientes as cli
    conta = conta_logada(request)
    if conta is None:
        return JSONResponse({"clientes": []}, status_code=401)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return JSONResponse({"clientes": []}, status_code=403)
    q = (q or "").strip()
    if len(q) < 2:
        return JSONResponse({"clientes": []})
    lista = cli.listar_clientes(pool, conta[0], busca=q, limite=8)
    out = [{"id": c["id"], "nome": c["nome"], "telefone": c["telefone"], "cpf": c["cpf"]}
           for c in lista]
    return JSONResponse({"clientes": out})


@router.get("/painel/clientes/{cliente_id}", response_class=HTMLResponse)
def painel_cliente_detalhe(request: Request, cliente_id: int):
    from finance import empresa as emp, clientes as cli
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    cl = cli.obter_cliente(pool, conta[0], cliente_id)
    if cl is None:
        request.session["erro"] = "Cliente nao encontrado."
        return RedirectResponse("/painel/clientes", status_code=303)
    with pool.connection() as conn:
        rows = conn.execute(
            """select id, data, valor_centavos, coalesce(pagamento,''), coalesce(descricao,'')
                 from lancamentos
                where cliente_id=%s and conta_id=%s and tipo='receita'
                order by data desc, id desc limit 50""",
            (cliente_id, conta[0]),
        ).fetchall()
        agg = conn.execute(
            """select count(*), coalesce(sum(valor_centavos),0), max(data)
                 from lancamentos where cliente_id=%s and conta_id=%s and tipo='receita'""",
            (cliente_id, conta[0]),
        ).fetchone()
        fiados_rows = conn.execute(
            """select id, valor_centavos, vencimento, coalesce(cobranca_link_url,'')
                 from titulos
                where cliente_id=%s and conta_id=%s and status='aberto' and tipo='receber'
                order by vencimento asc, id asc""",
            (cliente_id, conta[0]),
        ).fetchall()
    fiados = [{"id": r[0], "valor_centavos": r[1], "vencimento": r[2], "link": r[3]}
              for r in fiados_rows]
    fiado_total = sum(f["valor_centavos"] for f in fiados)
    compras = [{"id": r[0], "data": r[1], "valor_centavos": r[2],
                "pagamento": r[3], "descricao": r[4]} for r in rows]
    n = agg[0] or 0
    total = agg[1] or 0
    resumo = {"n": n, "total_centavos": total, "ultima": agg[2],
              "ticket_centavos": (total // n) if n else 0}
    return _render("cliente_detalhe", request, conta=conta, cliente=cl,
                   compras=compras, resumo=resumo, fiados=fiados, fiado_total=fiado_total,
                   erro=request.session.pop("erro", None),
                   aviso=request.session.pop("aviso", None))


@router.post("/painel/clientes/{cliente_id}/editar")
def painel_cliente_editar(request: Request, cliente_id: int, nome: str = Form(""),
                          telefone: str = Form(""), cpf: str = Form(""),
                          email: str = Form(""), aniversario: str = Form(""),
                          obs: str = Form("")):
    from finance import empresa as emp, clientes as cli
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    cli.atualizar_cliente(pool, conta[0], cliente_id, nome=nome, telefone=telefone,
                          cpf=cpf, email=email, aniversario=aniversario, obs=obs)
    request.session["aviso"] = "Cliente atualizado."
    return RedirectResponse(f"/painel/clientes/{cliente_id}", status_code=303)


@router.post("/painel/clientes/{cliente_id}/arquivar")
def painel_cliente_arquivar(request: Request, cliente_id: int):
    from finance import empresa as emp, clientes as cli
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    cli.arquivar_cliente(pool, conta[0], cliente_id)
    request.session["aviso"] = "Cliente arquivado."
    return RedirectResponse("/painel/clientes", status_code=303)


@router.get("/painel/pdv", response_class=HTMLResponse)
def painel_pdv(request: Request, add: int = 0):
    from finance import empresa as emp, catalogo as cat
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    produtos = cat.listar_produtos(pool, conta[0])
    prod_js = [{"id": pr["id"], "nome": pr["nome"], "unidade": pr["unidade"],
                "preco": (pr["preco_venda_centavos"] or 0) / 100.0,
                "saldo": float(pr["saldo"] or 0)} for pr in produtos]
    vendas = []
    totais = {}
    recebido = 0
    fiado_total = 0
    fiado_n = 0
    with pool.connection() as conn:
        pagas = conn.execute(
            """select l.id, (l.criado_em at time zone 'America/Sao_Paulo'),
                      l.valor_centavos, coalesce(l.pagamento,'dinheiro'), coalesce(cc.nome,'')
                 from lancamentos l
                 left join (select c.id cid, coalesce(p.nome, c.nome) nome
                              from clientes c left join pessoas p on p.id = c.pessoa_id) cc
                        on cc.cid = l.cliente_id
                where l.conta_id=%s and l.origem='balcao' and l.tipo='receita'
                      and l.data = current_date
                order by l.criado_em desc""",
            (conta[0],),
        ).fetchall()
        for r in pagas:
            vendas.append({"tipo": "paga", "id": r[0], "hora": r[1],
                           "valor_centavos": r[2], "pagamento": r[3], "cliente": r[4]})
            recebido += r[2]
            t = totais.get(r[3], [0, 0]); t[0] += 1; t[1] += r[2]; totais[r[3]] = t
        fiados = conn.execute(
            """select t.id, (t.criado_em at time zone 'America/Sao_Paulo'),
                      t.valor_centavos, coalesce(cc.nome,''), t.cliente_id
                 from titulos t
                 left join (select c.id cid, coalesce(p.nome, c.nome) nome
                              from clientes c left join pessoas p on p.id = c.pessoa_id) cc
                        on cc.cid = t.cliente_id
                where t.conta_id=%s and t.tipo='receber' and t.status='aberto'
                      and t.criado_em::date = current_date
                      and t.descricao like 'Venda de balcao%%'
                order by t.criado_em desc""",
            (conta[0],),
        ).fetchall()
        for r in fiados:
            vendas.append({"tipo": "fiado", "id": r[0], "hora": r[1],
                           "valor_centavos": r[2], "pagamento": "fiado",
                           "cliente": r[3], "cliente_id": r[4]})
            fiado_total += r[2]; fiado_n += 1
    vendas.sort(key=lambda x: x["hora"], reverse=True)
    totais_lst = [{"metodo": k, "n": v[0], "total_centavos": v[1]} for k, v in totais.items()]
    return _render("pdv", request, conta=conta, produtos_json=prod_js, add=add or 0,
                   vendas=vendas, totais=totais_lst, recebido_centavos=recebido,
                   fiado_total=fiado_total, fiado_n=fiado_n,
                   erro=request.session.pop("erro", None))


@router.post("/painel/clientes/{cliente_id}/fiado/{titulo_id}/baixar")
def painel_cliente_fiado_baixar(request: Request, cliente_id: int, titulo_id: int):
    from finance import empresa as emp
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    try:
        emp.dar_baixa_titulo(pool, conta[0], titulo_id)
        request.session["aviso"] = "Fiado recebido — lancado no caixa."
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse(f"/painel/clientes/{cliente_id}", status_code=303)


@router.post("/painel/clientes/{cliente_id}/fiado/{titulo_id}/cobrar")
def painel_cliente_fiado_cobrar(request: Request, cliente_id: int, titulo_id: int):
    from finance import empresa as emp
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    tits = {t["id"]: t for t in emp.listar_titulos(pool, conta[0], status="aberto")}
    t = tits.get(titulo_id)
    if t and t["tipo"] == "receber":
        try:
            from finance import asaas
            link = asaas.criar_link_pagamento(
                conta_id=f"titulo:{titulo_id}",
                nome_plano=f"Cobranca — {t['descricao']}"[:60],
                valor_reais=t["valor_centavos"] / 100,
                descricao=(t["descricao"] or "")[:100])
            url = link.get("url")
            if url:
                emp.registrar_link_cobranca(pool, conta[0], titulo_id, url)
                request.session["aviso"] = "Link de cobranca Pix gerado."
            else:
                request.session["erro"] = "Nao foi possivel gerar o link agora."
        except Exception:
            request.session["erro"] = "Nao foi possivel gerar o link agora."
    return RedirectResponse(f"/painel/clientes/{cliente_id}", status_code=303)


@router.get("/painel/pdv/venda/{lancamento_id}", response_class=HTMLResponse)
def painel_pdv_venda(request: Request, lancamento_id: int):
    from finance import empresa as emp
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    with pool.connection() as conn:
        cab = conn.execute(
            """select l.id, (l.criado_em at time zone 'America/Sao_Paulo'),
                      l.valor_centavos, coalesce(l.pagamento,''), coalesce(cc.nome,'')
                 from lancamentos l
                 left join (select c.id cid, coalesce(p.nome, c.nome) nome
                              from clientes c left join pessoas p on p.id = c.pessoa_id) cc
                        on cc.cid = l.cliente_id
                where l.id=%s and l.conta_id=%s and l.origem='balcao' and l.tipo='receita'""",
            (lancamento_id, conta[0]),
        ).fetchone()
        if cab is None:
            request.session["erro"] = "Venda nao encontrada."
            return RedirectResponse("/painel/pdv", status_code=303)
        its = conn.execute(
            """select descricao, quantidade, valor_unitario_centavos,
                      valor_total_centavos, coalesce(unidade,'un')
                 from itens_lancamento where lancamento_id=%s order by id""",
            (lancamento_id,),
        ).fetchall()
    itens = [{"descricao": r[0], "quantidade": float(r[1]), "vu": r[2],
              "vt": r[3], "unidade": r[4]} for r in its]
    subtotal = sum(i["vt"] for i in itens)
    venda = {"id": cab[0], "hora": cab[1], "valor_centavos": cab[2],
             "pagamento": cab[3], "cliente": cab[4],
             "subtotal_centavos": subtotal,
             "desconto_centavos": max(0, subtotal - cab[2])}
    return _render("venda_detalhe", request, conta=conta, venda=venda, itens=itens,
                   erro=request.session.pop("erro", None))


@router.get("/painel/empresa", response_class=HTMLResponse)
def painel_empresa(request: Request):
    """Visão geral do módulo Empresa (PJ). Só pra conta com o módulo ativo."""
    from finance import empresa as emp
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.modulo_pj_ativo(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    # Gate: enquanto a empresa não tiver dados (CNPJ+razão), mostra só a tela
    # de cadastro. Resto do app (Financeiro/Compras) segue livre.
    if not emp.dados_empresa_completos(pool, conta[0]):
        d = emp.obter_dados_empresa(pool, conta[0])
        return _render("empresa_dados", request, dados=d, tem_pj=True, erro="",
                       nichos_lista=_nichos.lista_nichos())
    hoje = _date.today()
    res = emp.resumo_titulos(pool, conta[0])
    fluxo = emp.fluxo_projetado(pool, conta[0])
    dre = emp.dre_mes(pool, conta[0], hoje.year, hoje.month)
    doc = ""
    with pool.connection() as c:
        r = c.execute("select coalesce(documento,'') from contas where id=%s",
                      (conta[0],)).fetchone()
        doc = _mascara_cnpj(r[0]) if r else ""
    titulos = emp.listar_titulos(pool, conta[0], status="aberto")
    folha = emp.folha_do_mes(pool, conta[0], hoje.year, hoje.month)
    return _render("empresa", request, empresa_nome=conta[2], empresa_doc=doc,
                   res=res, fluxo=fluxo, dre=dre, titulos=titulos, folha=folha,
                   tem_pj=True)


@router.get("/painel/produtos")
def painel_produtos(request: Request):
    """Aba Produtos do PJ base. Reusa catalogo.py (genérico)."""
    from finance import empresa as emp, catalogo as cat, nichos as nic
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    ov = emp.o_que_vende(pool, conta[0])
    if not ov["produto"]:
        return RedirectResponse("/painel/servicos" if ov["servico"] else "/painel",
                                status_code=303)
    if not emp.cadastro_pj_ok(pool, conta[0]):
        return RedirectResponse("/painel/empresa", status_code=303)
    dados = emp.obter_dados_empresa(pool, conta[0])
    nicho = dados.get("nicho") or ""
    produtos = cat.listar_produtos(pool, conta[0])
    valor = sum(int(p.get("preco_venda_centavos") or 0) * (p.get("saldo") or 0)
                for p in produtos)
    resumo = {"total": len(produtos), "valor_estoque": int(valor),
              "baixo": sum(1 for p in produtos if p.get("abaixo_minimo"))}
    return _render("produtos", request, produtos=produtos, resumo=resumo,
                   nicho_label=nic.label_do_nicho(nicho),
                   unidades=nic.unidades_do_nicho(nicho),
                   categorias=nic.categorias_do_nicho(nicho),
                   label_unidade=nic.label_unidade,
                   origens=cat.listar_origens(pool, conta[0]),
                   pode_servico=ov["servico"],
                   aviso=request.session.pop("aviso", ""),
                   erro=request.session.pop("erro", ""),
                   tem_pj=True, vende_produto=True, vende_servico=ov["servico"])


@router.post("/painel/produtos/salvar")
def painel_produtos_salvar(request: Request,
                           produto_id: str = Form(""),
                           nome: str = Form(...),
                           unidade: str = Form("unidade"),
                           categoria: str = Form(""),
                           preco_venda: str = Form("0"),
                           estoque_minimo: str = Form("0"),
                           foto_url: str = Form("")):
    """Cria (produto_id vazio) ou edita (produto_id preenchido). Reusa catalogo.py."""
    from finance import empresa as emp, catalogo as cat
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    try:
        s = preco_venda.replace("R$", "").strip()
        s = s.replace(".", "").replace(",", ".") if "," in s else s
        preco_centavos = int(round(float(s or 0) * 100))
        emin = float((estoque_minimo or "0").replace(",", "."))
        dados = emp.obter_dados_empresa(pool, conta[0])
        if (produto_id or "").strip():
            campos = dict(nome=nome, unidade=unidade,
                          categoria=(categoria.strip() or None),
                          preco_venda_centavos=preco_centavos,
                          estoque_minimo=emin)
            if foto_url.strip():
                campos["foto_url"] = foto_url.strip()
            cat.atualizar_produto(pool, conta[0], int(produto_id), **campos)
            request.session["aviso"] = "Produto atualizado."
        else:
            cat.criar_produto(pool, conta[0], nome, unidade, categoria or "",
                              preco_centavos, emin,
                              foto_url=(foto_url.strip() or None),
                              nicho=dados.get("nicho") or None)
            request.session["aviso"] = f"Produto '{nome}' criado!"
    except Exception as e:
        request.session["erro"] = f"Erro: {e}"
    return RedirectResponse("/painel/produtos", status_code=303)


@router.post("/painel/produtos/entrada")
def painel_produtos_entrada(request: Request,
                            produto_id: int = Form(...),
                            quantidade: str = Form(""),
                            custo_unit: str = Form(""),
                            origem_id: int = Form(...)):
    from finance import empresa as emp, catalogo as cat
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    try:
        custo_centavos = int(float(custo_unit or 0) * 100)
        r = cat.registrar_movimentacao(pool, conta[0], produto_id, "entrada",
                                       float(quantidade or 0), custo_centavos, origem_id)
        aviso = f"✓ Entrada registrada! Novo saldo: {r['saldo_novo']} " \
                f"| Custo médio: R$ {r['custo_medio_centavos']/100:.2f}"
        if r.get("abaixo_minimo"):
            aviso += " ⚠ Abaixo do mínimo!"
        request.session["aviso"] = aviso
    except Exception as e:
        request.session["erro"] = f"Erro: {e}"
    return RedirectResponse("/painel/produtos", status_code=303)


@router.post("/painel/produtos/perda")
def painel_produtos_perda(request: Request,
                          produto_id: int = Form(...),
                          quantidade: str = Form(""),
                          motivo: str = Form("")):
    from finance import empresa as emp, catalogo as cat
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    try:
        r = cat.registrar_movimentacao(pool, conta[0], produto_id, "perda",
                                       float(quantidade or 0), motivo=motivo)
        request.session["aviso"] = f"✓ Perda registrada. Novo saldo: {r['saldo_novo']}"
    except Exception as e:
        request.session["erro"] = f"Erro: {e}"
    return RedirectResponse("/painel/produtos", status_code=303)


@router.post("/painel/produtos/apagar")
def painel_produtos_apagar(request: Request, produto_id: int = Form(...)):
    from finance import empresa as emp, catalogo as cat
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    try:
        cat.arquivar_produto(pool, conta[0], produto_id)
        request.session["aviso"] = "Produto apagado."
    except Exception as e:
        request.session["erro"] = f"Erro: {e}"
    return RedirectResponse("/painel/produtos", status_code=303)


@router.post("/painel/produtos/origem")
def painel_produtos_origem(request: Request,
                           nome: str = Form(""),
                           contato: str = Form("")):
    """Cria uma origem de compra (pro modal de entrada)."""
    from finance import empresa as emp, catalogo as cat
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    try:
        cat.criar_origem(pool, conta[0], nome, contato)
        request.session["aviso"] = f"Origem '{nome}' criada!"
    except Exception as e:
        request.session["erro"] = f"Erro: {e}"
    return RedirectResponse("/painel/produtos", status_code=303)


@router.post("/painel/produtos/foto")
def painel_produtos_foto(request: Request, dados: dict = Body(...)):
    from finance import empresa as emp, catalogo as cat
    conta = conta_logada(request)
    if conta is None:
        return JSONResponse({"erro": "nao autenticado"}, status_code=401)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return JSONResponse({"erro": "sem modulo pj"}, status_code=403)
    produto_id = int(dados.get("produto_id", 0))
    foto_url = (dados.get("foto_url") or "").strip()
    cat.atualizar_produto(pool, conta[0], produto_id, foto_url=foto_url or None)
    return JSONResponse({"ok": True})


@router.post("/painel/produtos/promo")
def painel_produtos_promo(request: Request, dados: dict = Body(...)):
    from finance import empresa as emp, catalogo as cat
    conta = conta_logada(request)
    if conta is None:
        return JSONResponse({"erro": "nao autenticado"}, status_code=401)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return JSONResponse({"erro": "sem modulo pj"}, status_code=403)
    produto_id = int(dados.get("produto_id", 0))
    em_promo = bool(dados.get("em_promo"))
    preco_promo = float(dados.get("preco_promo", 0)) if em_promo else 0
    promo_c = int(round(preco_promo * 100)) if em_promo else None
    cat.atualizar_produto(pool, conta[0], produto_id,
                          em_promo=em_promo, preco_promo_centavos=promo_c)
    return JSONResponse({"ok": True, "em_promo": em_promo, "preco_promo_centavos": promo_c})


@router.post("/painel/produtos/upload-foto")
async def painel_produtos_upload_foto(request: Request, produto_id: int = Form(...),
                                      arquivo: UploadFile = File(...)):
    from finance import upload_foto, empresa as emp, catalogo as cat
    conta = conta_logada(request)
    if conta is None:
        return JSONResponse({"erro": "nao autenticado"}, status_code=401)
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return JSONResponse({"erro": "sem modulo pj"}, status_code=403)
    try:
        conteudo = await arquivo.read()
        url = upload_foto.subir_foto(conteudo, arquivo.filename or "",
                                     arquivo.content_type or "image/jpeg")
    except ValueError as e:
        return JSONResponse({"erro": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"erro": f"Erro ao processar: {e}"}, status_code=500)
    cat.atualizar_produto(pool, conta[0], produto_id, foto_url=url)
    return JSONResponse({"ok": True, "foto_url": url})


@router.get("/painel/produtos/sugerir-fotos")
def painel_produtos_sugerir_fotos(request: Request, nome: str = ""):
    from finance import galeria_fotos, empresa as emp
    conta = conta_logada(request)
    if conta is None:
        return JSONResponse({"opcoes": []}, status_code=401)
    if not emp.acesso_pj(get_pool(), conta[0]):
        return JSONResponse({"opcoes": []})
    return JSONResponse({"opcoes": galeria_fotos.opcoes_de_foto(nome, n=4)})


@router.get("/painel/produtos/sugerir-categoria")
def painel_produtos_sugerir_categoria(request: Request, nome: str = ""):
    from finance import categorias as cat_sug, empresa as emp
    conta = conta_logada(request)
    if conta is None:
        return JSONResponse({"categoria": None}, status_code=401)
    if not emp.acesso_pj(get_pool(), conta[0]):
        return JSONResponse({"categoria": None})
    return JSONResponse({"categoria": cat_sug.sugerir_categoria(nome)})


@router.post("/painel/produtos/ler-planilha")
async def painel_produtos_ler_planilha(request: Request):
    from finance.catalogo_import import ler_planilha_csv
    from finance import empresa as emp
    conta = conta_logada(request)
    if conta is None:
        return {"ok": False, "erro": "nao autenticado"}
    if not emp.acesso_pj(get_pool(), conta[0]):
        return {"ok": False, "erro": "sem modulo pj"}
    try:
        form = await request.form()
        arquivo = form.get("arquivo")
        if not arquivo:
            return {"ok": False, "erro": "arquivo nao enviado"}
        conteudo = await arquivo.read()
        return ler_planilha_csv(conteudo)
    except Exception as e:
        return {"ok": False, "erro": str(e)}


@router.post("/painel/produtos/importar-planilha")
async def painel_produtos_importar_planilha(request: Request):
    from finance.catalogo_import import importar_itens
    from finance import empresa as emp
    conta = conta_logada(request)
    if conta is None:
        return {"ok": False, "erro": "nao autenticado"}
    pool = get_pool()
    if not emp.acesso_pj(pool, conta[0]):
        return {"ok": False, "erro": "sem modulo pj"}
    try:
        body = await request.json()
        itens = body.get("itens", [])
        return importar_itens(pool, conta[0], itens)
    except Exception as e:
        return {"ok": False, "erro": str(e)}


@router.get("/painel/empresa/dados", response_class=HTMLResponse)
def painel_empresa_dados_form(request: Request):
    from finance import empresa as emp
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.modulo_pj_ativo(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    d = emp.obter_dados_empresa(pool, conta[0])
    eh_forn = bool(conta[8])
    identidade, margem_alvo = {}, 60.0
    if eh_forn:
        with pool.connection() as c:
            row = c.execute("select margem_alvo_pct, logo_url, banner_url from contas where id=%s", (conta[0],)).fetchone()
        if row:
            margem_alvo = float(row[0]) if row[0] else 60.0
            identidade = {"logo_url": row[1], "banner_url": row[2]}
    return _render("empresa_dados", request, dados=d, tem_pj=True, erro="",
                   nichos_lista=_nichos.lista_nichos(),
                   eh_fornecedor=eh_forn, identidade=identidade, margem_alvo=margem_alvo)


@router.post("/painel/empresa/dados", response_class=HTMLResponse)
def painel_empresa_dados_salvar(
    request: Request,
    documento: str = Form(""), razao_social: str = Form(""),
    nome_fantasia: str = Form(""), endereco: str = Form(""),
    bairro: str = Form(""), cep: str = Form(""),
    cidade: str = Form(""), uf: str = Form(""),
    email_empresa: str = Form(""), telefone: str = Form(""),
    nicho: str = Form(""),
):
    from finance import empresa as emp
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    if not emp.modulo_pj_ativo(pool, conta[0]):
        return RedirectResponse("/painel", status_code=303)
    ok, msg = emp.salvar_dados_empresa(
        pool, conta[0], documento=documento, razao_social=razao_social,
        nome_fantasia=nome_fantasia, endereco=endereco, bairro=bairro,
        cep=cep, cidade=cidade, uf=uf, email_empresa=email_empresa,
        telefone=telefone, nicho=nicho)
    if not ok:
        d = emp.obter_dados_empresa(pool, conta[0])
        # devolve o que o usuário digitou + erro
        d.update({"documento": documento, "razao_social": razao_social,
                  "nome_fantasia": nome_fantasia, "endereco": endereco,
                  "bairro": bairro, "cep": cep, "cidade": cidade, "uf": uf,
                  "email_empresa": email_empresa, "telefone": telefone})
        return _render("empresa_dados", request, dados=d, tem_pj=True, erro=msg,
                       nichos_lista=_nichos.lista_nichos())
    return RedirectResponse("/painel/empresa", status_code=303)


@router.get("/painel/empresa/buscar-cnpj")
def painel_empresa_buscar_cnpj(request: Request, cnpj: str = ""):
    conta = conta_logada(request)
    if conta is None:
        return JSONResponse({"ok": False}, status_code=401)
    from finance import cnpj_info
    dados = cnpj_info.consultar_cnpj(cnpj)
    if not dados:
        return JSONResponse({"ok": False})
    return JSONResponse({"ok": True, "nome": dados.get("nome") or "",
                         "endereco": dados.get("endereco") or "",
                         "bairro": dados.get("bairro") or "",
                         "cep": dados.get("cep") or "",
                         "cidade": dados.get("cidade") or "",
                         "uf": dados.get("uf") or "",
                         "email": dados.get("email") or "",
                         "telefone": dados.get("telefone") or "",
                         "nicho": dados.get("nicho") or ""})


def _mascara_cnpj(doc: str) -> str:
    d = "".join(ch for ch in (doc or "") if ch.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return doc or ""


def _reais_para_centavos(txt: str) -> int:
    t = (txt or "").strip().replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return int(round(float(t) * 100))
    except (ValueError, TypeError):
        return 0


def _guard_pj(request: Request):
    """Retorna (conta, pool) se a conta tem o módulo PJ ativo; senão None."""
    from finance import empresa as emp
    conta = conta_logada(request)
    if conta is None:
        return None
    pool = get_pool()
    if not emp.modulo_pj_ativo(pool, conta[0]):
        return None
    return conta, pool


@router.post("/painel/empresa/titulo")
def empresa_titulo_criar(request: Request, tipo: str = Form("pagar"),
                         descricao: str = Form(""), valor: str = Form(""),
                         vencimento: str = Form(""), contraparte: str = Form(""),
                         recorrente: str = Form("")):
    from finance import empresa as emp
    g = _guard_pj(request)
    if not g:
        return RedirectResponse("/painel", status_code=303)
    conta, pool = g
    cent = _reais_para_centavos(valor)
    if cent > 0 and descricao.strip() and vencimento:
        try:
            emp.criar_titulo(pool, conta[0], tipo if tipo in ("pagar", "receber") else "pagar",
                             descricao, cent, _date.fromisoformat(vencimento),
                             contraparte=contraparte,
                             recorrente=bool(recorrente), criado_por=None)
        except Exception:
            pass
    return RedirectResponse("/painel/empresa", status_code=303)


@router.post("/painel/empresa/titulo/{titulo_id}/baixa")
def empresa_titulo_baixa(request: Request, titulo_id: int):
    from finance import empresa as emp
    g = _guard_pj(request)
    if not g:
        return RedirectResponse("/painel", status_code=303)
    conta, pool = g
    emp.dar_baixa_titulo(pool, conta[0], titulo_id)
    return RedirectResponse("/painel/empresa", status_code=303)


@router.post("/painel/empresa/titulo/{titulo_id}/cobrar")
def empresa_titulo_cobrar(request: Request, titulo_id: int):
    from finance import empresa as emp
    g = _guard_pj(request)
    if not g:
        return RedirectResponse("/painel", status_code=303)
    conta, pool = g
    tits = {t["id"]: t for t in emp.listar_titulos(pool, conta[0], status="aberto")}
    t = tits.get(titulo_id)
    if t and t["tipo"] == "receber":
        try:
            from finance import asaas
            link = asaas.criar_link_pagamento(
                conta_id=f"titulo:{titulo_id}", nome_plano=f"Cobrança — {t['descricao']}"[:60],
                valor_reais=t["valor_centavos"] / 100,
                descricao=(t["descricao"] or "")[:100])
            url = link.get("url")
            if url:
                emp.registrar_link_cobranca(pool, conta[0], titulo_id, url)
        except Exception:
            pass
    return RedirectResponse("/painel/empresa", status_code=303)


@router.post("/painel/empresa/titulo/{titulo_id}/descricao")
def empresa_titulo_descricao(request: Request, titulo_id: int,
                             descricao: str = Form("")):
    from finance import empresa as emp
    g = _guard_pj(request)
    if not g:
        return RedirectResponse("/painel", status_code=303)
    conta, pool = g
    if descricao.strip():
        emp.editar_descricao_titulo(pool, conta[0], titulo_id, descricao)
    return RedirectResponse("/painel/empresa", status_code=303)


@router.post("/painel/empresa/titulo/{titulo_id}/apagar")
def empresa_titulo_apagar(request: Request, titulo_id: int):
    from finance import empresa as emp
    g = _guard_pj(request)
    if not g:
        return RedirectResponse("/painel", status_code=303)
    conta, pool = g
    emp.apagar_titulo(pool, conta[0], titulo_id)
    return RedirectResponse("/painel/empresa", status_code=303)


@router.post("/painel/empresa/funcionario")
def empresa_funcionario_criar(request: Request, nome: str = Form(""),
                              cargo: str = Form(""), salario: str = Form(""),
                              dia: str = Form("5"), pro_labore: str = Form("")):
    from finance import empresa as emp
    g = _guard_pj(request)
    if not g:
        return RedirectResponse("/painel", status_code=303)
    conta, pool = g
    if nome.strip():
        try:
            dia_i = max(1, min(28, int(dia or "5")))
        except ValueError:
            dia_i = 5
        try:
            emp.criar_funcionario(pool, conta[0], nome, cargo=cargo,
                                  salario_centavos=_reais_para_centavos(salario),
                                  dia_pagamento=dia_i, pro_labore=bool(pro_labore))
        except Exception:
            pass
    return RedirectResponse("/painel/empresa", status_code=303)


@router.post("/painel/empresa/funcionario/{funcionario_id}/vale")
def empresa_vale(request: Request, funcionario_id: int, valor: str = Form("")):
    from finance import empresa as emp
    g = _guard_pj(request)
    if not g:
        return RedirectResponse("/painel", status_code=303)
    conta, pool = g
    cent = _reais_para_centavos(valor)
    if cent > 0:
        emp.registrar_evento_folha(pool, conta[0], funcionario_id, "vale", cent)
    return RedirectResponse("/painel/empresa", status_code=303)


@router.post("/painel/empresa/folha/pagar")
def empresa_folha_pagar(request: Request, funcionario_id: str = Form("")):
    from finance import empresa as emp
    g = _guard_pj(request)
    if not g:
        return RedirectResponse("/painel", status_code=303)
    conta, pool = g
    hoje = _date.today()
    fid = int(funcionario_id) if funcionario_id.strip().isdigit() else None
    emp.pagar_folha(pool, conta[0], hoje.year, hoje.month, funcionario_id=fid)
    return RedirectResponse("/painel/empresa", status_code=303)


@router.get("/painel/empresa/contador.csv")
def empresa_contador_csv(request: Request, ano: int = 0, mes: int = 0):
    from finance import empresa as emp
    g = _guard_pj(request)
    if not g:
        return RedirectResponse("/painel", status_code=303)
    conta, pool = g
    hoje = _date.today()
    ano = ano or hoje.year
    mes = mes or hoje.month
    csv = emp.csv_contador(pool, conta[0], ano, mes)
    return HTMLResponse(csv, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="empresa_{ano}_{mes:02d}.csv"'})



@router.get("/painel/financeiro", response_class=HTMLResponse)
def painel_financeiro(request: Request, mes: str = "", membro: str = "", tipo: str = "", q: str = "", natureza: str = ""):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not pode_financas(_papel_logado(request, conta[0])):
        return RedirectResponse("/painel/compras", status_code=303)
    pool = get_pool()
    hoje = date.today()
    # Gate PJ ÚNICO: usa modulo_pj_ativo (mesma verdade da aba Empresa e do bot).
    # Cobre plano PJ + status válido E o override por cortesia (conta_modulos).
    # Chamado uma vez só por request — não pesa pra conta PF (retorna False).
    from finance import empresa as _emp
    eh_pj = _emp.modulo_pj_ativo(pool, conta[0])
    try:
        ano_sel, mes_num = (int(x) for x in mes.split("-")) if mes else (hoje.year, hoje.month)
    except ValueError:
        ano_sel, mes_num = hoje.year, hoje.month
    mes_sel = f"{ano_sel:04d}-{mes_num:02d}"
    membro_sel = int(membro) if membro.isdigit() else None

    with pool.connection() as c:
        pessoas = c.execute(
            "select id, coalesce(nome,'-') from membros where conta_id=%s and ativo order by id",
            (conta[0],)).fetchall()
    if membro_sel is not None and membro_sel not in {p[0] for p in pessoas}:
        membro_sel = None

    livro = LivroCaixa(pool, conta[0])

    # Previsao da fatura do cartao (compras parceladas no credito). Materializa as
    # parcelas ja' vencidas (viram despesa do mes) e olha as futuras. Tolerante:
    # nunca deixa derrubar o painel.
    try:
        livro.materializar_parcelas_devidas()
        prev_cartao = livro.previsao_cartao(meses=6)
    except Exception:
        prev_cartao = {"total_centavos": 0, "pontos": [], "meses": 6}

    # Se há busca, usa buscar_lancamentos; senão fluxo normal
    if q:
        lancamentos = livro.buscar_lancamentos(q, limite=100)
        dias = []  # plano, não agrupado
        raiox = {}
        # soma os resultados da busca (valor em centavos, como resumo_mes/brl)
        _rec = sum(l["valor"] for l in lancamentos if l["tipo"] == "receita")
        _desp = sum(l["valor"] for l in lancamentos if l["tipo"] == "despesa")
        resumo = {"saldo": _rec - _desp, "receitas": _rec, "despesas": _desp, "anterior": 0}
        categorias = []
        receitas_cat = []
        maior_cat = 0
        maior_rec = 0
        n_a_definir = 0
        quebra = None
    else:
        # natureza (pessoal/empresa/a_definir) só filtra em conta PJ; PF ignora
        nat = natureza if (eh_pj and natureza in ("pessoal", "empresa", "a_definir")) else None
        # quebra por natureza (só PJ): 1 query com group by no lugar de 4x resumo_mes (perf)
        quebra = None
        if eh_pj:
            _q = livro.resumo_mes_quebra(ano_sel, mes_num, membro_sel)
            resumo = _q[nat] if nat else _q["total"]
            quebra = {
                "receitas": {"empresa": _q["empresa"]["receitas"], "pessoal": _q["pessoal"]["receitas"], "a_definir": _q["a_definir"]["receitas"]},
                "despesas": {"empresa": _q["empresa"]["despesas"], "pessoal": _q["pessoal"]["despesas"], "a_definir": _q["a_definir"]["despesas"]},
            }
        else:
            resumo = livro.resumo_mes(ano_sel, mes_num, membro_sel, natureza=nat)
        categorias = livro.despesas_por_categoria(ano_sel, mes_num, membro_sel, natureza=nat)
        maior_cat = max((v for _, v in categorias), default=0)
        receitas_cat = livro.receitas_por_categoria(ano_sel, mes_num, membro_sel, natureza=nat)
        maior_rec = max((v for _, v in receitas_cat), default=0)
        lancamentos = livro.lancamentos_recentes(ano_sel, mes_num, membro_sel,
                                                 tipo if tipo in ("despesa", "receita") else None,
                                                 limite=1000, natureza=nat)
        # agrupa por DIA (pro accordion): cada dia com seu saldo e seus lancamentos
        from collections import OrderedDict
        por_dia = OrderedDict()
        for l in lancamentos:
            d = l["data"]
            if d not in por_dia:
                por_dia[d] = {"itens": [], "saldo": 0}
            por_dia[d]["itens"].append(l)
            por_dia[d]["saldo"] += l["valor"] if l["tipo"] == "receita" else -l["valor"]
        dias = [{"data": d, "itens": g["itens"], "saldo": g["saldo"]} for d, g in por_dia.items()]
        n_a_definir = livro.contar_a_definir(ano_sel, mes_num) if eh_pj else 0
        raiox_bruto = livro.raiox_por_departamento(ano=ano_sel, mes=mes_num, membro_id=membro_sel)
        # monta {dep: {total, dias:[{data, itens, subtotal}]}} - itens divididos por dia
        from collections import OrderedDict
        raiox = {}
        for dep, itens in raiox_bruto.items():
            por_dia = OrderedDict()
            for it in itens:
                por_dia.setdefault(it["data"], []).append(it)
            dias_dep = [{"data": d, "itens": its, "subtotal": sum(i["valor"] for i in its)}
                        for d, its in por_dia.items()]
            raiox[dep] = {"total": sum(i["valor"] for i in itens), "dias": dias_dep}

    from finance.models import CATEGORIAS_DESPESA, CATEGORIAS_RECEITA
    categorias_lista = CATEGORIAS_DESPESA + [c for c in CATEGORIAS_RECEITA if c not in CATEGORIAS_DESPESA]

    meses = []
    y, m = hoje.year, hoje.month
    nomes = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]
    for _ in range(6):
        meses.append((f"{y:04d}-{m:02d}", f"{nomes[m-1]}/{y}"))
        m -= 1
        if m == 0:
            m = 12; y -= 1

    return _render("dash", request, titulo="Financeiro", conta=conta,
                   resumo=resumo, categorias=categorias, maior_cat=maior_cat,
                   lancamentos=lancamentos, dias=dias, raiox=raiox, pessoas=pessoas,
                   meses=meses, mes_sel=mes_sel, membro_sel=membro_sel, tipo_sel=tipo,
                   receitas_cat=receitas_cat, maior_rec=maior_rec, categorias_lista=categorias_lista,
                   q_search=q, n_resultados=len(lancamentos) if q else 0,
                   natureza_sel=(natureza if eh_pj else ""),
                   n_a_definir=n_a_definir,
                   quebra=quebra,
                   prev_cartao=prev_cartao,
                   eh_pj=eh_pj)


# ---------- lista de compras ----------

def _lista_logada(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return None, None
    pool = get_pool()
    with pool.connection() as c:
        m = c.execute("select id from membros where conta_id=%s order by id limit 1",
                      (conta[0],)).fetchone()
    membro_id = m[0] if m else None
    return conta, ListaCompras(pool, conta[0], membro_id)


@router.get("/painel/compras", response_class=HTMLResponse)
def compras(request: Request):
    conta, lista = _lista_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    # busca o papel do usuario logado (dono, membro, restrito)
    pool = get_pool()
    with pool.connection() as c:
        m = c.execute(
            "select papel from membros where conta_id=%s order by id limit 1",
            (conta[0],)).fetchone()
    papel = m[0] if m else "membro"
    # a pagina carrega "vazia" e o JS busca a lista e os precos via AJAX
    # (sem reload a cada acao). Ver endpoint /painel/compras/api.
    return _render("compras", request, titulo="Compras", papel=papel)


@router.get("/painel/compras/precos")
def compras_precos(request: Request):
    """Endpoint chamado pelo JS em segundo plano: calcula o comparador separado
    por departamento e devolve JSON. A pagina ja' carregou; isso preenche depois."""
    conta = conta_logada(request)
    if conta is None:
        return JSONResponse({"erro": "nao logado"}, status_code=401)
    from finance.banco_precos import comparar_separado
    from finance.lista_compras import ListaCompras
    lista = ListaCompras(get_pool(), conta[0])
    pendentes = [i["descricao"] for i in lista.listar(incluir_comprados=False)]
    if not pendentes:
        return JSONResponse({"grupos": {}, "observacoes": 0, "fonte": "vazio"})
    # escolhas de ajuste: parametros ajuste=item||termo
    escolhas: dict[str, str] = {}
    for a in request.query_params.getlist("ajuste"):
        if "||" in a:
            item, termo = a.split("||", 1)
            if item.strip() and termo.strip():
                escolhas[item.strip()] = termo.strip()
    with get_pool().connection() as c:
        row = c.execute("select cidade from contas where id=%s", (conta[0],)).fetchone()
    cidade = row[0] if row else None
    try:
        r = comparar_separado(get_pool(), pendentes, cidade, escolhas)
    except Exception:  # noqa: BLE001
        return JSONResponse({"grupos": {}, "observacoes": 0, "fonte": "erro"})
    return JSONResponse(_fmt_comparacao(r, cidade))


def _fmt_comparacao(r: dict, cidade: str | None = None) -> dict:
    """Formata o resultado do comparador pra JSON amigavel ao front (com BRL). Cada
    produto leva dias (recencia) e fonte='cupom'. Itens SEM cupom ganham referencia de
    catalogo (fora do total)."""
    from datetime import date
    rotulos = {"mercado": "🏪 Mercado", "farmacia": "💊 Farmácia", "outro": "🏪 Mercado"}
    grupos = {}
    itens_detalhe = {item["descricao"]: item for item in r.get("itens", [])}

    def _dias(dt):
        try:
            return (date.today() - dt).days
        except Exception:  # noqa: BLE001
            return None

    for tipo, g in r.get("grupos", {}).items():
        linhas = []
        for m in g["mercados"]:
            nome_merc = m["mercado"]
            produtos = []
            endereco_merc = None
            for item_desc, item_info in itens_detalhe.items():
                for preco in item_info.get("precos", []):
                    if preco["mercado"] == nome_merc:
                        produtos.append({
                            "descricao": preco["descricao"],
                            "preco": brl(preco["valor_centavos"]),
                            "fonte": "cupom",
                            "dias": _dias(preco.get("data")),
                        })
                        if endereco_merc is None and preco.get("endereco"):
                            endereco_merc = preco["endereco"]
                        break
            linhas.append({
                "mercado": nome_merc,
                "total": brl(m["total_centavos"]),
                "cobertos": m["itens_cobertos"],
                "faltando": len(m["itens_faltando"]),
                "endereco": endereco_merc or "",
                "produtos": produtos,
            })
        if linhas:
            grupos[rotulos.get(tipo, tipo)] = linhas

    # itens da lista que NINGUÉM tem em cupom -> referência de catálogo (fora do total)
    sem_cupom = []
    try:
        from finance.banco_precos import BancoPrecos
        banco = BancoPrecos(get_pool())
        for desc, info in itens_detalhe.items():
            if not info.get("precos"):
                ref = banco.referencia_catalogo(desc, regiao=cidade)
                if ref:
                    sem_cupom.append({
                        "item": desc,
                        "descricao": ref["descricao"],
                        "mercado": ref["mercado"],
                        "preco": brl(ref["valor_centavos"]),
                    })
    except Exception:  # noqa: BLE001
        pass

    return {"grupos": grupos, "observacoes": r.get("observacoes", 0),
            "fonte": r.get("fonte", "vazio"), "sem_cupom": sem_cupom}


@router.get("/painel/compras/opcoes")
def compras_opcoes(request: Request, item: str = "", gtin: str = "", produto: str = "", tudo: str = ""):
    """Menor preço em 2 níveis: sem gtin -> produtos distintos (nome + faixa);
    com gtin/produto -> lojas daquele produto exato, com fonte (cupom/catálogo).
    opcoes_item já devolve o dict pronto {"nivel","opcoes",...} - NÃO embrulhar."""
    conta = conta_logada(request)
    if conta is None:
        return JSONResponse({"erro": "nao logado"}, status_code=401)
    item = (item or "").strip()
    if not item and not gtin and not produto:
        return JSONResponse({"nivel": "produtos", "termo": "", "opcoes": []})
    with get_pool().connection() as c:
        row = c.execute("select cidade from contas where id=%s", (conta[0],)).fetchone()
    cidade = row[0] if row else None
    from finance.banco_precos import BancoPrecos
    resp = BancoPrecos(get_pool()).opcoes_item(
        item, regiao=cidade, gtin=(gtin or None), produto=(produto or None), tudo=bool(tudo))
    return JSONResponse(resp)


@router.post("/painel/compras/api")
async def compras_api(request: Request):
    """Acao na lista SEM reload: recebe {acao, ...} e devolve a lista atualizada
    em JSON. O front redesenha so' a lista, sem recarregar a pagina."""
    conta, lista = _lista_logada(request)
    if conta is None:
        return JSONResponse({"erro": "nao logado"}, status_code=401)
    dados = await request.json()
    acao = dados.get("acao")
    if acao == "add":
        import re as _re
        partes = [p.strip() for p in _re.split(r"[,;\n]+", dados.get("descricao", "")) if p.strip()]
        if len(partes) > 1:
            lista.adicionar_varios(partes)
        elif partes:
            lista.adicionar(partes[0])
    elif acao == "marcar":
        lista.marcar_comprado(int(dados["item_id"]), bool(dados.get("comprado", 1)))
    elif acao == "remover":
        lista.remover(int(dados["item_id"]))
    elif acao == "limpar":
        lista.limpar_comprados()
    elif acao == "apagar_tudo":
        lista.limpar_tudo()
    elif acao == "finalizar":
        resultado = lista.finalizar_compra()
    # NAO estima preco aqui (sob demanda no botao "Comparar precos" ->
    # /painel/compras/precos). Adicionar item fica leve, sem buscar preco.
    itens = lista.listar(incluir_comprados=True)  # frontend separa pendentes/comprados
    resumo = lista.resumo()
    return JSONResponse({
        "itens": [{"id": i["id"], "descricao": i["descricao"], "comprado": i["comprado"],
                   "quem": i["quem"],
                   "preco": brl(i["preco_estimado_centavos"]) if i["preco_estimado_centavos"] else None}
                  for i in itens],
        "pendentes": resumo["pendentes"], "comprados": resumo["comprados"],
        "tem_pendentes": resumo["pendentes"] > 0,
    })


@router.get("/painel/compras/historico", response_class=JSONResponse)
def compras_historico(request: Request):
    """Lista compras finalizadas desta conta."""
    conta, lista = _lista_logada(request)
    if conta is None:
        return JSONResponse({"erro": "nao logado"}, status_code=401)
    hist = lista.listar_historico()
    # formata data pra exibir
    for h in hist:
        h["data"] = h["criado_em"].strftime("%d/%m/%Y") if h.get("criado_em") else "-"
        h.pop("criado_em", None)
    return JSONResponse({"historico": hist})


@router.post("/painel/compras/add")
def compras_add(request: Request, descricao: str = Form(...)):
    conta, lista = _lista_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    # permite adicionar varios de uma vez: "arroz, cafe, leite" -> 3 itens
    import re as _re
    partes = [p.strip() for p in _re.split(r"[,;\n]+", descricao) if p.strip()]
    if len(partes) > 1:
        lista.adicionar_varios(partes)
    elif partes:
        lista.adicionar(partes[0])
    return RedirectResponse("/painel/compras", status_code=303)


@router.post("/painel/compras/marcar")
def compras_marcar(request: Request, item_id: int = Form(...), comprado: int = Form(1)):
    conta, lista = _lista_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    lista.marcar_comprado(item_id, bool(comprado))
    return RedirectResponse("/painel/compras", status_code=303)


@router.post("/painel/compras/remover")
def compras_remover(request: Request, item_id: int = Form(...)):
    conta, lista = _lista_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    lista.remover(item_id)
    return RedirectResponse("/painel/compras", status_code=303)


@router.post("/painel/compras/limpar")
def compras_limpar(request: Request):
    conta, lista = _lista_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    lista.limpar_comprados()
    return RedirectResponse("/painel/compras", status_code=303)


@router.post("/painel/compras/avisar")
def compras_avisar(request: Request):
    """Pessoa (qualquer uma menos o dono) avisa que terminou a lista.
    Manda Telegram pro dono. Nao trava a lista."""
    conta, lista = _lista_logada(request)
    if conta is None:
        return JSONResponse({"erro": "nao logado"}, status_code=401)
    pool = get_pool()
    with pool.connection() as c:
        m = c.execute(
            "select nome, papel from membros where conta_id=%s and id = "
            "(select id from membros where conta_id=%s order by id limit 1)",
            (conta[0], conta[0])).fetchone()
    quem_nome = (m[0] if m else None) or conta[2] or "Alguem"
    papel = m[1] if m else "membro"
    if papel == "dono":
        return JSONResponse({"ok": False, "msg": "Voce e' o dono - o aviso e' pra voce mesmo."})
    n = lista.resumo().get("pendentes", 0)
    from finance.notificar import avisar_dono_lista_fechada
    ok = avisar_dono_lista_fechada(pool, conta[0], quem_nome, n_itens=n)
    if ok:
        return JSONResponse({"ok": True, "msg": "Pronto! Avisei o responsavel. ✅"})
    return JSONResponse({"ok": True, "msg": "Lista marcada como pronta! (o aviso por Telegram sai quando o responsavel conectar)"})


@router.post("/painel/lancamento/categoria")
def mudar_categoria_lancamento(request: Request,
                               lancamento_id: int = Form(...),
                               categoria: str = Form(...)):
    conta = conta_logada(request)
    if not conta:
        return JSONResponse({"ok": False}, status_code=401)
    from finance.livro_caixa import LivroCaixa
    livro = LivroCaixa(get_pool(), conta[0])
    ok = livro.mudar_categoria(lancamento_id, categoria)
    return JSONResponse({"ok": bool(ok)})


@router.post("/painel/lancamento/natureza")
def marcar_natureza_lancamento(request: Request,
                               lancamento_id: int = Form(...),
                               natureza: str = Form(...)):
    conta = conta_logada(request)
    if not conta:
        return JSONResponse({"ok": False}, status_code=401)
    from finance.livro_caixa import LivroCaixa
    livro = LivroCaixa(get_pool(), conta[0])
    nat = natureza if natureza in ("pessoal", "empresa") else None
    ok = livro.marcar_natureza(lancamento_id, nat)
    return JSONResponse({"ok": bool(ok)})


@router.get("/painel/lancamentos-a-definir")
def listar_a_definir(request: Request, mes: str = "", membro: str = ""):
    conta = conta_logada(request)
    if not conta:
        return JSONResponse({"ok": False}, status_code=401)
    from finance.livro_caixa import LivroCaixa
    from datetime import date as _date
    hoje = _date.today()
    try:
        ano_sel, mes_num = (int(x) for x in mes.split("-")) if mes else (hoje.year, hoje.month)
    except ValueError:
        ano_sel, mes_num = hoje.year, hoje.month
    membro_sel = int(membro) if membro.isdigit() else None
    livro = LivroCaixa(get_pool(), conta[0])
    itens = livro.lancamentos_a_definir(ano_sel, mes_num, membro_sel)
    out = [{"id": i["id"], "data": i["data"].strftime("%d/%m") if hasattr(i["data"], "strftime") else str(i["data"]),
            "categoria": i["categoria"], "valor": i["valor"], "tipo": i["tipo"],
            "descricao": i["descricao"] or ""} for i in itens]
    return JSONResponse({"ok": True, "itens": out})


@router.post("/painel/lancamentos-classificar")
async def classificar_a_definir(request: Request):
    conta = conta_logada(request)
    if not conta:
        return JSONResponse({"ok": False}, status_code=401)
    from finance.livro_caixa import LivroCaixa
    dados = await request.json()
    mapa = dados.get("mapa") or {}
    livro = LivroCaixa(get_pool(), conta[0])
    n = livro.marcar_natureza_mapa(mapa)
    return JSONResponse({"ok": True, "n": n})


@router.post("/painel/lancamento/apagar")
def apagar_lancamento_endpoint(request: Request, lancamento_id: int = Form(...)):
    conta = conta_logada(request)
    if not conta:
        return JSONResponse({"ok": False}, status_code=401)
    from finance.livro_caixa import LivroCaixa
    livro = LivroCaixa(get_pool(), conta[0])
    ok = livro.apagar_lancamento(lancamento_id)
    return JSONResponse({"ok": bool(ok)})


@router.post("/membros/adicionar")
def membros_adicionar(request: Request, nome: str = Form(...), whatsapp: str = Form(""),
                      papel: str = Form("membro")):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    ativos, inclusos, pode_extra = _limite_membros(conta)
    if not pode_extra and ativos >= inclusos:
        request.session["erro"] = "Limite de pessoas do plano atingido."
        return RedirectResponse("/painel", status_code=303)
    papel = papel if papel in ("membro", "restrito") else "membro"
    nome = nome.strip()

    zap = _normalizar_zap(whatsapp) if whatsapp.strip() else None
    if zap:
        with pool.connection() as c:
            ja = c.execute("select 1 from membros where whatsapp_id=%s", (zap,)).fetchone()
        if ja:
            request.session["erro"] = "Esse WhatsApp ja esta cadastrado."
            return RedirectResponse("/painel", status_code=303)

    # cria UM membro com codigo de convite (e whatsapp, se informado)
    # criar_convite foi removido; agora é adicionar_membro + gerar_convite_para
    membro_id = ct.adicionar_membro(pool, conta[0], nome=nome, papel=papel, whatsapp_id=zap)
    codigo = ct.gerar_convite_para(pool, membro_id, conta[0])
    request.session["aviso"] = (
        f"{nome} adicionado(a)! Código de convite do Telegram: {codigo} — "
        f"peça pra essa pessoa abrir o bot ClawIAOpen e enviar esse código."
        + (" (ou já pode usar pelo WhatsApp cadastrado)" if zap else ""))
    return RedirectResponse("/painel", status_code=303)


@router.post("/membros/convite")
def membros_convite(request: Request, membro_id: int = Form(...)):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    codigo = ct.gerar_convite_para(get_pool(), membro_id, conta[0])
    if codigo:
        request.session["aviso"] = (
            f"Código de convite gerado: {codigo} — peça pra pessoa abrir o bot "
            f"ClawIAOpen no Telegram e enviar esse código.")
    else:
        request.session["erro"] = "Não foi possível gerar o convite para essa pessoa."
    return RedirectResponse("/painel", status_code=303)


@router.post("/membros/reconectar")
def membros_reconectar(request: Request, membro_id: int = Form(...)):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    codigo = ct.regerar_acesso(get_pool(), membro_id, conta[0])
    if codigo:
        request.session["aviso"] = (
            f"Código de reconexão gerado: {codigo} — mande este código pro bot "
            f"no Telegram ou WhatsApp do número novo. Pronto, você conecta!")
    else:
        request.session["erro"] = "Não foi possível gerar o código de reconexão."
    return RedirectResponse("/painel", status_code=303)


@router.post("/membros/reativar")
def membros_reativar(request: Request, membro_id: int = Form(...)):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if ct.reativar_membro(get_pool(), membro_id, conta[0]):
        request.session["aviso"] = "Pessoa reativada."
    else:
        request.session["erro"] = "Não foi possível reativar."
    return RedirectResponse("/painel", status_code=303)


@router.post("/membros/desativar")
def membros_desativar(request: Request, membro_id: int = Form(...)):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        ok = c.execute(
            "select 1 from membros where id=%s and conta_id=%s and papel <> 'dono'",
            (membro_id, conta[0]),
        ).fetchone()
    if not ok:
        request.session["erro"] = "Nao foi possivel desativar essa pessoa."
        return RedirectResponse("/painel", status_code=303)
    ct.desativar_membro(pool, membro_id)
    request.session["aviso"] = "Pessoa desativada (o historico dela fica preservado)."
    return RedirectResponse("/painel", status_code=303)


@router.get("/senha", response_class=HTMLResponse)
def senha_form(request: Request):
    if not request.session.get("conta_id"):
        return RedirectResponse("/login", status_code=303)
    return _render("senha", request, erro=None, ok=None, titulo="Alterar senha")


@router.post("/senha", response_class=HTMLResponse)
def senha_envia(request: Request, atual: str = Form(...), nova: str = Form(...)):
    cid = request.session.get("conta_id")
    if not cid:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        row = c.execute("select senha_hash from contas where id=%s", (cid,)).fetchone()
    if not row or not verificar_senha(atual, row[0]):
        return _render("senha", request, erro="Senha atual incorreta.", ok=None, titulo="Alterar senha")
    with pool.connection() as c:
        c.execute("update contas set senha_hash=%s where id=%s", (hash_senha(nova), cid))
        c.commit()
    ct.registrar_evento(pool, cid, "senha_alterada", "via portal")
    return _render("senha", request, erro=None, ok="Senha alterada com sucesso!", titulo="Alterar senha")
