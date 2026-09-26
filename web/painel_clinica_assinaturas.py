"""Assinatura da clínica (finance/clinica_assinaturas.py).

  /painel/clinica/assinaturas                 os assinantes, quem já pode assinar, os planos
  /painel/clinica/assinaturas/nova            ativar a assinatura de um paciente (?lead=)
  /painel/clinica/assinaturas/plano           cadastrar/editar um plano (dono e gestor)
  /painel/clinica/assinaturas/{id}/cancelar   cancelar (dono e gestor)

A mesma porta da agenda da clínica. A recepção ativa a assinatura; o preço e o que o
plano dá são do dono.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import clinica_agenda as ca
from finance import clinica_assinaturas as cas
from finance import clinica_config as cc
from web.painel_clinica_agenda import _acesso
from web.portal import _env, _render

router = APIRouter()
URL = "/painel/clinica/assinaturas"
_AVISOS = {"plano": "Plano salvo.", "ativa": "Assinatura ativada. A mensalidade do mês já está no Financeiro.",
           "cancelada": "Assinatura cancelada. As próximas mensalidades não saem mais."}


def _int(v) -> int | None:
    s = str(v or "").strip()
    return int(s) if s.isdigit() else None


def _ir(request: Request, url: str, aviso: str = "", erro: str = "") -> RedirectResponse:
    if erro:
        request.session["assin_erro"] = erro[:300]
    if aviso in _AVISOS:
        url += ("&" if "?" in url else "?") + f"aviso={aviso}"
    return RedirectResponse(url, status_code=303)


@router.get(URL, response_class=HTMLResponse)
def lista(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    agora = datetime.now(timezone.utc)
    hoje = ca.hoje_br(agora)
    q = request.query_params
    lead = _int(q.get("lead"))
    with get_pool().connection() as c:
        planos = cas.planos(c, conta[0])
        todos = cas.assinantes(c, conta[0], hoje)
        podem = cas.quem_pode_assinar(c, conta[0], agora)
        tipos = cc.listar_tipos(c, conta[0])
        base = {"lead": lead, "paciente": "", "fone": ""}
        if lead:
            r = c.execute("""select coalesce(nullif(contato,''), empresa, ''), coalesce(nullif(whatsapp,''), telefone, '')
                               from prospeccao where id=%s and conta_id=%s""", (lead, conta[0])).fetchone()
            if r:
                base.update(paciente=r[0], fone=r[1])
            else:
                base["lead"] = None
    editar = next((p for p in planos if p["id"] == _int(q.get("editar"))), None) if gerencia else None
    return _render("clinica_assinaturas.html", request, titulo="Assinaturas", secao_ativa="agenda",
                   aviso=_AVISOS.get(q.get("aviso") or "", ""), erro=request.session.pop("assin_erro", ""),
                   planos=planos, ativos=[a for a in todos if a["estado"] == "ativa"],
                   canceladas=[a for a in todos if a["estado"] != "ativa"][:30], podem=podem,
                   r=cas.resumo(todos), tipos=tipos, base=base, gerencia=gerencia, editar=editar,
                   hoje=hoje, brl=cc.reais, ca_data=lambda d: ca.local(d).strftime("%d/%m/%Y") if d else "")


@router.post(URL + "/plano")
def salvar_plano(request: Request, plano_id: str = Form(""), nome: str = Form(""), preco: str = Form(""),
                 dia: str = Form(""), servico_id: str = Form(""), sessoes: str = Form(""),
                 desconto_procedimento: str = Form(""), desconto_produto: str = Form(""),
                 prioridade: str = Form(""), ativo: str = Form("")):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return _ir(request, URL, erro="Só o dono ou o gestor cadastra e muda os planos de assinatura.")
    pid = _int(plano_id)
    with get_pool().connection() as c:
        _id, erro = cas.salvar_plano(c, conta[0], plano_id=pid, nome=nome, preco=preco, dia=dia,
                                     servico_id=servico_id, sessoes=sessoes,
                                     desconto_procedimento=desconto_procedimento, desconto_produto=desconto_produto,
                                     prioridade=bool(prioridade), ativo=bool(ativo) if pid else True)
        (c.rollback if erro else c.commit)()
    if erro:
        return _ir(request, URL + (f"?editar={pid}" if pid else ""), erro=erro)
    return _ir(request, URL, "plano")


@router.post(URL + "/nova")
def nova(request: Request, plano_id: str = Form(""), lead: str = Form(""), paciente: str = Form(""),
         fone: str = Form(""), inicio: str = Form("")):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    lid = _int(lead)
    _aid, erro = cas.assinar(get_pool(), conta[0], plano_id=_int(plano_id), lead=lid, paciente=paciente,
                             fone=fone, inicio=inicio, membro_id=request.session.get("membro_id"))
    if erro:
        return _ir(request, URL + (f"?lead={lid}" if lid else ""), erro=erro)
    return _ir(request, URL, "ativa")


@router.post(URL + "/{assinante_id}/cancelar")
def cancelar(request: Request, assinante_id: int, motivo: str = Form("")):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return _ir(request, URL, erro="Só o dono ou o gestor cancela uma assinatura.")
    with get_pool().connection() as c:
        ok = cas.cancelar(c, conta[0], assinante_id, motivo, request.session.get("membro_id"))
        c.commit()
    return _ir(request, URL, "cancelada" if ok else "")


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.as-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.as-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.as-topo h2{margin:0;font-size:1.5rem}.as-topo .sub{color:var(--txt-mut);font-size:.86rem;margin-top:.2rem;max-width:66ch}
.as-kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem;margin:1rem 0}
.as-kpi div{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.55rem .75rem}
.as-kpi b{display:block;font-size:1.3rem}.as-kpi span{font-size:.72rem;color:var(--txt-mut)}
.as-sec{margin:1.2rem 0 .4rem;font-size:1.02rem}
.as-pl{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:.6rem}
.as-card{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.7rem .85rem}
.as-card.off{opacity:.6}.as-card b{font-size:1rem}.as-card .p{font-size:1.2rem;font-weight:700;margin:.15rem 0}
.as-l{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.2rem .6rem;padding:.5rem .1rem;border-top:1px solid var(--borda)}
.as-m{font-size:.8rem;color:var(--txt-mut)}
.as-chip{font-size:.7rem;padding:.08rem .45rem;border-radius:999px;border:1px solid var(--borda);color:var(--txt-mut);margin-left:.3rem}
.as-chip.al{border-color:var(--ambar-borda);background:var(--ambar-fundo);color:#F0DCA6}
.as-acoes{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center}.as-acoes form{margin:0}
.as-acoes a,.as-acoes button{width:auto;margin:0;min-height:36px;padding:.3rem .7rem;font-size:.82rem;border-radius:8px}
.as-acoes .sec,.as-acoes a{background:transparent;border:1px solid var(--borda);color:var(--txt);text-decoration:none;display:inline-flex;align-items:center}
.as-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin-top:1rem}
.as-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.6rem}
.as-grid label{display:block;font-size:.78rem;color:var(--txt-mut);margin-bottom:.15rem}
details.as-can summary{cursor:pointer;font-size:.8rem;color:var(--txt-mut)}
</style>
<div class="as-pag">
  <div class="as-topo"><div><h2>Assinaturas</h2>
    <div class="sub">Receita que entra todo mês, com ou sem movimento na agenda. A mensalidade vira uma conta a receber no Financeiro no dia de cobrança do plano; o benefício do assinante vale sozinho na agenda, no plano de tratamento e na fila de vagas.</div></div>
    <div class="as-acoes"><a href="/painel/clinica/agenda">‹ Agenda</a><a href="/painel/clinica/pacotes">Pacotes</a></div></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}

  <div class="as-kpi">
    <div><span>Assinantes</span><b>{{ r.ativos }}</b></div>
    {% if gerencia %}<div><span>Receita recorrente</span><b>{{ brl(r.recorrente) if r.recorrente else 'R$ 0' }}</b><span>por mês</span></div>{% endif %}
    <div><span>Com mensalidade atrasada</span><b>{{ r.atrasados }}</b></div>
    <div><span>Planos ativos</span><b>{{ planos|selectattr('ativo')|list|length }}</b></div>
  </div>

  {% if planos|selectattr('ativo')|list %}
  <form class="as-cx" method="post" action="/painel/clinica/assinaturas/nova">
    <b>Ativar assinatura</b>
    <div class="as-m" style="margin:.3rem 0 .6rem">Ofereça no fim do atendimento. A mensalidade do mês é lançada na hora; as próximas, no dia 1º de cada mês, vencendo no dia de cobrança do plano.</div>
    <input type="hidden" name="lead" value="{{ base.lead or '' }}">
    <div class="as-grid">
      <div><label>Plano</label><select name="plano_id">{% for p in planos if p.ativo %}<option value="{{ p.id }}">{{ p.nome }} · {{ p.preco }}/mês</option>{% endfor %}</select></div>
      <div><label>Paciente</label><input name="paciente" value="{{ base.paciente }}" maxlength="120" {% if not base.lead %}required{% endif %}></div>
      <div><label>Celular (com DDD)</label><input name="fone" value="{{ base.fone }}" inputmode="tel" maxlength="40"></div>
      <div><label>Início</label><input type="date" name="inicio" value="{{ hoje.isoformat() }}"></div>
    </div>
    <div class="as-acoes" style="margin-top:.7rem"><button>Ativar</button></div>
  </form>
  {% endif %}

  <h3 class="as-sec">Assinantes</h3>
  {% for a in ativos %}<div class="as-l"><div><b>{{ a.paciente }}</b><span class="as-chip">{{ a.plano }}</span>
      {% if a.atrasadas %}<span class="as-chip al">{{ a.atrasadas }} mensalidade{{ 's' if a.atrasadas != 1 }} atrasada{{ 's' if a.atrasadas != 1 }}</span>{% endif %}
      <div class="as-m">{{ a.preco }}/mês, todo dia {{ a.dia }} · desde {{ a.inicio.strftime('%d/%m/%Y') }}{% if a.sessoes_mes %} · {{ a.servico }} do mês: {{ a.usadas_mes }} de {{ a.sessoes_mes }}{% endif %}</div></div>
    <div class="as-acoes">{% if a.lead %}<a href="/painel/clinica/agenda/novo?lead={{ a.lead }}">Marcar</a>{% endif %}
      {% if gerencia %}<details class="as-can"><summary>cancelar</summary>
        <form method="post" action="/painel/clinica/assinaturas/{{ a.id }}/cancelar" style="margin-top:.4rem"><input name="motivo" placeholder="Motivo" maxlength="200"><button class="sec" style="margin-top:.3rem">Cancelar assinatura</button></form></details>{% endif %}</div></div>
  {% else %}<div class="as-m">Ninguém assinou ainda.</div>{% endfor %}

  <h3 class="as-sec">Já vieram 3 vezes ou mais no ano</h3>
  <div class="as-m" style="margin-bottom:.2rem">Quem mais volta é quem mais ganha com a assinatura. A recepção oferece no próximo atendimento.</div>
  {% for p in podem %}<div class="as-l"><div><b>{{ p.paciente }}</b><div class="as-m">{{ p.visitas }} atendimentos em 12 meses · o último em {{ p.ultima.strftime('%d/%m/%Y') }}</div></div>
    <div class="as-acoes"><a href="/painel/clinica/assinaturas?lead={{ p.lead }}">Oferecer</a></div></div>
  {% else %}<div class="as-m">Ninguém ainda.</div>{% endfor %}

  <h3 class="as-sec">Planos</h3>
  <div class="as-pl">{% for p in planos %}<div class="as-card {% if not p.ativo %}off{% endif %}"><b>{{ p.nome }}</b>{% if not p.ativo %}<span class="as-chip">desativado</span>{% endif %}
      <div class="p">{{ p.preco }}/mês</div><div class="as-m">{{ p.beneficios }}</div>
      <div class="as-m">cobrança todo dia {{ p.dia }} · {{ p.assinantes }} assinante{{ 's' if p.assinantes != 1 }}</div>
      {% if gerencia %}<div class="as-acoes" style="margin-top:.4rem"><a href="/painel/clinica/assinaturas?editar={{ p.id }}#plano">Editar</a></div>{% endif %}</div>
  {% else %}<div class="as-m">Nenhum plano cadastrado.{% if gerencia %} Comece por um simples: uma limpeza por mês e desconto nos procedimentos.{% endif %}</div>{% endfor %}</div>

  {% if gerencia %}
  <form class="as-cx" id="plano" method="post" action="/painel/clinica/assinaturas/plano">
    <b>{{ 'Editar ' ~ editar.nome if editar else 'Novo plano' }}</b>
    <div class="as-m" style="margin:.3rem 0 .6rem">Mudar o preço ou o dia vale para quem assinar daqui pra frente (quem já assinou paga o que combinou); os benefícios valem para todos os assinantes do plano. O desconto em procedimentos vale no plano de tratamento sem pedir sua aprovação; o de produtos entra na venda de produto.</div>
    <input type="hidden" name="plano_id" value="{{ editar.id if editar else '' }}">
    <div class="as-grid">
      <div><label>Nome</label><input name="nome" maxlength="80" value="{{ editar.nome if editar else '' }}" placeholder="Pele em dia" required></div>
      <div><label>Mensalidade (R$)</label><input name="preco" inputmode="decimal" value="{{ (editar.preco_centavos / 100)|round(2) if editar else '' }}" placeholder="149" required></div>
      <div><label>Dia de cobrança (1 a 28)</label><input name="dia" inputmode="numeric" value="{{ editar.dia if editar else 10 }}"></div>
      <div><label>Atendimento incluso</label><select name="servico_id"><option value="">nenhum</option>{% for t in tipos %}<option value="{{ t.id }}" {% if editar and editar.servico_id == t.id %}selected{% endif %}>{{ t.nome }}</option>{% endfor %}</select></div>
      <div><label>Quantos por mês</label><input name="sessoes" inputmode="numeric" value="{{ editar.sessoes_mes if editar else 0 }}"></div>
      <div><label>Desconto em procedimentos (%)</label><input name="desconto_procedimento" inputmode="decimal" value="{{ editar.desconto_procedimento_pct if editar else 0 }}"></div>
      <div><label>Desconto em produtos (%)</label><input name="desconto_produto" inputmode="decimal" value="{{ editar.desconto_produto_pct if editar else 0 }}"></div>
      <div><label style="display:flex;gap:.4rem;align-items:center;margin-top:1.2rem"><input type="checkbox" name="prioridade" value="1" style="width:auto" {% if not editar or editar.prioridade_vagas %}checked{% endif %}> Prioridade nas vagas liberadas</label></div>
      {% if editar %}<div><label style="display:flex;gap:.4rem;align-items:center;margin-top:1.2rem"><input type="checkbox" name="ativo" value="1" style="width:auto" {% if editar.ativo %}checked{% endif %}> Plano ativo (aceita novos assinantes)</label></div>{% endif %}
    </div>
    <div class="as-acoes" style="margin-top:.7rem"><button>Salvar plano</button>{% if editar %}<a href="/painel/clinica/assinaturas">cancelar edição</a>{% endif %}</div>
  </form>
  {% endif %}

  {% if canceladas %}<h3 class="as-sec">Canceladas</h3>
  {% for a in canceladas %}<div class="as-l"><div>{{ a.paciente }}<span class="as-chip">{{ a.plano }}</span><div class="as-m">cancelada em {{ ca_data(a.cancelada_em) }} · {{ a.motivo }}</div></div><div></div></div>{% endfor %}{% endif %}
</div>
{% endblock %}"""

_env.loader.mapping["clinica_assinaturas.html"] = _TPL
