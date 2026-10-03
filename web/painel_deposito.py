"""A aba "Depósito (CD)": /painel/obras/deposito — o almoxarifado central das obras.

PR 1 de 3 do desenho aprovado pelo dono em 03/10/2026
(docs/mockups/obras_cd_almoxarifado.html, "segue as recomendações"): a visão
geral, o quadro dos pedidos das obras (Pedido → Separando → Saiu → Recebido),
o estoque com a cobertura em dias, as entradas e a sobra das casas prontas.
Depois vieram as ferramentas (PR 2), a conferência da nota (PR 3a) e o
inventário rotativo pela curva ABC com os indicadores (PR 3b).

QUEM VÊ: quem tem a capacidade `deposito` (contas.equipe) — dono, gestor,
financeiro e o ALMOXARIFE, que vê só esta aba. O "dinheiro parado" (o valor do
estoque pelo preço da nota) é só de dono e gestor (decisão 3 do dono).

AS ROTAS ENTRAM ANTES das de web/painel_obras.py (web/app.py), como as do mapa:
/painel/obras/deposito bateria na ficha /painel/obras/{obra_id}.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from contas import equipe as eq
from db.conexao import get_pool
from finance import obra_conferencia as conf
from finance import obra_ferramentas as fer
from finance import obra_inventario as inv
from finance import obra_pedidos as op
from finance import obras as ob
from finance import raio_x_perfil as rxp
from web.portal import _env, _render, conta_logada, nicho_da_conta

router = APIRouter()
_log = logging.getLogger("openclaw.painel_deposito")
_BASE = "/painel/obras/deposito"
_ABAS = (("geral", "Visão geral"), ("pedidos", "Pedidos das obras"), ("estoque", "Estoque"),
         ("entradas", "Entradas"), ("ferramentas", "Ferramentas"),
         ("sobras", "Sobras das casas prontas"), ("inventario", "Inventário"))


def _acesso(request: Request):
    """(conta, papel) ou (None, redirect)."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    papel = request.session.get("papel", "dono")
    if not eq.caps_do_papel(papel).get("deposito"):
        return None, RedirectResponse(eq.destino_barrado(papel), status_code=303)
    if rxp.perfil_por_nicho(nicho_da_conta(conta)) != "obras":
        return None, RedirectResponse("/painel", status_code=303)
    return (conta, papel), None


def _rotulo_mat():
    from finance import obra_material as omat
    return omat.rotulo


def _volta(aba: str, ok: str = "", erro: str = "") -> RedirectResponse:
    url = f"{_BASE}?aba={aba}"
    if ok:
        url += "&ok=" + quote(ok)
    if erro:
        url += "&erro=" + quote(erro)
    return RedirectResponse(url, status_code=303)


@router.get(_BASE, response_class=HTMLResponse)
def deposito(request: Request):
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, papel = ac
    pool = get_pool()
    aba = request.query_params.get("aba") or "geral"
    if aba not in dict(_ABAS):
        aba = "geral"
    v = op.visao(pool, conta[0])
    colunas = {s: [p for p in v["pedidos"] if p["status"] == s]
               for s in ("pedido", "separando", "saiu", "recebido")}
    ferramentas = fer.listar(pool, conta[0])
    obras_abertas = [o for o in ob.listar_obras(pool, conta[0], com_custos=False)
                     if o["status"] not in ("vendida", "entregue", "arquivada")]
    abc, ordem = inv.curva(pool, conta[0], v["estoque"]) if aba in ("geral", "estoque", "inventario") else ({}, [])
    return _render("obras_deposito", request, titulo="Depósito (CD)", secao_ativa="obras_deposito",
                   aba=aba, abas=_ABAS, v=v, colunas=colunas, rotulo=op.ROTULO,
                   ve_dinheiro=papel in ("dono", "gestor"), brl=ob._brl,
                   rotulo_mat=_rotulo_mat(),
                   sobras=op.sobras(pool, conta[0]) if aba in ("geral", "sobras") else [],
                   entradas=op.entradas(pool, conta[0]) if aba == "entradas" else [],
                   a_conferir=conf.pendentes(pool, conta[0]) if aba in ("geral", "entradas") else [],
                   conferidas=conf.conferidas(pool, conta[0]) if aba == "entradas" else [],
                   divergencias=conf.divergencias(pool, conta[0]) if aba == "entradas" else [],
                   abc=abc, motivos=inv.MOTIVOS,
                   dia=inv.contagem_do_dia(pool, conta[0], linhas=v["estoque"], abc=abc)
                   if aba in ("geral", "inventario") else None,
                   ind=inv.indicadores(pool, conta[0], linhas=v["estoque"], ordem=ordem)
                   if aba in ("geral", "inventario") else None,
                   ferramentas=ferramentas, rf=fer.resumo(ferramentas), obras_abertas=obras_abertas,
                   ferr_prontas=[f for f in ferramentas if f["obra_pronta"]],
                   ok=(request.query_params.get("ok") or "").strip(),
                   erro=(request.query_params.get("erro") or "").strip())


@router.post(_BASE + "/pedido/{pedido_id}/{acao}")
def pedido_acao(request: Request, pedido_id: int, acao: str):
    """separar / saiu / cancelar / receber (o "recebi" pelo painel, pra obra sem
    mestre no app: chegou tudo o que foi pedido)."""
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, _ = ac
    try:
        if acao == "separar":
            op.avancar(get_pool(), conta[0], pedido_id, "separando")
            msg = "Separando."
        elif acao == "saiu":
            op.avancar(get_pool(), conta[0], pedido_id, "saiu")
            msg = "Saiu pra obra — agora é com o \"recebi\" do mestre."
        elif acao == "cancelar":
            op.cancelar(get_pool(), conta[0], pedido_id)
            msg = "Pedido cancelado."
        elif acao == "receber":
            msg = op.receber(get_pool(), conta[0], pedido_id,
                             recebido_por=request.session.get("membro_id"))["frase"]
        else:
            return _volta("pedidos", erro="Ação desconhecida.")
    except ValueError as e:
        return _volta("pedidos", erro=str(e))
    return _volta("pedidos", ok=msg)


