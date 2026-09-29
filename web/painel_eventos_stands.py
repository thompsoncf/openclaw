"""A aba Estandes: /painel/eventos/estandes — o mapa de venda por trás do
/e/<slug> público (migração 448, finance/evento_stands.py).

ONDE ENCAIXAR (decisão de projeto, 28/09/2026): painel DEDICADO, e não uma aba
dentro de web/painel_servicos.py. Comparado o padrão recente (painel_obras.py,
painel_clinica_numeros.py — tela própria por assunto, gate por nicho/feature)
contra o padrão antigo (painel_servicos.py, funil de 3 abas dentro de uma tela
só): estande de feira não é uma PROPOSTA em pipeline (sumiu_apos_proposta,
com_o_cliente...) — é uma UNIDADE FÍSICA com posição na planta, ocupação e
prazo de reserva, muito mais perto do que painel_obras já faz pra casa/reforma
(KPIs + lista de pendência de confirmação) do que do funil de vendas. Um dia,
se a Outlet Chic também vender por proposta consultiva, a proposta dela
aparece no painel_servicos normal — as duas coisas não competem.

USA O MESMO CASCO DE TODA TELA DO PAINEL (base.html via _render — o mesmo
`extends "base"` de painel_obras.py), e não uma página HTML solta: a versão
anterior desta tela desenhava `<html>` própria, sem menu nem cabeçalho — não
aparecia em lugar nenhum de dentro do produto e não tinha como chegar nela sem
colar a URL (achado do dono, 29/09/2026, revisando o PR #919 já mergeado). O
item de menu correspondente mora em web/portal.py (`tem_estandes` em
`_render()`, os dois `navi('estandes', ...)` perto de Obras).

O CONTEÚDO é o port fiel da "Visão do Gestor" da maquete aprovada
(scratchpad/outlet-chic-mockup.html, v11 — que já usava os tokens reais de
web/tema.py de propósito): linha de KPIs, barras de ocupação por espaço e o
funil de propostas em 3 abas (Precisa de mim / Com o cliente / Fechada) com
linha expansível — badge do stand na cor do tamanho, sub-abas de comprovante,
contrato e cliente (achado do dono, 29/09/2026: "no painel com aba da lista
não está fiel"). A diferença pra maquete é só a fonte dos dados: status vem de
evento_stands, o interessado vem de prospeccao, e as ações (confirmar/liberar/
ver comprovante) são os POSTs reais abaixo, não stubs.

GATE (opt-in, igual ao resto do módulo): nicho 'eventos' E a conta ter
`evento_stands_config` — nem toda conta de eventos vende estande numerado
(Prime Eventos, conta 34, não vende).

QUEM VÊ: dono e gestor — confirmar pagamento é decisão de quem administra a
venda, mesmo corte de painel_obras (financeiro entra lá porque obra é custo;
aqui é receita e ainda não existe um terceiro papel dedicado a vendas de
estande, então fica com quem sempre decidiu preço/venda no eventos).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from db.conexao import get_pool
from finance import comprovantes as comprov
from finance import evento_stands as es
from web.portal import _env, _render, brl, conta_logada, nicho_da_conta

router = APIRouter()
_log = logging.getLogger("openclaw.painel_eventos_stands")

_PAPEIS_OK = ("dono", "gestor")

# Cores por tamanho — a MESMA paleta da planta oficial do PDF que a página
# pública usa na legenda "cor por tamanho" (e que a maquete usava no badge).
_COR_TAM = {
    "4x2": ("#FF4FA3", "#360019"), "4x3": ("#4C8DFF", "#04143B"),
    "3x2": ("#2BD4E0", "#022B2E"), "2x2": ("#B073FF", "#1D0940"),
    "3x3": ("#FF9A3C", "#3A1900"), "tenda": ("#FFDE2E", "#241C00"),
    "personalizado": ("#6B5CFF", "#0D0836"),
}
_TAM_LABEL = {"4x2": "4x2m", "4x3": "4x3m", "3x2": "3x2m", "2x2": "2x2m",
              "3x3": "3x3m", "tenda": "Tenda", "personalizado": "Custom"}
_MES = ["jan", "fev", "mar", "abr", "mai", "jun",
        "jul", "ago", "set", "out", "nov", "dez"]


def _data_curta(dt) -> str:
    return f"{dt.day} {_MES[dt.month - 1]}" if dt else "—"


def _acesso(request: Request):
    """(conta, config) ou (None, redirect) — mesma dupla checagem de nicho +
    feature ligada que o resto do painel usa."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if request.session.get("papel", "dono") not in _PAPEIS_OK:
        return None, RedirectResponse("/painel", status_code=303)
    if nicho_da_conta(conta) != "eventos":
        return None, RedirectResponse("/painel", status_code=303)
    pool = get_pool()
    cfg = es.obter_config(pool, conta[0])
    if cfg is None:
        return None, RedirectResponse("/painel", status_code=303)
    return conta, cfg


