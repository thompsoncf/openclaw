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

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from db.conexao import get_pool
from finance import comprovantes as comprov
from finance import evento_stands as es
from web.portal import _env, brl, conta_logada, nicho_da_conta

router = APIRouter()
_log = logging.getLogger("openclaw.painel_eventos_stands")

_PAPEIS_OK = ("dono", "gestor")
_TPL_NOME = "painel_eventos_stands.html"


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

    html = _env.get_template(_TPL_NOME).render(
        cfg=cfg, kpis=kpis, por_pavilhao=por_pavilhao, pendentes=pendentes,
        brl=brl, erro=(request.query_params.get("erro") or "").strip(),
        ok=(request.query_params.get("ok") or "").strip())
    return HTMLResponse(html)


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
_TPL = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Estandes — {{ cfg.slug }}</title>
{% raw %}<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0E0E0E;color:#EDEDEA;margin:0;padding:20px 14px 60px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:#8A8A86;font-size:13px;margin-bottom:18px}
.msg{padding:10px 14px;border-radius:8px;font-size:13px;margin-bottom:14px}
.msg.ok{background:rgba(22,227,174,.12);border:1px solid #16E3AE;color:#16E3AE}
.msg.erro{background:rgba(255,222,46,.1);border:1px solid #FFDE2E;color:#FFDE2E}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:22px}
.kpi{background:#181818;border:1px solid #2A2A2A;border-radius:10px;padding:12px 14px}
.kpi .n{font-size:24px;font-weight:700}
.kpi .l{font-size:11px;color:#8A8A86;text-transform:uppercase;letter-spacing:.04em;margin-top:2px}
.kpi.livre .n{color:#16E3AE}
.kpi.pre .n{color:#FFDE2E}
.kpi.vendido .n{color:#9A9A96}
.kpi.fat .n{color:#16E3AE;font-size:19px}
h2{font-size:15px;margin:26px 0 10px;color:#EDEDEA}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:#8A8A86;font-weight:600;font-size:11px;text-transform:uppercase;padding:6px 8px;border-bottom:1px solid #2A2A2A}
td{padding:8px;border-bottom:1px solid #202020}
.pav-nome{text-transform:capitalize}
.pendente{background:#161616;border:1px solid #2A2A2A;border-radius:10px;padding:12px 14px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.pendente .cod{font-family:monospace;font-weight:700;font-size:15px}
.pendente .det{color:#8A8A86;font-size:12px;margin-top:2px}
.btns{display:flex;gap:8px;flex-wrap:wrap}
.btn{border:none;border-radius:7px;padding:8px 12px;font-size:12.5px;font-weight:600;cursor:pointer}
.btn.ok{background:#16E3AE;color:#04231B}
.btn.ghost{background:transparent;border:1px solid #3A3A3A;color:#EDEDEA;text-decoration:none;display:inline-block}
.btn.liberar{background:transparent;border:1px solid #FFDE2E;color:#FFDE2E}
.vazio{color:#8A8A86;font-size:13px;padding:16px 0}
form{display:inline}
</style>{% endraw %}
</head><body>
<h1>Estandes — {{ cfg.edicao_label or cfg.slug }}</h1>
<div class="sub">Página pública: <a href="/e/{{ cfg.slug }}" target="_blank" style="color:#16E3AE">/e/{{ cfg.slug }}</a></div>

{% if ok %}<div class="msg ok">{{ ok }}</div>{% endif %}
{% if erro %}<div class="msg erro">{{ erro }}</div>{% endif %}

<div class="kpis">
  <div class="kpi"><div class="n">{{ kpis.total }}</div><div class="l">Total</div></div>
  <div class="kpi livre"><div class="n">{{ kpis.get('livre',0) }}</div><div class="l">Livres</div></div>
  <div class="kpi pre"><div class="n">{{ kpis.get('pre_reservado',0) }}</div><div class="l">Pré-reservados</div></div>
  <div class="kpi vendido"><div class="n">{{ kpis.get('vendido',0) }}</div><div class="l">Vendidos</div></div>
  <div class="kpi fat"><div class="n">{{ brl(kpis.faturamento_centavos) }}</div><div class="l">Faturamento</div></div>
</div>

<h2>Ocupação por pavilhão</h2>
<table>
<tr><th>Pavilhão</th><th>Total</th><th>Livres</th><th>Pré-reservados</th><th>Vendidos</th></tr>
{% for pav, n in por_pavilhao.items() %}
<tr><td class="pav-nome">{{ pav|replace('_',' ') }}</td><td>{{ n.total }}</td>
    <td>{{ n.get('livre',0) }}</td><td>{{ n.get('pre_reservado',0) }}</td><td>{{ n.get('vendido',0) }}</td></tr>
{% endfor %}
</table>

<h2>Comprovante pendente de confirmação ({{ pendentes|length }})</h2>
{% if not pendentes %}<div class="vazio">Nada esperando confirmação agora.</div>{% endif %}
{% for s in pendentes %}
<div class="pendente">
  <div>
    <span class="cod">{{ s.codigo }}</span>
    <div class="det">{{ s.pavilhao|replace('_',' ') }}{% if s.zona %} · {{ s.zona }}{% endif %} · {{ s.tamanho }}
      {% if s.preco_centavos %} · {{ brl(s.preco_centavos) }}{% endif %}
      {% if s.pre_reserva_ate %} · reserva vence {{ s.pre_reserva_ate.strftime('%d/%m %H:%M') }}{% endif %}</div>
  </div>
  <div class="btns">
    <a class="btn ghost" href="/painel/eventos/estandes/{{ s.codigo }}/comprovante" target="_blank">Ver comprovante</a>
    {% if s.orcamento_id %}<a class="btn ghost" href="/painel/servicos?ab={{ s.orcamento_id }}">Ver proposta</a>{% endif %}
    <form method="post" action="/painel/eventos/estandes/{{ s.codigo }}/confirmar">
      <button class="btn ok" type="submit">Confirmar pagamento</button>
    </form>
    <form method="post" action="/painel/eventos/estandes/{{ s.codigo }}/liberar"
          onsubmit="return confirm('Liberar o estande {{ s.codigo }} de volta pra livre?')">
      <button class="btn liberar" type="submit">Liberar</button>
    </form>
  </div>
</div>
{% endfor %}
</body></html>
"""

_env.loader.mapping[_TPL_NOME] = _TPL