@router.post(_BASE + "/devolver/{obra_id}")
def devolver(request: Request, obra_id: int):
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, _ = ac
    try:
        msg = fer.devolver_tudo(get_pool(), conta[0], obra_id,
                                por=request.session.get("membro_id"))
    except ValueError as e:
        return _volta("sobras", erro=str(e))
    return _volta("sobras", ok=msg)


# ── as ferramentas (PR 2 do CD) ───────────────────────────────────────────
@router.post(_BASE + "/ferramenta/nova")
def ferramenta_nova(request: Request, nome: str = Form(""), quantidade: str = Form("1"),
                    obs: str = Form("")):
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, _ = ac
    try:
        cods = fer.cadastrar(get_pool(), conta[0], nome, quantidade, obs)
    except ValueError as e:
        return _volta("ferramentas", erro=str(e))
    return _volta("ferramentas", ok="Cadastrada: " + ", ".join(cods) + " — escreva o código nela.")


@router.post(_BASE + "/ferramenta/{ferramenta_id}/{acao}")
def ferramenta_acao(request: Request, ferramenta_id: int, acao: str, obra_id: str = Form(""),
                    com_quem: str = Form(""), motivo: str = Form("")):
    """emprestar (mandar pra obra, ou pra outra obra) / devolver / baixa."""
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, _ = ac
    por = request.session.get("membro_id")
    try:
        if acao == "emprestar":
            if not obra_id.isdigit():
                raise ValueError("Escolha a obra.")
            msg = fer.emprestar(get_pool(), conta[0], ferramenta_id, int(obra_id),
                                com_quem=com_quem, por=por)
        elif acao == "devolver":
            msg = fer.devolver(get_pool(), conta[0], ferramenta_id, por=por)
        elif acao == "baixa":
            msg = fer.baixar(get_pool(), conta[0], ferramenta_id, motivo)
        else:
            raise ValueError("Ação desconhecida.")
    except ValueError as e:
        return _volta("ferramentas", erro=str(e))
    return _volta("ferramentas", ok=msg)


# ── a conferência da nota (PR 3a do CD) ───────────────────────────────────
@router.post(_BASE + "/conferir/{lancamento_id}")
def conferir(request: Request, lancamento_id: int, mov_id: list[int] = Form([]),
             chegou: list[str] = Form([])):
    """Bateu (as quantidades como vieram) ou o que chegou de verdade. `def`
    síncrono com listas paralelas no Form (banco síncrono não entra em async)."""
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, _ = ac
    try:
        r = conf.conferir(get_pool(), conta[0], lancamento_id, chegou=dict(zip(mov_id, chegou)),
                          por=request.session.get("membro_id"))
    except ValueError as e:
        return _volta("entradas", erro=str(e))
    return _volta("entradas", ok=r["frase"])


@router.post(_BASE + "/conferencia/{conferencia_id}/desfazer")
def conferencia_desfazer(request: Request, conferencia_id: int):
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, _ = ac
    try:
        msg = conf.desfazer(get_pool(), conta[0], conferencia_id)
    except ValueError as e:
        return _volta("entradas", erro=str(e))
    return _volta("entradas", ok=msg)


# ── o inventário rotativo (PR 3b do CD) ───────────────────────────────────
@router.post(_BASE + "/contar")
def contar(request: Request, produto_id: int = Form(0), contado: str = Form(""),
           motivo: str = Form("")):
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, _ = ac
    try:
        r = inv.contar(get_pool(), conta[0], produto_id, contado, motivo=motivo,
                       por=request.session.get("membro_id"))
    except ValueError as e:
        return _volta("inventario", erro=str(e))
    return _volta("inventario", ok=r["frase"])


@router.post(_BASE + "/contagem/{contagem_id}/desfazer")
def contagem_desfazer(request: Request, contagem_id: int):
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, _ = ac
    try:
        msg = inv.desfazer(get_pool(), conta[0], contagem_id)
    except ValueError as e:
        return _volta("inventario", erro=str(e))
    return _volta("inventario", ok=msg)


@router.post(_BASE + "/minimo")
def minimo(request: Request, produto: list[int] = Form([]), minimo: list[str] = Form([])):
    """Os mínimos do CD, todos de uma vez. `def` síncrono (banco síncrono)."""
    ac, redir = _acesso(request)
    if redir is not None:
        return redir
    conta, _ = ac
    from finance import obra_material as omat
    for pid, m in zip(produto, minimo):
        t = (m or "").strip().replace(",", ".")
        try:
            omat.salvar_minimo(get_pool(), conta[0], pid, float(t) if t else 0)
        except ValueError:
            return _volta("estoque", erro="Mínimo inválido.")
    return _volta("estoque", ok="Mínimos salvos.")