def _prospeccoes(pool, conta_id: int, ids: list[int]) -> dict[int, dict]:
    """empresa/whatsapp dos interessados, num SELECT só — é o nome que aparece
    na linha do funil no lugar do 'expositor' fake da maquete."""
    if not ids:
        return {}
    with pool.connection() as c:
        rows = c.execute(
            """select id, empresa, whatsapp, criado_em from prospeccao
               where conta_id=%s and id = any(%s)""", (conta_id, ids)).fetchall()
    return {r[0]: {"empresa": r[1], "whatsapp": r[2], "criado_em": r[3]} for r in rows}


@router.get("/painel/eventos/estandes", response_class=HTMLResponse)
def painel_eventos_stands(request: Request):
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    cfg = cfg_ou_redir
    pool = get_pool()
    stands = es.listar(pool, conta[0])

    kpis = {"total": len(stands), "livre": 0, "pre_reservado": 0, "vendido": 0,
            "faturamento_centavos": 0, "a_confirmar_centavos": 0}
    por_pavilhao: dict = {}
    for s in stands:
        kpis[s["status"]] = kpis.get(s["status"], 0) + 1
        if s["status"] == "vendido":
            kpis["faturamento_centavos"] += int(s["preco_centavos"] or 0)
        elif s["status"] == "pre_reservado":
            kpis["a_confirmar_centavos"] += int(s["preco_centavos"] or 0)
        pav = por_pavilhao.setdefault(s["pavilhao"], {"total": 0, "livre": 0,
                                                       "pre_reservado": 0, "vendido": 0})
        pav["total"] += 1
        pav[s["status"]] += 1

    # O funil de 3 abas da maquete, com o mapeamento REAL de cada grupo:
    # - precisa_de_mim: pré-reservado (comprovante chegou, sinal esperando o
    #   dono conferir — é a fila de trabalho)
    # - com_o_cliente: vendido COM proposta vinculada (sinal confirmado, o
    #   resto do plano — parcelas, contrato — mora na proposta)
    # - fechada: vendido sem pendência de proposta (venda direta pela página)
    interessados = _prospeccoes(pool, conta[0],
                                [s["prospeccao_id"] for s in stands if s["prospeccao_id"]])
    funil = {"precisa_de_mim": [], "com_o_cliente": [], "fechada": []}
    for s in stands:
        if s["status"] == "livre":
            continue
        cli = interessados.get(s["prospeccao_id"]) or {}
        item = dict(s)
        item["cliente"] = cli
        if s["status"] == "pre_reservado":
            item["resumo"] = "Comprovante recebido · sinal aguardando confirmação"
            item["pend"] = [("Confirmar sinal", "coral")]
            funil["precisa_de_mim"].append(item)
        elif s["orcamento_id"]:
            item["resumo"] = "Sinal confirmado · parcelas e contrato na proposta"
            item["pend"] = [("Acompanhar proposta", "azul")]
            funil["com_o_cliente"].append(item)
        else:
            item["resumo"] = "Pagamento confirmado · stand vendido"
            item["pend"] = []
            funil["fechada"].append(item)
    funil["precisa_de_mim"].sort(key=lambda s: s["pre_reserva_ate"] or "")

    return _render(
        "estandes", request, titulo="Estandes", secao_ativa="estandes", brl=brl,
        cfg=cfg, kpis=kpis, por_pavilhao=por_pavilhao, funil=funil,
        cor_tam=_COR_TAM, tam_label=_TAM_LABEL, data_curta=_data_curta,
        erro=(request.query_params.get("erro") or "").strip(),
        ok=(request.query_params.get("ok") or "").strip())


