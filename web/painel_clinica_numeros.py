"""Os números da clínica: /painel/clinica/numeros (finance/clinica_numeros.py).

É o painel do DONO (e do gestor): ocupação, buraco da agenda, consulta → plano →
fechado, ticket, sessões devidas, faltas, retornos e vagas. A recepção não vê receita.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import clinica_agenda as ca
from finance import clinica_config as cc
from finance import clinica_numeros as cn
from web.painel_clinica_agenda import _acesso
from web.portal import _env, _render

router = APIRouter()


@router.get("/painel/clinica/numeros", response_class=HTMLResponse)
def numeros(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return RedirectResponse("/painel/clinica/agenda", status_code=303)
    agora = datetime.now(timezone.utc)
    hoje = ca.hoje_br(agora)
    ini, fim = cn.periodo_do_mes(request.query_params.get("mes"), hoje)
    with get_pool().connection() as c:
        d = cn.numeros(c, conta[0], ini, fim, agora)
    ant = (ini - timedelta(days=1)).replace(day=1)
    meses = []
    m = hoje.replace(day=1)
    for _ in range(12):
        meses.append((f"{m:%Y-%m}", f"{cn.MESES[m.month - 1]}/{m:%y}"))
        m = (m - timedelta(days=1)).replace(day=1)
    return _render("clinica_numeros.html", request, titulo="Números da clínica", secao_ativa="raio_x",
                   d=d, brl=lambda v: cc.reais(v) if v else "R$ 0", mes=f"{ini:%Y-%m}", meses=meses,
                   rotulo=f"{cn.MESES[ini.month - 1]}/{ini:%Y}", futuro=ini > hoje, parcial=ini <= hoje < fim,
                   ant=f"{ant:%Y-%m}")


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.nx-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.nx-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.nx-topo h2{margin:0;font-size:1.5rem}.nx-topo .sub{color:var(--txt-mut);font-size:.86rem;margin-top:.2rem;max-width:66ch}
.nx-g{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.55rem;margin:.6rem 0 1rem}
.nx-c{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.6rem .8rem}
.nx-c .r{font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut);display:block}
.nx-c .v{display:block;font-size:1.35rem;font-weight:700;line-height:1.2}
.nx-c .n{display:block;font-size:.74rem;color:var(--txt-mut)}
.nx-c.al{background:var(--ambar-fundo);border-color:var(--ambar-borda)}
.nx-h{margin:1.2rem 0 .2rem;font-size:1.02rem}.nx-ex{font-size:.8rem;color:var(--txt-mut);margin-bottom:.3rem}
.nx-t{width:100%;border-collapse:collapse;font-size:.88rem}.nx-t td,.nx-t th{padding:.4rem .3rem;border-bottom:1px solid var(--borda);text-align:left}
.nx-t th{font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--txt-mut)}.nx-t td.v{text-align:right}
.nx-bar{height:8px;border-radius:4px;background:var(--borda);overflow:hidden;min-width:80px}.nx-bar i{display:block;height:100%;background:var(--verde)}
.nx-sel{display:flex;gap:.4rem;align-items:center;flex-wrap:wrap}.nx-sel select{width:auto}
</style>
<div class="nx-pag">
  <div class="nx-topo"><div><h2>Números da clínica · {{ rotulo }}</h2>
    <div class="sub">O que a agenda, os planos, os pacotes e as vagas contam do negócio.{% if parcial %} O mês ainda está correndo.{% endif %}{% if d.passado %} A grade usada é a de hoje (a grade não guarda histórico).{% endif %} Receita e ticket são estimados pelo preço de tabela e pelo plano aceito; o caixa de verdade está no Financeiro.</div></div>
    <form class="nx-sel" method="get" action="/painel/clinica/numeros">
      <select name="mes" onchange="this.form.submit()">{% for k, r in meses %}<option value="{{ k }}" {% if k == mes %}selected{% endif %}>{{ r }}</option>{% endfor %}</select>
      <a href="/painel/raio-x">Raio-X</a></form></div>

  <div class="nx-g">
    <div class="nx-c"><span class="r">Ocupação da agenda</span><span class="v">{{ d.ocupacao_pct if d.ocupacao_pct is not none else '—' }}{% if d.ocupacao_pct is not none %}%{% endif %}</span><span class="n">{{ d.vendido_h }} h vendidas de {{ d.grade_h }} h de grade</span></div>
    <div class="nx-c {% if d.vazios %}al{% endif %}"><span class="r">Horários que passaram vazios</span><span class="v">{{ d.vazios }}</span><span class="n">de 30 min sem ninguém{% if d.vazios_valor %} · até {{ brl(d.vazios_valor) }} em consultas de {{ brl(d.consulta_preco) }} ({{ d.consulta_min }} min){% endif %}</span></div>
    <div class="nx-c"><span class="r">Ticket médio</span><span class="v">{{ brl(d.ticket) if d.ticket else '—' }}</span><span class="n">{{ d.atendimentos }} atendimento{{ 's' if d.atendimentos != 1 }} · {{ brl(d.receita) }} estimados</span></div>
    <div class="nx-c {% if d.devidas %}al{% endif %}"><span class="r">Sessões devidas hoje</span><span class="v">{{ d.devidas }}</span><span class="n">no mês: vendidas {{ d.vendidas }} · usadas {{ d.usadas }} · é atendimento que a clínica deve</span></div>
  </div>

  <h3 class="nx-h">Da consulta ao tratamento fechado</h3>
  <div class="nx-ex">Onde o dinheiro trava: quantas consultas viram plano e quantos planos fecham.</div>
  <div class="nx-g">
    <div class="nx-c"><span class="r">Consultas finalizadas</span><span class="v">{{ d.consultas }}</span></div>
    <div class="nx-c"><span class="r">Planos enviados</span><span class="v">{{ d.planos_enviados }}</span><span class="n">{% if d.consulta_proposta_pct is not none %}{{ d.consulta_proposta_pct }}% das consultas{% endif %}</span></div>
    <div class="nx-c"><span class="r">Planos fechados</span><span class="v">{{ d.planos_aceitos }}</span><span class="n">{% if d.proposta_fechada_pct is not none %}{{ d.proposta_fechada_pct }}% dos enviados · {% endif %}{{ brl(d.valor_aceito) }}</span></div>
  </div>

  <h3 class="nx-h">Ocupação por profissional</h3>
  {% if d.ocupacao %}<table class="nx-t"><tr><th>Profissional</th><th>Grade</th><th>Vendido</th><th></th><th>Vazios que passaram</th></tr>
  {% for o in d.ocupacao %}<tr><td>{{ o.prof }}{% if not o.ativo %} <small>(desativado)</small>{% endif %}</td><td>{{ o.grade_h }} h</td><td>{{ o.vendido_h }} h{% if o.fora_h %} <small>+ {{ o.fora_h }} h fora da grade</small>{% endif %}</td>
    <td><div class="nx-bar"><i style="width:{{ o.pct or 0 }}%"></i></div></td><td class="v">{{ o.vazios }}</td></tr>{% endfor %}</table>
  {% else %}<div class="nx-ex">Nenhum profissional com grade neste mês. Cadastre os horários em Configurar.</div>{% endif %}

  <h3 class="nx-h">Faltas, confirmação e retorno</h3>
  <div class="nx-g">
    <div class="nx-c {% if d.falta_pct and d.falta_pct >= 10 %}al{% endif %}"><span class="r">Faltas</span><span class="v">{{ d.faltas }}</span><span class="n">{% if d.falta_pct is not none %}{{ d.falta_pct }}% dos atendimentos · {% endif %}{{ d.cancelamentos }} cancelamento{{ 's' if d.cancelamentos != 1 }}</span></div>
    <div class="nx-c"><span class="r">Confirmados depois do lembrete</span><span class="v">{{ d.confirmacao_pct if d.confirmacao_pct is not none else '—' }}{% if d.confirmacao_pct is not none %}%{% endif %}</span><span class="n">{{ d.confirmaram }} de {{ d.lembrados }} lembretes</span></div>
    <div class="nx-c"><span class="r">Retornos pedidos</span><span class="v">{{ d.retornos_pedidos }}</span><span class="n">{{ d.retornos_marcados }} marcados · {{ d.retornos_perdidos }} perdidos</span></div>
    <div class="nx-c"><span class="r">Produtos vendidos</span><span class="v">{{ brl(d.produtos) }}</span><span class="n">no mês, fora do ticket por atendimento</span></div>
    <div class="nx-c"><span class="r">Assinantes hoje</span><span class="v">{{ d.assinantes }}</span><span class="n">{{ brl(d.recorrente) }}/mês recorrente · {{ brl(d.mensalidades) }} lançados no mês</span></div>
    <div class="nx-c"><span class="r">Vagas liberadas</span><span class="v">{{ d.vagas }}</span><span class="n">{{ d.vagas_preenchidas }} preenchidas · {{ brl(d.vagas_valor) }}</span></div>
  </div>

  <h3 class="nx-h">De onde vieram os agendamentos</h3>
  <div class="nx-g">
    <div class="nx-c"><span class="r">Recepção</span><span class="v">{{ d.origem.recepcao }}</span></div>
    <div class="nx-c"><span class="r">Agente no WhatsApp</span><span class="v">{{ d.origem.ia }}</span></div>
    <div class="nx-c"><span class="r">Vaga liberada</span><span class="v">{{ d.origem.vaga }}</span></div>
    <div class="nx-c"><span class="r">Sessões vencidas hoje</span><span class="v">{{ d.vencidas }}</span><span class="n">de pacotes que venceram com saldo</span></div>
  </div>
</div>
{% endblock %}"""

_env.loader.mapping["clinica_numeros.html"] = _TPL
