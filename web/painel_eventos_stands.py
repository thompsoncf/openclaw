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


@router.get("/painel/eventos/estandes", response_class=HTMLResponse)
def painel_eventos_stands(request: Request):
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    cfg = cfg_ou_redir
    pool = get_pool()
    stands = es.listar(pool, conta[0])

    kpis = {"total": len(stands), "livre": 0, "pre_reservado": 0, "vendido": 0,
            "faturamento_centavos": 0}
    por_pavilhao: dict = {}
    pendentes = []
    for s in stands:
        kpis[s["status"]] = kpis.get(s["status"], 0) + 1
        if s["status"] == "vendido":
            kpis["faturamento_centavos"] += int(s["preco_centavos"] or 0)
        pav = por_pavilhao.setdefault(s["pavilhao"], {"total": 0, "livre": 0,
                                                       "pre_reservado": 0, "vendido": 0})
        pav["total"] += 1
        pav[s["status"]] += 1
        # a fila que importa: pré-reservado COM comprovante já anexado é
        # trabalho esperando o dono — sem comprovante é só a rede de segurança
        # correndo (ninguém mandou nada ainda, não há o que conferir).
        if s["status"] == "pre_reservado" and s["comprovante_url"]:
            pendentes.append(s)
    pendentes.sort(key=lambda s: s["pre_reserva_ate"] or "")

    return _render(
        "estandes", request, titulo="Estandes", secao_ativa="estandes", brl=brl,
        cfg=cfg, kpis=kpis, por_pavilhao=por_pavilhao, pendentes=pendentes,
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
# web/portal.py) — não uma página solta. Convenção de nome de classe (`es-`) e
# tokens de cor (--card, --borda, --txt-mut, --verde, --ambar-*) copiados de
# painel_obras.py de propósito: é a MESMA gramática visual do resto do
# painel, pra esta aba não parecer de outro produto.
# ─────────────────────────────────────────────────────────────────────────
_CSS = r"""<style>
.es-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.es-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.es-topo h2{margin:0;font-size:1.5rem;line-height:1.15}
.es-sub{color:var(--txt-mut);font-size:.88rem;margin-top:.25rem}
.es-sub a{color:var(--verde-claro)}
.es-msg{border-radius:9px;padding:.55rem .75rem;margin:.8rem 0;font-size:.86rem}
.es-msg.ok{background:var(--verde-fundo,rgba(70,166,121,.12));border:1px solid var(--verde)}
.es-msg.erro{background:var(--neon-fundo);border:1px solid var(--neon-borda)}
.es-faixas{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem;margin:1rem 0}
.es-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.55rem .75rem}
.es-cx .r{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut);display:block}
.es-cx .v{font-size:1.3rem;font-weight:700;line-height:1.2;display:block}
.es-cx.livre .v{color:var(--verde-claro)}
.es-cx.pre .v{color:#F0DCA6}
.es-sec{margin:1.6rem 0 .6rem;font-size:1.05rem}
.es-pavs{display:flex;flex-direction:column;gap:.5rem}
.es-pav{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.6rem .85rem}
.es-pav .l{display:flex;justify-content:space-between;font-size:.82rem;font-weight:600;margin-bottom:.35rem;text-transform:capitalize}
.es-pav .n{font-weight:400;color:var(--txt-mut);text-transform:none}
.es-bar{height:8px;background:var(--borda);border-radius:5px;overflow:hidden;display:flex}
.es-bar i{display:block;height:100%}
.es-bar i.vendido{background:var(--txt-mut)}
.es-bar i.pre{background:#D9932B}
.es-bar i.livre{background:var(--verde)}
.es-lista{display:flex;flex-direction:column;gap:.45rem}
.es-card{display:grid;grid-template-columns:1.4fr 1fr 1fr auto;gap:.8rem;align-items:center;background:var(--card);
  border:1px solid var(--borda);border-radius:11px;padding:.65rem .85rem}
.es-card .cod{font-family:var(--mono,monospace);font-weight:700;font-size:.95rem}
.es-mut{color:var(--txt-mut);font-size:.78rem}
.es-acoes{display:flex;gap:.35rem;flex-wrap:wrap;justify-content:flex-end}
.es-bt{padding:.35rem .7rem;border-radius:7px;border:1px solid var(--borda);background:transparent;color:inherit;
  cursor:pointer;font-size:.8rem;text-decoration:none;display:inline-block}
.es-bt.prim{background:var(--verde);border-color:var(--verde);color:#fff}
.es-bt.ambar{border-color:var(--ambar-borda);color:#F0DCA6}
.es-vazio{color:var(--txt-mut);font-size:.86rem;padding:.6rem 0}
@media (max-width:760px){.es-card{grid-template-columns:1fr 1fr}.es-acoes{grid-column:1/-1;justify-content:flex-start}}
</style>"""

_TPL = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="es-pag">
<div class="es-topo"><div><h2>Estandes</h2>
  <div class="es-sub">{{ cfg.edicao_label or cfg.slug }} · página pública:
    <a href="/e/{{ cfg.slug }}" target="_blank">/e/{{ cfg.slug }}</a></div></div></div>

{% if ok %}<div class="es-msg ok">{{ ok|e }}</div>{% endif %}
{% if erro %}<div class="es-msg erro">{{ erro|e }}</div>{% endif %}

<div class="es-faixas">
  <div class="es-cx"><span class="r">Total</span><span class="v">{{ kpis.total }}</span></div>
  <div class="es-cx livre"><span class="r">Livres</span><span class="v">{{ kpis.get('livre',0) }}</span></div>
  <div class="es-cx pre"><span class="r">Pré-reservados</span><span class="v">{{ kpis.get('pre_reservado',0) }}</span></div>
  <div class="es-cx"><span class="r">Vendidos</span><span class="v">{{ kpis.get('vendido',0) }}</span></div>
  <div class="es-cx"><span class="r">Faturamento</span><span class="v">{{ brl(kpis.faturamento_centavos) }}</span></div>
</div>

<h3 class="es-sec">Ocupação por pavilhão</h3>
<div class="es-pavs">
{% for pav, n in por_pavilhao.items() %}
<div class="es-pav">
  <div class="l">{{ pav|replace('_',' ') }}
    <span class="n">{{ n.get('vendido',0) }} vendidos · {{ n.get('pre_reservado',0) }} pré-reservados · {{ n.get('livre',0) }} livres de {{ n.total }}</span></div>
  <div class="es-bar">
    <i class="vendido" style="width:{{ (n.get('vendido',0)/n.total*100) if n.total else 0 }}%"></i>
    <i class="pre" style="width:{{ (n.get('pre_reservado',0)/n.total*100) if n.total else 0 }}%"></i>
    <i class="livre" style="width:{{ (n.get('livre',0)/n.total*100) if n.total else 0 }}%"></i>
  </div>
</div>
{% endfor %}
</div>

<h3 class="es-sec">Comprovante pendente de confirmação{% if pendentes %} · {{ pendentes|length }}{% endif %}</h3>
{% if not pendentes %}<p class="es-vazio">Nada esperando confirmação agora.</p>{% endif %}
<div class="es-lista">
{% for s in pendentes %}
<div class="es-card">
  <div><span class="cod">{{ s.codigo }}</span>
    <div class="es-mut">{{ s.pavilhao|replace('_',' ') }}{% if s.zona %} · {{ s.zona }}{% endif %} · {{ s.tamanho }}</div></div>
  <div>{% if s.preco_centavos %}<b>{{ brl(s.preco_centavos) }}</b>{% endif %}</div>
  <div class="es-mut">{% if s.pre_reserva_ate %}reserva vence {{ s.pre_reserva_ate.strftime('%d/%m %H:%M') }}{% endif %}</div>
  <div class="es-acoes">
    <a class="es-bt" href="/painel/eventos/estandes/{{ s.codigo }}/comprovante" target="_blank">Ver comprovante</a>
    {% if s.orcamento_id %}<a class="es-bt" href="/painel/servicos?ab={{ s.orcamento_id }}">Ver proposta</a>{% endif %}
    <form method="post" action="/painel/eventos/estandes/{{ s.codigo }}/confirmar" style="display:inline">
      <button class="es-bt prim" type="submit">Confirmar pagamento</button>
    </form>
    <form method="post" action="/painel/eventos/estandes/{{ s.codigo }}/liberar" style="display:inline"
          onsubmit="return confirm('Liberar o estande {{ s.codigo }} de volta pra livre?')">
      <button class="es-bt ambar" type="submit">Liberar</button>
    </form>
  </div>
</div>
{% endfor %}
</div>
</div>
{% endblock %}"""

_env.loader.mapping["estandes"] = _TPL