@router.post("/painel/eventos/estandes/{codigo}/confirmar")
def confirmar(request: Request, codigo: str):
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    membro_id = request.session.get("membro_id")
    r = es.confirmar_pagamento(get_pool(), conta[0], codigo, membro_id=membro_id)
    if not r["ok"]:
        return RedirectResponse(f"/painel/eventos/estandes?erro={r['erro']}", status_code=303)
    return RedirectResponse(f"/painel/eventos/estandes?ok=Estande {codigo} confirmado.",
                            status_code=303)


@router.post("/painel/eventos/estandes/{codigo}/liberar")
def liberar(request: Request, codigo: str):
    """Devolve pro mapa público — desistência do interessado, ou pra corrigir
    um clique errado. Ação de negócio simples o bastante pra não precisar de
    tela própria (mesmo corte de excluir/cancelar em painel_obras)."""
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    ok = es.liberar(get_pool(), conta[0], codigo)
    msg = f"Estande {codigo} liberado." if ok else f"Estande {codigo} já estava livre."
    return RedirectResponse(f"/painel/eventos/estandes?ok={msg}", status_code=303)


@router.get("/painel/eventos/estandes/{codigo}/comprovante")
def ver_comprovante(request: Request, codigo: str):
    """Entrega o arquivo do bucket PRIVADO — mesmo desenho de
    painel_servicos_comprovante_ver: é esta rota (com a sessão da conta
    conferida) que permite o bucket continuar privado. O `conta_id` no WHERE
    (dentro de `es.buscar`) é o que impede ler o comprovante de outra conta
    trocando o código na URL."""
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    s = es.buscar(get_pool(), conta[0], codigo)
    if not s or not s["comprovante_url"]:
        return JSONResponse({"erro": "sem comprovante"}, status_code=404)
    try:
        conteudo, tipo = comprov.ler(s["comprovante_url"])
    except ValueError as e:
        return JSONResponse({"erro": str(e)}, status_code=502)
    return Response(conteudo, media_type=tipo, headers={
        "Content-Disposition": f'inline; filename="comprovante-{codigo}"',
        "Cache-Control": "no-store"})