# ─────────────────────────────────────────────────────────────── a tela
#
# DADO DIGITADO NUNCA ENTRA NO JAVASCRIPT do onsubmit/onclick: o `|e` protege o
# HTML, mas o navegador desfaz o `&#39;` antes de rodar o JS — "Bomba d'água"
# quebrava o confirm (e o formulário enviava sem perguntar), e um nome feito pra
# isso viraria código na sessão do dono (achado da verificação do #997). A frase
# vai num `data-confirma` e o JS só lê: confirm(this.dataset.confirma).
_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.dp-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.dp-pag h2{margin:0;font-size:1.5rem}
.dp-sub{color:var(--txt-mut);font-size:.88rem;margin-top:.25rem}
.dp-msg{border-radius:9px;padding:.55rem .75rem;margin:.8rem 0}
.dp-msg.ok{background:#10241d;border:1px solid var(--verde)}.dp-msg.erro{background:var(--neon-fundo);border:1px solid var(--neon-borda)}
.dp-abas{display:flex;gap:2px;border-bottom:1px solid var(--borda);margin:1rem 0 .9rem;overflow-x:auto}
.dp-abas a{padding:.55rem .8rem;text-decoration:none;color:var(--txt-mut);border-bottom:3px solid transparent;white-space:nowrap;font-size:.9rem}
.dp-abas a.on{color:inherit;font-weight:700;border-color:var(--verde)}
.dp-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.5rem;margin-bottom:.9rem}
.dp-kpi{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.5rem .7rem}
.dp-kpi .r{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut);display:block}
.dp-kpi .v{font-size:1.3rem;font-weight:700;line-height:1.25;display:block}
.dp-kpi .x{font-size:.72rem;color:var(--txt-mut)}
.dp-kpi.alerta{background:var(--ambar-fundo);border-color:var(--ambar-borda)}
.dp-box{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin-bottom:.7rem}
.dp-duas{display:grid;grid-template-columns:1fr 1fr;gap:.8rem}
@media (max-width:820px){.dp-duas{grid-template-columns:1fr}}
.dp-tab{width:100%;border-collapse:collapse;font-size:.85rem}
.dp-tab td,.dp-tab th{padding:.45rem .5rem;border-bottom:1px solid var(--borda);text-align:left;vertical-align:middle}
.dp-tab th{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut)}
.dp-tab td.v,.dp-tab th.v{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.dp-tab tr.alerta td{background:var(--ambar-fundo)}
.dp-rolo{overflow-x:auto}
.dp-chip{display:inline-block;border-radius:12px;padding:1px 8px;font-size:.72rem;font-weight:700;border:1px solid var(--borda)}
.dp-chip.v{border-color:var(--verde);color:var(--verde-claro)}.dp-chip.a{background:var(--ambar-fundo);border-color:var(--ambar-borda);color:#F0DCA6}
.dp-chip.c{background:var(--neon-fundo);border-color:var(--neon-borda)}
.dp-bt{width:auto;margin:0;display:inline-block;padding:.3rem .65rem;border-radius:7px;border:1px solid var(--borda);background:transparent;color:inherit;cursor:pointer;font-size:.78rem;text-decoration:none;line-height:1.3}
.dp-bt.prim{background:var(--verde);border-color:var(--verde);color:#fff;font-weight:700}
.dp-fluxo{display:grid;grid-template-columns:repeat(4,minmax(210px,1fr));gap:.6rem;overflow-x:auto}
.dp-col{background:var(--borda);border-radius:11px;padding:.5rem;min-height:140px}
.dp-col h4{margin:.1rem .2rem .5rem;font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;display:flex;justify-content:space-between}
.dp-ped{background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:.55rem .6rem;margin-bottom:.45rem;font-size:.84rem}
.dp-ped.urg{border-left:4px solid #e8590c}
.dp-ped ul{margin:.3rem 0 .45rem;padding-left:1.1rem}
.dp-ped .rod{display:flex;justify-content:space-between;align-items:center;gap:.4rem;flex-wrap:wrap;font-size:.74rem;color:var(--txt-mut)}
.dp-ped form{display:inline;margin:0}
.dp-mut{color:var(--txt-mut);font-size:.8rem}
</style>
<div class="dp-pag">
<h2>📦 Depósito (CD)</h2>
<div class="dp-sub">O almoxarifado central: o que tem, quanto dura, o que as obras pediram e o que sobrou nelas.</div>
{% if ok %}<div class="dp-msg ok">✓ {{ ok|e }}</div>{% endif %}
{% if erro %}<div class="dp-msg erro">⚠️ {{ erro|e }}</div>{% endif %}

<div class="dp-abas">{% for k, r in abas %}<a href="?aba={{ k }}" class="{{ 'on' if aba == k }}">{{ r }}{% if k == 'pedidos' and v.abertos %} · {{ v.abertos }}{% endif %}</a>{% endfor %}</div>

{% macro chip_abc(k) %}<span class="dp-chip{{ ' c' if k == 'A' else (' a' if k == 'B' else '') }}">{{ k }}</span>{% endmacro %}
{% macro cartao(p) %}<div class="dp-ped{{ ' urg' if p.urgente and p.status != 'recebido' }}">
  <b>{{ p.obra|e }}</b>{% if p.urgente and p.status != 'recebido' %} <span class="dp-chip c">urgente</span>{% endif %}
  <ul>{% for i in p.itens %}<li>{{ i.rotulo }} de {{ i.nome|e }}
    {% if p.status in ('pedido', 'separando') and i.falta_no_cd %} <span class="dp-chip a">tem {{ '%g'|format(i.no_cd) }}</span>{% endif %}
    {% if i.recebida is not none and i.recebida < i.qtd %} <span class="dp-chip c">recebeu {{ i.rotulo_recebida }}</span>{% endif %}</li>{% endfor %}</ul>
  {% if p.recado %}<div class="dp-mut" style="margin-bottom:.35rem">“{{ p.recado|e }}”</div>{% endif %}
  <div class="rod"><span>{{ p.quem|e }} · {{ p.quando.strftime('%d/%m %H:%M') }}{% if p.prazo %} · pra {{ p.prazo.strftime('%d/%m') }}{% endif %}{% if p.foto_id %} · <a href="/painel/obras/{{ p.obra_id }}/foto/{{ p.foto_id }}" target="_blank" rel="noopener">📷</a>{% endif %}</span>
  <span>
  {% if p.status == 'pedido' %}<form method="post" action="/painel/obras/deposito/pedido/{{ p.id }}/separar"><button class="dp-bt prim">Separar</button></form>
  {% elif p.status == 'separando' %}<form method="post" action="/painel/obras/deposito/pedido/{{ p.id }}/saiu"><button class="dp-bt prim">Saiu</button></form>
  {% elif p.status == 'saiu' %}<span class="dp-chip">esperando o “recebi”</span>
  {% elif p.faltou %}<span class="dp-chip c">faltou</span>{% else %}<span class="dp-chip v">ok</span>{% endif %}
  {% if p.status in ('pedido', 'separando', 'saiu') %}
    {% if p.status == 'saiu' %}<form method="post" action="/painel/obras/deposito/pedido/{{ p.id }}/receber" onsubmit="return confirm('Marcar como recebido TUDO o que foi pedido? O ideal é o mestre confirmar pelo app, com a foto.')"><button class="dp-bt" title="pra obra sem mestre no app">recebido</button></form>{% endif %}
    <form method="post" action="/painel/obras/deposito/pedido/{{ p.id }}/cancelar" onsubmit="return confirm('Cancelar este pedido?')"><button class="dp-bt">cancelar</button></form>
  {% endif %}</span></div>
</div>{% endmacro %}

{% if aba == 'geral' %}
<div class="dp-kpis">
  <div class="dp-kpi"><span class="r">Itens no CD</span><span class="v">{{ v.itens }}</span><span class="x">materiais com saldo</span></div>
  {% if ve_dinheiro %}<div class="dp-kpi"><span class="r">Dinheiro parado</span><span class="v">{{ brl(v.dinheiro) if v.dinheiro else '—' }}</span><span class="x">{{ 'pelo preço da nota' if v.dinheiro else 'sem preço de nota ainda' }}</span></div>{% endif %}
  <div class="dp-kpi{{ ' alerta' if v.urgentes }}"><span class="r">Pedidos das obras</span><span class="v">{{ v.abertos }} aberto{{ 's' if v.abertos != 1 }}</span><span class="x">{{ v.urgentes }} urgente{{ 's' if v.urgentes != 1 }}</span></div>
  <div class="dp-kpi{{ ' alerta' if v.abaixo }}"><span class="r">Abaixo do mínimo</span><span class="v">{{ v.abaixo|length }}</span><span class="x">{{ v.abaixo|map(attribute='nome')|join(' · ')|truncate(40) if v.abaixo else 'nada' }}</span></div>
  {% if rf.total %}<div class="dp-kpi{{ ' alerta' if rf.alertas }}"><span class="r">Ferramentas fora</span><span class="v">{{ rf.fora }} de {{ rf.total }}</span><span class="x">{{ rf.alertas|length }} pra conferir</span></div>{% endif %}
  <div class="dp-kpi{{ ' alerta' if sobras }}"><span class="r">Sobra em casa pronta</span><span class="v">{{ sobras|length }}</span><span class="x">casa{{ 's' if sobras|length != 1 }} com material</span></div>
  <div class="dp-kpi{{ ' alerta' if ind.acuracidade is not none and ind.acuracidade < 95 }}"><span class="r">Acuracidade</span><span class="v">{{ ind.acuracidade ~ '%' if ind.acuracidade is not none else '—' }}</span><span class="x">{{ 'nas contagens do mês' if ind.contagens else 'nenhuma contagem ainda' }}</span></div>
</div>
<div class="dp-duas">
  <div class="dp-box"><b>Precisa de você hoje</b>
    <table class="dp-tab" style="margin-top:.4rem">
    {% for p in v.pedidos if p.status == 'pedido' %}<tr><td>{{ '🔴' if p.urgente else '📝' }} <b>{{ p.obra|e }}</b> pediu {{ p.itens|map(attribute='texto')|join(', ')|e }}</td><td class="v"><a class="dp-bt prim" href="?aba=pedidos">Ver</a></td></tr>{% endfor %}
    {% for r in v.abaixo %}<tr><td>🟠 <b>{{ r.nome|e }}</b> abaixo do mínimo</td><td class="v"><a class="dp-bt" href="?aba=estoque">Ver</a></td></tr>{% endfor %}
    {% for s in sobras %}<tr><td>🏁 <b>{{ s.obra|e }}</b> está pronta com material</td><td class="v"><a class="dp-bt" href="?aba=sobras">Ver</a></td></tr>{% endfor %}
    {% for f in rf.alertas %}<tr><td>🔧 <b>{{ f.nome|e }}</b> ({{ f.codigo|e }}) na {{ f.obra|e }} — {{ f.alerta|e }}</td><td class="v"><a class="dp-bt" href="?aba=ferramentas">Ver</a></td></tr>{% endfor %}
    {% if a_conferir %}<tr><td>🧾 <b>{{ a_conferir|length }} nota{{ 's' if a_conferir|length != 1 }}</b> que chegou no CD falta conferir</td><td class="v"><a class="dp-bt" href="?aba=entradas">Conferir</a></td></tr>{% endif %}
    {% if dia.faltam %}<tr><td>🔢 Contagem do dia: <b>{{ dia.faltam }} {{ 'materiais' if dia.faltam != 1 else 'material' }}</b>{% if dia.faltam_a %} <span class="dp-mut">· {{ dia.faltam_a }} da classe A</span>{% endif %}</td><td class="v"><a class="dp-bt" href="?aba=inventario">Contar</a></td></tr>{% endif %}
    {% if not (v.pedidos|selectattr('status', 'equalto', 'pedido')|list or v.abaixo or sobras or rf.alertas or a_conferir or dia.faltam) %}<tr><td class="dp-mut">Nada pendente. ✅</td></tr>{% endif %}
    </table></div>
  <div class="dp-box"><b>Quanto dura o que tem no CD</b>
    <div class="dp-mut">Pelo que saiu nas últimas 4 semanas.</div>
    {% set com_cob = v.estoque|selectattr('cobertura', 'ne', none)|sort(attribute='cobertura')|list %}
    {% if com_cob %}<table class="dp-tab" style="margin-top:.4rem"><tr><th>Material</th><th class="v">No CD</th><th class="v">Cobertura</th></tr>
    {% for r in com_cob[:6] %}<tr{% if r.cobertura < 7 %} class="alerta"{% endif %}><td>{{ r.nome|e }}</td><td class="v">{{ rotulo_mat(r.saldo, r.unidade)|e }}</td>
      <td class="v"><span class="dp-chip {{ 'c' if r.cobertura < 5 else ('a' if r.cobertura < 7 else 'v') }}">{{ r.cobertura }} dia{{ 's' if r.cobertura != 1 }}</span></td></tr>{% endfor %}</table>
    {% else %}<p class="dp-mut" style="margin:.5rem 0 0">Ainda sem saída do CD no último mês pra calcular.</p>{% endif %}</div>
</div>

{% elif aba == 'pedidos' %}
<p class="dp-mut">O mestre pede pelo app. O CD separa e despacha; o “recebi” do mestre, com a foto, é o que tira o material do CD e põe na casa. Ninguém precisa aprovar — dá pra cancelar.</p>
<div class="dp-fluxo">{% for s in ('pedido', 'separando', 'saiu', 'recebido') %}
  <div class="dp-col"><h4><span>{{ rotulo[s] }}</span><span>{{ colunas[s]|length }}</span></h4>
  {% for p in colunas[s] %}{{ cartao(p) }}{% endfor %}
  {% if not colunas[s] %}<div class="dp-mut" style="padding:.3rem">—</div>{% endif %}</div>{% endfor %}
</div>

{% elif aba == 'estoque' %}
<form method="post" action="/painel/obras/deposito/minimo"><div class="dp-rolo"><table class="dp-tab">
<tr><th>Material</th><th>ABC</th><th class="v">No CD</th><th class="v">Mínimo</th><th class="v">Sai por semana</th><th class="v">Cobertura</th>{% if ve_dinheiro %}<th class="v">Valor (nota)</th>{% endif %}</tr>
{% for r in v.estoque %}<tr{% if r.abaixo or (r.cobertura is not none and r.cobertura < 7) %} class="alerta"{% endif %}>
  <td{% if r.chave %} style="font-weight:600"{% endif %}>{{ r.nome|e }}</td>
  <td>{{ chip_abc(abc.get(r.produto_id, 'C')) }}</td>
  <td class="v"><b>{{ rotulo_mat(r.saldo, r.unidade)|e }}</b>{% if r.abaixo %} ⚠️{% endif %}</td>
  <td class="v"><input type="hidden" name="produto" value="{{ r.produto_id }}"><input name="minimo" value="{{ '%g'|format(r.minimo) if r.minimo else '' }}" inputmode="decimal" style="max-width:4.5rem;text-align:right" placeholder="—"></td>
  <td class="v">{{ r.rotulo_semana|e }}</td>
  <td class="v">{% if r.cobertura is not none %}<span class="dp-chip {{ 'c' if r.cobertura < 5 else ('a' if r.cobertura < 7 else 'v') }}">{{ r.cobertura }} dia{{ 's' if r.cobertura != 1 }}</span>{% else %}—{% endif %}</td>
  {% if ve_dinheiro %}<td class="v">{{ brl(r.valor) if r.valor else '—' }}</td>{% endif %}</tr>{% endfor %}
</table></div>
{% if v.estoque %}<button class="dp-bt prim" style="margin-top:.6rem">Salvar mínimos</button>{% else %}<p class="dp-mut">O CD ainda está vazio. A nota de material sem obra entra aqui sozinha.</p>{% endif %}</form>
<p class="dp-mut" style="margin-top:.6rem"><b>ABC</b> pelo valor que saiu do CD nos últimos 90 dias (CD novo: pelo valor parado nele) — a classe A são os poucos que pesam mais. <b>Cobertura</b> = o que tem no CD ÷ o que sai por dia (média do último mês).{% if ve_dinheiro %} <b>Valor</b> pelo último preço de nota de cada material.{% endif %}</p>

{% elif aba == 'entradas' %}
<h3 style="margin:.2rem 0 .4rem;font-size:1rem">🧾 Falta conferir{% if a_conferir %} · {{ a_conferir|length }}{% endif %}</h3>
<p class="dp-mut">Confira a nota contra o que desceu do caminhão. Bateu? Toque em “Conferir” do jeito que está. Não bateu? Corrija a quantidade que chegou — o CD fica com o que chegou de verdade, e a diferença fica contra o fornecedor.</p>
{% for n in a_conferir %}<form method="post" action="/painel/obras/deposito/conferir/{{ n.lancamento_id }}" class="dp-box">
  <div style="display:flex;justify-content:space-between;gap:.6rem;flex-wrap:wrap"><b>{{ n.fornecedor|e }}</b><span class="dp-mut">nota de {{ n.data.strftime('%d/%m') }}</span></div>
  <table class="dp-tab" style="margin:.4rem 0"><tr><th>Material</th><th class="v">A nota diz</th><th class="v">Chegou</th></tr>
  {% for i in n.itens %}<tr><td>{{ i.nome|e }}</td><td class="v">{{ i.rotulo|e }}</td>
    <td class="v"><input type="hidden" name="mov_id" value="{{ i.mov_id }}"><input name="chegou" value="{{ i.valor_campo }}" inputmode="decimal" style="max-width:5.5rem;text-align:right"></td></tr>{% endfor %}
  </table>
  <button class="dp-bt prim">✓ Conferir</button>
</form>{% endfor %}
{% if not a_conferir %}<div class="dp-box dp-mut">Nenhuma nota pra conferir. ✅</div>{% endif %}

{% if divergencias %}<h3 style="margin:1.2rem 0 .4rem;font-size:1rem">Fornecedores · últimos 30 dias</h3>
<div class="dp-rolo"><table class="dp-tab"><tr><th>Fornecedor</th><th class="v">Notas conferidas</th><th class="v">Com diferença</th></tr>
{% for d in divergencias %}<tr{% if d.com_diferenca %} class="alerta"{% endif %}><td>{{ d.fornecedor|e }}</td><td class="v">{{ d.notas }}</td><td class="v">{% if d.com_diferenca %}<span class="dp-chip a">{{ d.com_diferenca }}</span>{% else %}0{% endif %}</td></tr>{% endfor %}
</table></div>{% endif %}

{% if conferidas %}<h3 style="margin:1.2rem 0 .4rem;font-size:1rem">Conferidas</h3>
<div class="dp-rolo"><table class="dp-tab">
{% for x in conferidas %}<tr><td>{{ x.quando.strftime('%d/%m %H:%M') }}</td><td><b>{{ x.fornecedor|e }}</b>{% if x.difs %}<div class="dp-mut">{{ x.difs|join(' · ')|e }}</div>{% endif %}</td>
  <td>{% if x.divergente %}<span class="dp-chip a">com diferença</span>{% else %}<span class="dp-chip v">bateu</span>{% endif %} <span class="dp-mut">· {{ x.quem|e }}</span></td>
  <td class="v">{% if x.pode_desfazer %}<form method="post" action="/painel/obras/deposito/conferencia/{{ x.id }}/desfazer" onsubmit="return confirm('Desfazer esta conferência? A nota volta pra conferir.')"><button class="dp-bt">desfazer</button></form>{% else %}<span class="dp-mut">foi pra obra</span>{% endif %}</td></tr>{% endfor %}
</table></div>{% endif %}

<h3 style="margin:1.2rem 0 .4rem;font-size:1rem">Tudo que entrou no CD</h3>
<p class="dp-mut">A nota de material sem obra, o “chegou sem nota” e a sobra que voltou das casas.</p>
<div class="dp-rolo"><table class="dp-tab"><tr><th>Quando</th><th>Material</th><th class="v">Quantidade</th><th>De onde</th></tr>
{% for e in entradas %}<tr><td>{{ e.quando.strftime('%d/%m %H:%M') }}</td><td>{{ e.nome|e }}</td><td class="v">{{ e.rotulo|e }}</td><td class="dp-mut">{{ e.origem|e }}</td></tr>{% endfor %}
{% if not entradas %}<tr><td colspan="4" class="dp-mut">Nada entrou no CD ainda.</td></tr>{% endif %}
</table></div>

{% elif aba == 'ferramentas' %}
<p class="dp-mut">Ferramenta não se gasta: sai e volta. A lista diz onde está cada uma, com quem e há quantos dias — e destaca a que ficou em casa pronta ou está fora há mais de 7 dias.</p>
{% for f in rf.alertas %}<div class="dp-msg erro" style="margin:.4rem 0">🔧 <b>{{ f.nome|e }}</b> ({{ f.codigo|e }}) na {{ f.obra|e }}: {{ f.alerta|e }}</div>{% endfor %}
<div class="dp-rolo"><table class="dp-tab">
<tr><th>Ferramenta</th><th>Código</th><th>Onde está</th><th>Com quem</th><th>Desde</th><th></th></tr>
{% for f in ferramentas %}<tr{% if f.alerta %} class="alerta"{% endif %}>
  <td><b>{{ f.nome|e }}</b>{% if f.obs %}<div class="dp-mut">{{ f.obs|e }}</div>{% endif %}</td>
  <td>{{ f.codigo|e }}</td>
  <td>{% if f.fora %}{{ f.obra|e }}{% if f.obra_pronta %} <span class="dp-chip v">pronta</span>{% endif %}{% else %}<span class="dp-mut">no CD</span>{% endif %}</td>
  <td>{{ f.com_quem|e or ('—' if f.fora else '') }}</td>
  <td>{% if f.fora %}{% if f.alerta and not f.obra_pronta %}<span class="dp-chip a">{{ f.ha }}</span>{% else %}{{ f.ha }}{% endif %}{% endif %}</td>
  <td class="v" style="white-space:normal">
    {% if f.fora %}<form method="post" action="/painel/obras/deposito/ferramenta/{{ f.id }}/devolver" style="display:inline"><button class="dp-bt prim">{{ 'Recolher' if f.obra_pronta else 'Devolver ao CD' }}</button></form>{% endif %}
    {% if obras_abertas %}<details style="display:inline-block;text-align:left"><summary class="dp-bt" style="list-style:none">{{ 'Outra obra' if f.fora else 'Mandar pra obra' }}</summary>
      <form method="post" action="/painel/obras/deposito/ferramenta/{{ f.id }}/emprestar" style="display:flex;gap:.3rem;flex-wrap:wrap;margin-top:.3rem">
        <select name="obra_id" required style="width:auto">{% for o in obras_abertas %}{% if o.id != f.obra_id %}<option value="{{ o.id }}">{{ o.nome|e }}</option>{% endif %}{% endfor %}</select>
        <input name="com_quem" placeholder="com quem" style="width:7.5rem"><button class="dp-bt prim">Mandar</button></form></details>{% endif %}
    <details style="display:inline-block;text-align:left"><summary class="dp-bt" style="list-style:none">Baixa</summary>
      <form method="post" action="/painel/obras/deposito/ferramenta/{{ f.id }}/baixa" style="display:flex;gap:.3rem;margin-top:.3rem" data-confirma="Dar baixa em {{ f.nome|e }} ({{ f.codigo|e }})? Ela sai da lista." onsubmit="return confirm(this.dataset.confirma)">
        <input name="motivo" required placeholder="quebrou, sumiu…" style="width:8.5rem"><button class="dp-bt">Dar baixa</button></form></details>
  </td></tr>{% endfor %}
{% if not ferramentas %}<tr><td colspan="6" class="dp-mut">Nenhuma ferramenta cadastrada ainda.</td></tr>{% endif %}
</table></div>
<details class="dp-box" style="margin-top:.8rem"{% if not ferramentas %} open{% endif %}><summary style="cursor:pointer;font-weight:700">+ Cadastrar ferramenta ou equipamento</summary>
<form method="post" action="/painel/obras/deposito/ferramenta/nova" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.5rem;align-items:end;margin-top:.6rem">
  <div><label class="dp-mut">Nome</label><input name="nome" required placeholder="Betoneira 400 L" style="width:100%"></div>
  <div><label class="dp-mut">Quantas</label><input name="quantidade" value="1" inputmode="numeric" style="width:100%"></div>
  <div><label class="dp-mut">Observação (opcional)</label><input name="obs" placeholder="motor novo em 09/2026" style="width:100%"></div>
  <div><button class="dp-bt prim" style="padding:.5rem .9rem">Cadastrar</button></div>
</form>
<p class="dp-mut" style="margin:.5rem 0 0">Cada uma ganha um código (FER-01, FER-02…). Escreva o código na ferramenta — com tinta ou etiqueta.</p></details>

{% elif aba == 'sobras' %}
<p class="dp-mut">Casa pronta com material ainda nela é dinheiro parado no lugar errado. Um toque traz tudo de volta pro CD — o material e as ferramentas.</p>
{% for f in ferr_prontas %}<div class="dp-box" style="display:flex;justify-content:space-between;gap:.8rem;align-items:center;flex-wrap:wrap">
  <div><b>🔧 {{ f.nome|e }}</b> ({{ f.codigo|e }}) <span class="dp-mut">na {{ f.obra|e }}, pronta — saiu {{ 'hoje' if f.ha == 'hoje' else 'há ' ~ f.ha }}</span></div>
  <form method="post" action="/painel/obras/deposito/ferramenta/{{ f.id }}/devolver"><button class="dp-bt prim">Recolher</button></form>
</div>{% endfor %}
{% for s in sobras %}<div class="dp-box" style="display:flex;justify-content:space-between;gap:.8rem;align-items:center;flex-wrap:wrap">
  <div><b>🏁 {{ s.obra|e }}</b><div class="dp-mut">{{ s.itens|map(attribute='texto')|join(' · ')|e }}</div></div>
  <form method="post" action="/painel/obras/deposito/devolver/{{ s.obra_id }}" data-confirma="Trazer tudo de {{ s.obra|e }} de volta pro CD?" onsubmit="return confirm(this.dataset.confirma)"><button class="dp-bt prim">Devolver ao CD</button></form>
</div>{% endfor %}
{% if not sobras and not ferr_prontas %}<div class="dp-box dp-mut">Nenhuma casa pronta com material ou ferramenta. ✅</div>{% endif %}

{% elif aba == 'inventario' %}
{% macro motivo_sel() %}<select name="motivo" style="width:auto;max-width:11rem;margin:0"><option value="">— motivo —</option>{% for k, r in motivos.items() %}<option value="{{ k }}">{{ r }}</option>{% endfor %}</select>{% endmacro %}
<div class="dp-kpis">
  <div class="dp-kpi{{ ' alerta' if ind.acuracidade is not none and ind.acuracidade < 95 }}"><span class="r">Acuracidade</span><span class="v">{{ ind.acuracidade ~ '%' if ind.acuracidade is not none else '—' }}</span><span class="x">{% if ind.contagens %}{{ ind.bateram }} de {{ ind.contagens }} bateram · meta 95%{% else %}sem contagem ainda{% endif %}</span></div>
  <div class="dp-kpi{{ ' alerta' if ind.perdas }}"><span class="r">Perdas no mês</span><span class="v">{% if not ind.perdas %}nenhuma{% elif ve_dinheiro and ind.perdas_valor %}{{ brl(ind.perdas_valor) }}{% else %}{{ ind.perdas|length }} materia{{ 'is' if ind.perdas|length != 1 else 'l' }}{% endif %}</span><span class="x">{% for x in ind.perdas %}{{ x.rotulo|e }} de {{ x.nome|e }} ({{ x.motivo }}){{ ' · ' if not loop.last }}{% endfor %}</span></div>
  <div class="dp-kpi{{ ' alerta' if ind.divergentes }}"><span class="r">Notas com diferença</span><span class="v">{{ ind.divergentes or 'nenhuma' }}</span><span class="x">{{ ind.fornecedor_divergente|e if ind.divergentes else 'nas notas conferidas' }}</span></div>
  <div class="dp-kpi"><span class="r">Pedido → recebido</span><span class="v">{{ ind.pedido_recebido.media if ind.pedido_recebido else '—' }}</span><span class="x">{% if ind.pedido_recebido %}média de {{ ind.pedido_recebido.pedidos }} pedido{{ 's' if ind.pedido_recebido.pedidos != 1 }}{% else %}sem pedido recebido{% endif %}</span></div>
  <div class="dp-kpi"><span class="r">Giro</span><span class="v">{{ ('%.1f'|format(ind.giro.vezes)).replace('.', ',') ~ '×' if ind.giro and ind.giro.vezes is not none else '—' }}</span><span class="x">{% if ind.giro %}{{ ind.giro.nome|e }} no mês{% else %}sem saída no mês{% endif %}</span></div>
</div>

<div class="dp-box"><b>🔢 Contagem de hoje</b> <span class="dp-mut">({{ dia.itens|length }} {{ 'materiais' if dia.itens|length != 1 else 'material' }}, classe A primeiro)</span>
  {% if dia.itens %}<div class="dp-rolo"><table class="dp-tab" style="margin-top:.4rem"><tr><th>Material</th><th class="v">Sistema</th><th class="v">Contado</th></tr>
  {% for i in dia.itens %}{% set k = i.contagem %}<tr{% if k and k.dif < 0 %} class="alerta"{% endif %}>
    <td>{{ chip_abc(i.classe) }} {{ i.nome|e }}{% if not k %} <span class="dp-mut">· {{ 'nunca contado' if not i.ultima else 'última contagem ' ~ i.ultima.strftime('%d/%m') }}</span>{% endif %}</td>
    <td class="v">{{ rotulo_mat(i.sistema, i.unidade)|e }}</td>
    <td class="v">{% if k %}{{ '%g'|format(k.contado) }} <span class="dp-chip {{ 'v' if k.dif == 0 else 'c' }}">{{ k.rotulo_dif|e }}</span>
      {% if k.pode_desfazer %}<form method="post" action="/painel/obras/deposito/contagem/{{ k.id }}/desfazer" style="display:inline" onsubmit="return confirm('Desfazer esta contagem? O ajuste no estoque é apagado.')"><button class="dp-bt" title="digitou errado?">desfazer</button></form>{% endif %}
    {% else %}<form method="post" action="/painel/obras/deposito/contar" style="display:flex;gap:.35rem;justify-content:flex-end;align-items:center;flex-wrap:wrap;margin:0">
      <input type="hidden" name="produto_id" value="{{ i.produto_id }}"><input name="contado" required inputmode="decimal" placeholder="quanto tem" style="width:6.5rem;margin:0;text-align:right">
      {{ motivo_sel() }}<button class="dp-bt prim">Contar</button></form>{% endif %}</td></tr>{% endfor %}
  </table></div>
  {% else %}<p class="dp-mut" style="margin:.5rem 0 0">Nada pra contar hoje — o CD está em dia. ✅</p>{% endif %}
  {% if dia.esperando %}<p class="dp-mut" style="margin:.6rem 0 0">⏳ Esperando a conferência da nota: {% for x in dia.esperando %}<b>{{ x.nome|e }}</b> ({{ x.fornecedor|e }}){{ ', ' if not loop.last }}{% endfor %}. <a href="?aba=entradas">Confira na aba Entradas</a> — depois eles entram na contagem.</p>{% endif %}
  <p class="dp-mut" style="margin:.6rem 0 0">Conte o que está na prateleira e digite. Não bateu? Escolha o motivo — a diferença vira ajuste no estoque. A classe A é contada toda semana, a B a cada 15 dias, a C uma vez por mês.</p>
</div>

{% set na_lista = dia.itens|map(attribute='produto_id')|list %}
{% set fora = v.estoque|rejectattr('produto_id', 'in', na_lista)|list %}
{% if fora %}<details class="dp-box"><summary><b>Contar outro material</b> <span class="dp-mut">— achou algo estranho na prateleira?</span></summary>
<form method="post" action="/painel/obras/deposito/contar" style="display:flex;gap:.4rem;flex-wrap:wrap;align-items:center;margin-top:.6rem">
  <select name="produto_id" required>{% for r in fora %}<option value="{{ r.produto_id }}">{{ r.nome|e }} (sistema: {{ rotulo_mat(r.saldo, r.unidade)|e }})</option>{% endfor %}</select>
  <input name="contado" required inputmode="decimal" placeholder="quanto tem" style="max-width:6rem;text-align:right">
  {{ motivo_sel() }}<button class="dp-bt prim">Contar</button>
</form></details>{% endif %}

{% if dia.recentes %}<h3 style="margin:1.2rem 0 .4rem;font-size:1rem">Contagens recentes</h3>
<div class="dp-rolo"><table class="dp-tab"><tr><th>Quando</th><th>Material</th><th class="v">Sistema</th><th class="v">Contado</th><th>Diferença</th><th>Quem</th></tr>
{% for k in dia.recentes %}<tr><td>{{ k.quando.strftime('%d/%m %H:%M') }}</td><td>{{ k.nome|e }}</td>
  <td class="v">{{ '%g'|format(k.sistema) }}</td><td class="v">{{ '%g'|format(k.contado) }}</td>
  <td><span class="dp-chip {{ 'v' if k.dif == 0 else 'c' }}">{{ k.rotulo_dif|e }}</span></td><td class="dp-mut">{{ k.quem|e }}</td></tr>{% endfor %}
</table></div>{% endif %}
<p class="dp-mut" style="margin-top:.8rem"><b>Acuracidade</b> = contagens que bateram ÷ contagens (últimos 30 dias). <b>Giro</b> = o que saiu do CD no mês ÷ o estoque médio, do material que mais pesa na curva ABC.</p>
{% endif %}
</div>
{% endblock %}"""

_env.loader.mapping["obras_deposito"] = _TPL