# ─────────────────────────────────────────────────────────────────────────
# Mesmo casco de toda tela do painel (`{% extends "base" %}` + o `_render` de
# web/portal.py) — não uma página solta.
#
# O CSS dos componentes (kpi, occ-*, fn-tab, oc-*) é o da maquete aprovada
# QUASE VERBATIM, escopado em .es-pag. As variáveis --mint/--gold/--coral são
# redefinidas AQUI com os valores reais de web/tema.py (os mesmos que a
# maquete v11 já usava no bloco #view-gestor) — redefinir em vez de apontar
# pra var(--card) evita ciclo de custom property (--card já é var(--surface)
# no :root do tema).
# ─────────────────────────────────────────────────────────────────────────
_CSS = r"""<style>
.es-pag{
  width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box;
  --surface:#121A16; --surface-2:#16201B; --line:#1E2A23;
  --fg:#EAF2ED; --fg-dim:#8FA197;
  --mint:#25D366; --mint-strong:#1FB859; --mint-fg:#04150C;
  --gold:#E0A32E; --gold-strong:#F0C070; --gold-fg:#2B1D00;
  --coral:#E0574F; --coral-strong:#B84440; --coral-fg:#2B0F09;
  --shadow:0 1px 2px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.35);
}
/* O atributo `hidden` perde pra display:flex/grid de regra de autor — sem
   isto, trocar de aba do funil não escondia linha nenhuma. E o base do painel
   alarga <button> pra 100%; os componentes da maquete são pílulas inline. */
.es-pag [hidden]{display:none !important}
.es-pag .fn-tab,.es-pag .oc-subtab,.es-pag .oc-expand-btn,.es-pag .oc-ghost-btn{width:auto;min-height:0;margin:0}
.es-pag .oc-expand-btn{padding:0}
.es-pag .oc-expand-btn svg{display:block;flex:0 0 auto}
.es-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.es-topo h2{margin:0;font-size:1.5rem;line-height:1.15;font-family:"Bricolage Grotesque",sans-serif;font-weight:800}
.es-sub{color:var(--fg-dim);font-size:.88rem;margin-top:.25rem}
.es-sub a{color:var(--mint)}
.es-msg{border-radius:9px;padding:.55rem .75rem;margin:.8rem 0;font-size:.86rem}
.es-msg.ok{background:var(--neon-fundo,rgba(37,211,102,.1));border:1px solid var(--mint)}
.es-msg.erro{background:var(--coral-fundo,rgba(224,87,79,.12));border:1px solid var(--coral)}
.es-sec{margin:1.6rem 0 4px;font-size:1.02rem;font-family:"Bricolage Grotesque",sans-serif;font-weight:800}
.es-sec-sub{margin:0 0 14px;color:var(--fg-dim);font-size:.84rem}

/* ---- KPIs (maquete: .kpi-row/.kpi) ---- */
.es-pag .kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:1rem 0 18px}
.es-pag .kpi{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow)}
.es-pag .kpi span{display:block;font-size:11.5px;color:var(--fg-dim);font-weight:600;text-transform:uppercase;letter-spacing:0.03em}
.es-pag .kpi b{display:block;font-family:var(--mono,monospace);font-size:22px;margin-top:4px}

/* ---- ocupação (maquete: .occ-block/.occ-row/.occ-bar) ---- */
.es-pag .occ-block{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:18px;box-shadow:var(--shadow)}
.es-pag .occ-block h3{margin:0 0 12px;font-size:14px}
.es-pag .occ-row{margin-bottom:12px}
.es-pag .occ-row:last-child{margin-bottom:0}
.es-pag .occ-label{display:flex;justify-content:space-between;font-size:12.5px;font-weight:700;margin-bottom:6px;gap:10px;text-transform:capitalize}
.es-pag .occ-label span:last-child{color:var(--fg-dim);font-weight:400;text-transform:none}
.es-pag .occ-bar{display:flex;height:10px;border-radius:6px;overflow:hidden;background:var(--surface-2)}
.es-pag .occ-bar span{height:100%}

/* ---- funil de propostas (maquete: .fn-tabs/.oc-*) ---- */
.es-pag .fn-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.es-pag .fn-tab{
  appearance:none;cursor:pointer;font-family:inherit;font-weight:700;font-size:12.5px;
  display:inline-flex;align-items:center;gap:7px;padding:8px 13px;border-radius:999px;
  border:1px solid var(--line);background:var(--surface);color:var(--fg-dim);
}
.es-pag .fn-tab .pt{width:8px;height:8px;border-radius:50%;flex:0 0 auto}
.es-pag .fn-tab .n{font-family:var(--mono,monospace);color:var(--fg-dim)}
.es-pag .fn-tab.on{background:var(--surface-2);color:var(--fg);border-color:var(--fg-dim)}

.es-pag .oc-list{display:flex;flex-direction:column;gap:8px}
.es-pag .oc-hist{
  display:flex;align-items:center;gap:12px;background:var(--surface);border:1px solid var(--line);
  border-radius:12px;padding:11px 13px;box-shadow:var(--shadow);flex-wrap:wrap;
}
.es-pag .oc-open{display:flex;align-items:center;gap:12px;flex:1 1 260px;min-width:0;cursor:pointer}
.es-pag .oc-stand-badge{
  flex:0 0 auto;width:44px;height:36px;border-radius:8px;display:flex;flex-direction:column;
  align-items:center;justify-content:center;font-family:var(--mono,monospace);
}
.es-pag .oc-stand-badge .c{font-size:11px;font-weight:800;line-height:1.1}
.es-pag .oc-stand-badge .z{font-size:7px;font-weight:600;opacity:0.85}
.es-pag .oc-body{min-width:0}
.es-pag .oc-body b{font-size:13.5px}
.es-pag .oc-sub{font-size:11.5px;color:var(--fg-dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.es-pag .oc-criada{flex:0 0 auto;text-align:center;padding:0 6px}
.es-pag .oc-criada .rot{font-size:9px;color:var(--fg-dim);text-transform:uppercase;letter-spacing:0.03em}
.es-pag .oc-criada .dt{font-size:12px;font-weight:700;font-family:var(--mono,monospace)}
.es-pag .oc-zap{
  flex:0 0 auto;width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;
  background:var(--surface-2);text-decoration:none;
}
.es-pag .oc-zap svg{width:16px;height:16px;color:var(--mint-strong)}
.es-pag .oc-acoes{display:flex;align-items:center;gap:8px;flex:0 0 auto;margin-left:auto}
.es-pag .oc-badge{font-size:10.5px;font-weight:700;padding:4px 9px;border-radius:999px;white-space:nowrap}
.es-pag .oc-badge.coral{background:color-mix(in srgb, var(--coral) 35%, var(--surface-2));color:var(--coral-strong)}
.es-pag .oc-badge.azul{background:color-mix(in srgb, #229ED9 25%, var(--surface-2));color:#8FD1F0}
.es-pag .oc-ok{font-size:11px;color:var(--mint-strong);font-weight:600}
.es-pag .oc-expand-btn{
  flex:0 0 auto;width:28px;height:28px;border-radius:8px;border:1px solid var(--line);background:var(--surface-2);
  color:var(--fg-dim);cursor:pointer;display:flex;align-items:center;justify-content:center;transition:transform .2s;
}
.es-pag .oc-expand-btn.on{transform:rotate(180deg);background:var(--mint);color:var(--mint-fg);border-color:var(--mint)}
.es-pag .oc-expand-btn svg{width:13px;height:13px}
.es-pag .oc-hist.expanded{flex-direction:column;align-items:stretch}
.es-pag .oc-hist-top{display:flex;align-items:center;gap:12px;width:100%;flex-wrap:wrap}

.es-pag .oc-detail{width:100%;margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}
.es-pag .oc-subtabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}
.es-pag .oc-subtab{
  appearance:none;cursor:pointer;font-family:inherit;font-weight:700;font-size:11.5px;
  padding:6px 12px;border-radius:8px;border:1px solid var(--line);background:var(--surface-2);color:var(--fg-dim);
}
.es-pag .oc-subtab.on{background:var(--mint);color:var(--mint-fg);border-color:var(--mint)}
.es-pag .oc-detail-body{font-size:12.5px;line-height:1.6}

.es-pag .oc-comprovante-item{display:flex;align-items:center;gap:10px;background:var(--surface-2);border-radius:8px;padding:9px 11px;margin-bottom:10px}
.es-pag .oc-comprovante-item .ic{width:30px;height:30px;border-radius:7px;background:var(--mint);color:var(--mint-fg);display:flex;align-items:center;justify-content:center;flex:0 0 auto;font-size:13px}
.es-pag .oc-comprovante-item .txt{flex:1;min-width:0}
.es-pag .oc-comprovante-item .txt b{display:block;font-size:12.5px}
.es-pag .oc-comprovante-item .txt span{font-size:10.5px;color:var(--fg-dim)}
.es-pag .oc-contract-status{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:700;padding:4px 10px;border-radius:999px;margin-bottom:12px}
.es-pag .oc-contract-status.pendente{background:color-mix(in srgb, var(--gold) 25%, var(--surface-2));color:var(--gold-strong)}
.es-pag .oc-contract-status.assinado{background:var(--mint);color:var(--mint-fg)}
.es-pag .oc-field-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px 16px;margin-bottom:12px}
.es-pag .oc-field span{display:block;font-size:10px;color:var(--fg-dim);text-transform:uppercase;letter-spacing:0.03em;margin-bottom:2px}
.es-pag .oc-field b{font-size:13px}
.es-pag .oc-ghost-btn{
  display:inline-flex;align-items:center;gap:6px;appearance:none;cursor:pointer;font-family:inherit;font-weight:700;
  font-size:12px;padding:8px 13px;border-radius:8px;border:1px solid var(--line);background:var(--surface-2);color:var(--fg);
  text-decoration:none;
}
.es-pag .oc-ghost-btn:hover{border-color:var(--mint);color:var(--mint-strong)}
.es-pag .oc-ghost-btn.prim{background:var(--mint);border-color:var(--mint);color:var(--mint-fg)}
.es-pag .oc-ghost-btn.prim:hover{background:var(--mint-strong);color:var(--mint-fg)}
.es-pag .oc-acoes-detail{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
.es-pag .oc-vazio{color:var(--fg-dim);font-size:13px;margin:.4rem 0}
</style>"""

_TPL = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="es-pag">
<div class="es-topo"><div><h2>Mapa de stands</h2>
  <div class="es-sub">{{ cfg.edicao_label or cfg.slug }} · página pública:
    <a href="/e/{{ cfg.slug }}" target="_blank">/e/{{ cfg.slug }}</a></div></div></div>

{% if ok %}<div class="es-msg ok">{{ ok|e }}</div>{% endif %}
{% if erro %}<div class="es-msg erro">{{ erro|e }}</div>{% endif %}

<div class="kpi-row">
  <div class="kpi"><span>Total de stands</span><b>{{ kpis.total }}</b></div>
  <div class="kpi"><span>Livres</span><b>{{ kpis.get('livre',0) }}</b></div>
  <div class="kpi"><span>Reservados</span><b>{{ kpis.get('pre_reservado',0) }}</b></div>
  <div class="kpi"><span>Vendidos</span><b>{{ kpis.get('vendido',0) }}</b></div>
  <div class="kpi"><span>Faturamento confirmado</span><b>{{ brl(kpis.faturamento_centavos) }}</b></div>
  <div class="kpi"><span>A confirmar (reservado)</span><b>{{ brl(kpis.a_confirmar_centavos) }}</b></div>
</div>

<div class="occ-block">
  <h3>Ocupação por espaço</h3>
  {% for pav, n in por_pavilhao.items() %}
  <div class="occ-row">
    <div class="occ-label"><span>{{ pav|replace('_',' ') }}</span>
      <span>{{ n.get('vendido',0) }} vendidos · {{ n.get('pre_reservado',0) }} reservados · {{ n.get('livre',0) }} livres de {{ n.total }}</span></div>
    <div class="occ-bar">
      <span style="width:{{ (n.get('vendido',0)/n.total*100) if n.total else 0 }}%;background:var(--coral)"></span>
      <span style="width:{{ (n.get('pre_reservado',0)/n.total*100) if n.total else 0 }}%;background:var(--gold)"></span>
      <span style="width:{{ (n.get('livre',0)/n.total*100) if n.total else 0 }}%;background:var(--mint)"></span>
    </div>
  </div>
  {% endfor %}
</div>

<h3 class="es-sec">Propostas — orçamento e contrato</h3>
<p class="es-sec-sub">Sinal, parcelas e contrato moram na proposta — o mapa só mostra pra onde ela aponta.</p>

<div class="fn-tabs">
  <button class="fn-tab on" data-grupo="precisa_de_mim" onclick="fnSel(this)"><span class="pt" style="background:var(--coral-strong)"></span>Precisa de mim <span class="n">{{ funil.precisa_de_mim|length }}</span></button>
  <button class="fn-tab" data-grupo="com_o_cliente" onclick="fnSel(this)"><span class="pt" style="background:#7FA8FF"></span>Com o cliente <span class="n">{{ funil.com_o_cliente|length }}</span></button>
  <button class="fn-tab" data-grupo="fechada" onclick="fnSel(this)"><span class="pt" style="background:var(--mint)"></span>Fechada <span class="n">{{ funil.fechada|length }}</span></button>
</div>

<div class="oc-list">
{% for grupo, itens in funil.items() %}
<p class="oc-vazio" data-grupo="{{ grupo }}" {% if itens or grupo != 'precisa_de_mim' %}hidden{% endif %}>✓ Nada por aqui nessa aba.</p>
{% for d in itens %}
{% set cor = cor_tam.get(d.tamanho, ('#8FA197','#0A0F0C')) %}
{% set pav_label = d.pavilhao|replace('_',' ')|title %}
<div class="oc-hist" data-grupo="{{ grupo }}" data-cod="{{ d.codigo }}" {% if grupo != 'precisa_de_mim' %}hidden{% endif %}>
  <div class="oc-hist-top">
    <div class="oc-open" title="Ver comprovante, contrato e cliente" onclick="ocToggle('{{ d.codigo }}')">
      <div class="oc-stand-badge" style="background:{{ cor[0] }};color:{{ cor[1] }}"><div class="c">{{ d.codigo }}</div><div class="z">{{ tam_label.get(d.tamanho, d.tamanho) }}</div></div>
      <div class="oc-body"><b>{{ d.cliente.get('empresa') or 'Interessado da página' }}</b>
        <div class="oc-sub">{% if d.zona and d.zona != pav_label %}{{ d.zona }} · {% endif %}{{ pav_label }}</div>
        <div class="oc-sub">{% if d.preco_centavos %}{{ brl(d.preco_centavos) }} · {% endif %}{{ d.resumo }}</div></div>
    </div>
    <div class="oc-criada"><div class="rot">Criada em</div><div class="dt">{{ data_curta(d.comprovante_em or d.cliente.get('criado_em')) }}</div></div>
    <div class="oc-acoes">
      {% if d.cliente.get('whatsapp') %}
      <a class="oc-zap" target="_blank" rel="noopener" title="Falar no WhatsApp" href="https://wa.me/{{ d.cliente.whatsapp|replace('+','')|replace(' ','')|replace('-','') }}?text=Olá! Sobre o stand {{ d.codigo }}...">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5.1-1.3A10 10 0 1 0 12 2Zm5.8 14.2c-.3.7-1.4 1.3-2 1.4-.5.1-1.1.2-3.6-.8-3-1.2-4.9-4.2-5.1-4.4-.1-.2-1.2-1.6-1.2-3s.7-2.1 1-2.4c.3-.3.6-.4.8-.4h.6c.2 0 .4 0 .6.5l.9 2.1c.1.2.1.4 0 .6l-.5.7c-.1.2-.2.3-.1.6.2.3.8 1.3 1.7 2.1 1.1 1 2.1 1.3 2.4 1.5.3.1.5.1.6-.1l.8-.9c.2-.3.4-.2.6-.1l1.9 1c.2.1.4.2.4.4.1.2.1.9-.2 1.6Z"/></svg>
      </a>
      {% endif %}
      {% for texto, tom in d.pend %}<span class="oc-badge {{ tom }}">{{ texto }}</span>{% endfor %}
      {% if not d.pend %}<span class="oc-ok">✓ nada pendente</span>{% endif %}
      <button class="oc-expand-btn" title="Abrir opções" onclick="ocToggle('{{ d.codigo }}')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
      </button>
    </div>
  </div>
  <div class="oc-detail" hidden>
    <div class="oc-subtabs">
      <button class="oc-subtab on" onclick="ocTab(this,'comprovante')">Comprovante</button>
      <button class="oc-subtab" onclick="ocTab(this,'contrato')">Abrir contrato</button>
      <button class="oc-subtab" onclick="ocTab(this,'cliente')">Dados do cliente</button>
    </div>
    <div class="oc-detail-body" data-tab="comprovante">
      {% if d.comprovante_url %}
      <div class="oc-comprovante-item"><div class="ic">✓</div><div class="txt"><b>Comprovante do sinal</b><span>enviado pela página{% if d.comprovante_em %} em {{ data_curta(d.comprovante_em) }}{% endif %}{% if d.preco_centavos %} · {{ brl(d.preco_centavos) }}{% endif %}</span></div></div>
      {% else %}
      <p class="oc-vazio">Nenhum arquivo anexado a este stand.</p>
      {% endif %}
      <div class="oc-acoes-detail">
        {% if d.comprovante_url %}<a class="oc-ghost-btn" href="/painel/eventos/estandes/{{ d.codigo }}/comprovante" target="_blank">Ver comprovante →</a>{% endif %}
        {% if grupo == 'precisa_de_mim' %}
        <form method="post" action="/painel/eventos/estandes/{{ d.codigo }}/confirmar" style="display:inline">
          <button class="oc-ghost-btn prim" type="submit">Confirmar pagamento</button>
        </form>
        <form method="post" action="/painel/eventos/estandes/{{ d.codigo }}/liberar" style="display:inline"
              onsubmit="return confirm('Liberar o estande {{ d.codigo }} de volta pra livre?')">
          <button class="oc-ghost-btn" type="submit">Liberar stand</button>
        </form>
        {% endif %}
      </div>
      {% if grupo == 'precisa_de_mim' and d.pre_reserva_ate %}
      <p class="oc-vazio" style="margin-top:10px">Reserva vence em {{ d.pre_reserva_ate.strftime('%d/%m às %H:%M') }} — depois disso o stand volta pro mapa sozinho.</p>
      {% endif %}
    </div>
    <div class="oc-detail-body" data-tab="contrato" hidden>
      {% if d.orcamento_id %}
      <span class="oc-contract-status assinado">✓ Proposta vinculada</span>
      <p style="margin:0 0 10px">Sinal, parcelas e contrato deste stand moram na proposta — abre lá pra ver assinatura e plano de pagamento.</p>
      <a class="oc-ghost-btn" href="/painel/servicos?ab={{ d.orcamento_id }}">Abrir proposta e contrato →</a>
      {% else %}
      <span class="oc-contract-status pendente">⏳ Sem proposta vinculada</span>
      <p style="margin:0">Venda direta pela página (Pix + comprovante). Se quiser contrato e parcelas, cria a proposta em Serviços e vincula o stand.</p>
      {% endif %}
    </div>
    <div class="oc-detail-body" data-tab="cliente" hidden>
      <div class="oc-field-grid">
        <div class="oc-field"><span>Empresa / expositor</span><b>{{ d.cliente.get('empresa') or '—' }}</b></div>
        <div class="oc-field"><span>WhatsApp</span><b>{{ d.cliente.get('whatsapp') or '—' }}</b></div>
        <div class="oc-field"><span>Origem</span><b>Página de stands</b></div>
        <div class="oc-field"><span>Stand</span><b>{{ d.codigo }} · {{ tam_label.get(d.tamanho, d.tamanho) }}</b></div>
      </div>
      {% if d.prospeccao_id %}<a class="oc-ghost-btn" href="/painel/prospeccao">Abrir no Funil →</a>
      {% else %}<p class="oc-vazio" style="margin:0">Este envio veio sem nome — o interessado não preencheu o cadastro.</p>{% endif %}
    </div>
  </div>
</div>
{% endfor %}
{% endfor %}
</div>
</div>

<script>
function fnSel(btn){
  var grupo = btn.dataset.grupo;
  document.querySelectorAll('.fn-tab').forEach(function(b){ b.classList.toggle('on', b === btn); });
  document.querySelectorAll('.oc-hist[data-grupo], .oc-vazio[data-grupo]').forEach(function(el){
    el.hidden = el.dataset.grupo !== grupo;
  });
}
function ocToggle(cod){
  var row = document.querySelector('.oc-hist[data-cod="' + cod + '"]');
  if (!row) return;
  var detail = row.querySelector('.oc-detail');
  var btn = row.querySelector('.oc-expand-btn');
  var aberto = !detail.hidden;
  detail.hidden = aberto;
  row.classList.toggle('expanded', !aberto);
  if (btn) btn.classList.toggle('on', !aberto);
}
function ocTab(btn, tab){
  var detail = btn.closest('.oc-detail');
  detail.querySelectorAll('.oc-subtab').forEach(function(b){ b.classList.toggle('on', b === btn); });
  detail.querySelectorAll('.oc-detail-body').forEach(function(el){ el.hidden = el.dataset.tab !== tab; });
}
</script>
{% endblock %}"""

_env.loader.mapping["estandes"] = _TPL
