"""O Raio-X do dono no painel: /painel/raio-x (Peça 3 do mockup
docs/mockups/raio_x_como_fica.html, aprovada em 05/09/2026).

A tela é o placar do período com a barra de filtros, uma linha por vendedor (o
mesmo Raio-X que o grupo recebe na segunda), os blocos que o Zaq enriquece
sozinho (demanda × agenda, dia da festa, tipo e ticket, do lead ao contrato, por
que perdeu), e a confiança do dado no pé. Toda conta lê finance/raio_x_dono; aqui
é só a tela.

Quem vê: dono e gestor (o mesmo par que recebe as Novidades). O vendedor tem o
dele no app, na aba Raio-X; o papel financeiro não vende.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import raio_x_dono as rxd
from web.portal import _env, _render, conta_logada

router = APIRouter()


def _pode_ver(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if request.session.get("papel", "dono") not in ("dono", "gestor"):
        return None, RedirectResponse("/painel", status_code=303)
    return conta, None


def _brl(centavos) -> str:
    return rxd._reais(centavos)


def _delta_txt(d: dict | None, menor_melhor: bool = False, unidade: str = "") -> tuple[str, str]:
    """(classe, texto) do comparativo com o período anterior."""
    if not d or d["n"] == 0:
        return "", "igual ao período anterior"
    sinal = "+" if d["n"] > 0 else "−"
    txt = f"{sinal}{abs(d['n'])}{unidade}" + (f" ({sinal}{abs(d['pct'])}%)" if d.get("pct") is not None else "")
    melhor = (d["n"] < 0) if menor_melhor else (d["n"] > 0)
    return ("ok" if melhor else "ruim"), txt + " vs. anterior"


@router.get("/painel/raio-x", response_class=HTMLResponse)
def painel_raio_x(request: Request):
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    pool = get_pool()
    f = rxd.filtros(request.query_params)
    d = rxd.dono(pool, conta[0], f)
    try:
        with pool.connection() as c:
            vendedores = c.execute("""select id, nome from membros
                                       where conta_id = %s and papel = 'vendedor' and coalesce(ativo, true)
                                       order by nome""", (conta[0],)).fetchall()
    except Exception:  # noqa: BLE001
        vendedores = []
    p, ant = d["placar"], d["anterior"]
    comp = {}
    if p and ant:
        comp["leads"] = _delta_txt(rxd.delta(p["leads"], ant["leads"]))
        comp["primeira"] = _delta_txt(rxd.delta(p["primeira_min"], ant["primeira_min"]), menor_melhor=True, unidade=" min")
        comp["propostas"] = _delta_txt(rxd.delta(p["propostas"], ant["propostas"]))
        comp["contratos"] = _delta_txt(rxd.delta(p["contratos"], ant["contratos"]))
    # os meses do filtro "mês da festa": os 12 a partir do corrente
    from datetime import date, timedelta
    hoje = date.today()
    m = hoje.replace(day=1)
    meses = []
    for _ in range(12):
        meses.append((m.strftime("%Y-%m"), f"{rxd._MESES[m.month - 1]}/{m:%y}"))
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    quente = [m["rotulo"] for m in (d["demanda_agenda"] or []) if m["pedindo"] > m["agenda"]]
    return _render("raio_x", request, titulo="Raio-X", secao_ativa="raio_x",
                   d=d, f=f, p=p, comp=comp, vendedores=vendedores, meses=meses, quente=quente,
                   rxd=rxd, brl=_brl, fmt_min=rxd.fmt_min,
                   confianca_txt=(rxd.texto_confianca(d["confianca"]) if d["confianca"] else ""),
                   maximo=max)


_RAIO_X_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.rx{display:flex;flex-direction:column;gap:1rem}
.rx h1{font-size:1.5rem;margin:0}
.rx .lede{color:var(--text-dim);font-size:.88rem;margin:.2rem 0 0;max-width:64ch}
.rx-bar{display:flex;flex-wrap:wrap;gap:.4rem;padding:.7rem .8rem;border:1px solid var(--line);border-radius:12px;background:var(--bg-2);align-items:center}
.rx-bar label{display:inline-flex;align-items:center;gap:.35rem;border:1px solid var(--line);border-radius:999px;padding:.2rem .35rem .2rem .7rem;font-size:.74rem;color:var(--text-dim);background:var(--surface);margin:0}
.rx-bar label.on{border-color:var(--neon-borda);background:var(--neon-fundo);color:var(--text)}
.rx-bar select,.rx-bar input[type=date]{width:auto;margin:0;padding:.15rem .3rem;font-size:.76rem;background:transparent;border:0;color:var(--text);font-weight:500}
.rx-bar select:focus,.rx-bar input:focus{outline:none}
.rx-bar .sep{width:1px;height:18px;background:var(--line);margin:0 .2rem}
.rx-bar .limpar{font-size:.72rem;color:var(--text-faint);margin-left:auto}
.rx-kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:.6rem}
@media (max-width:900px){.rx-kpis{grid-template-columns:repeat(2,1fr)}}
.kpi{border:1px solid var(--line);border-radius:10px;background:var(--surface);padding:.65rem .8rem}
.kpi b{display:block;font:600 1.35rem/1 var(--display);font-variant-numeric:tabular-nums}
.kpi span{display:block;font-size:.66rem;color:var(--text-faint);margin-top:.25rem}
.kpi em{display:block;font:500 .6rem var(--mono);font-style:normal;margin-top:.25rem;color:var(--text-dim)}
.kpi em.ok{color:var(--neon-bright)}.kpi em.ruim{color:var(--coral)}
.kpi.ok b{color:var(--neon-bright)}.kpi.amb b{color:var(--ambar)}.kpi.ruim b{color:var(--coral)}
.rx-grade{display:grid;grid-template-columns:repeat(3,1fr);gap:.8rem}
@media (max-width:900px){.rx-grade{grid-template-columns:1fr}}
.bloco{border:1px solid var(--line);border-radius:12px;background:var(--surface);padding:.85rem .95rem;display:flex;flex-direction:column;gap:.5rem}
.bloco h4{font-size:.92rem;display:flex;justify-content:space-between;align-items:baseline;gap:.5rem;margin:0}
.bloco h4 small{font:500 .62rem var(--mono);color:var(--text-faint)}
.bloco p{margin:0;font-size:.8rem;color:var(--text-dim)}
.bloco p b{color:var(--text);font-weight:500}
.bloco .acha{font-size:.76rem;color:#F0DCA6;background:var(--ambar-fundo);border:1px solid var(--ambar-borda);border-radius:8px;padding:.4rem .55rem}
.bloco .vazio{font-size:.76rem;color:var(--text-faint)}
.duas{display:flex;flex-direction:column;gap:.28rem;font-size:.72rem}
.duas div{display:grid;grid-template-columns:46px 1fr 1fr;gap:.4rem;align-items:center}
.duas div span{font-family:var(--mono);color:var(--text-faint)}
.duas i{display:block;height:8px;border-radius:999px;min-width:2px}
.duas .a{background:var(--neon)}.duas .b{background:var(--line)}
.duas .lg{display:flex;gap:.8rem;font-size:.64rem;color:var(--text-faint);margin-top:.15rem}
.duas .lg i{display:inline-block;width:10px;height:8px;vertical-align:middle;margin-right:.25rem}
.sem{display:grid;grid-template-columns:repeat(7,1fr);gap:.25rem;align-items:end;height:74px}
.sem div{display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:.2rem;height:100%;font:500 .62rem var(--mono);color:var(--text-faint)}
.sem i{display:block;width:100%;border-radius:4px 4px 0 0;background:var(--line);min-height:2px}
.sem i.on{background:var(--neon)}
.tipos{display:flex;flex-direction:column;gap:.25rem;font-size:.74rem}
.tipos div{display:grid;grid-template-columns:96px 1fr 72px;gap:.4rem;align-items:center}
.tipos div span:last-child{font-family:var(--mono);color:var(--text-dim);text-align:right}
.tipos i{display:block;height:8px;border-radius:999px;background:var(--roxo);opacity:.8;min-width:2px}
.perdas{display:flex;flex-wrap:wrap;gap:.3rem}
.perdas span{font:500 .66rem var(--mono);border:1px solid var(--line);border-radius:999px;padding:.15rem .5rem;color:var(--text-dim)}
.perdas span.on{border-color:var(--coral-borda);background:var(--coral-fundo);color:#F2BDB9}
.rx-vend{width:100%;border-collapse:collapse;font-size:.8rem}
.rx-vend th{font:500 .62rem var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--text-faint);text-align:left;padding:.4rem .5rem;border-bottom:1px solid var(--line)}
.rx-vend td{padding:.5rem;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums;vertical-align:top}
.rx-vend td b{font-weight:600}
.rx-vend .ok{color:var(--neon-bright)}.rx-vend .amb{color:var(--ambar)}.rx-vend .ruim{color:var(--coral)}
.rx-vend .n{text-align:right}
.rx-tab{overflow-x:auto}
.rx-dado{border:1px solid var(--azul-borda);background:var(--azul-fundo);border-radius:10px;padding:.55rem .7rem;font:400 .7rem/1.5 var(--mono);color:var(--azul)}
.rx-ey{font:500 .66rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--text-faint);margin:.2rem 0 -.4rem}
</style>
<div class="rx">
  <div>
    <h1>Raio-X</h1>
    <p class="lede">O placar de <b>{{ d.rotulo }}</b>, comparado com o período anterior. Os filtros cortam tudo que está abaixo; a linha por vendedor é a mesma que o grupo recebe na segunda.</p>
  </div>

  {% set fl = f %}
  <form method="get" action="/painel/raio-x" class="rx-bar" id="rx-form">
    <label class="on">Período
      <select name="periodo" onchange="rxPeriodo(this)">
        {% for k, r in rxd.PERIODOS %}<option value="{{ k }}" {% if fl.periodo==k %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select>
      <span id="rx-datas" {% if fl.periodo!='datas' %}hidden{% endif %}>
        <input type="date" name="de" value="{{ fl.de or '' }}" onchange="rxEnviar()"> a
        <input type="date" name="ate" value="{{ fl.ate or '' }}" onchange="rxEnviar()">
      </span>
    </label>
    <label class="{{ 'on' if fl.vendedor }}">Vendedor
      <select name="vendedor" onchange="rxEnviar()"><option value="">todos</option>
        {% for vid, vnome in vendedores %}<option value="{{ vid }}" {% if fl.vendedor==vid %}selected{% endif %}>{{ vnome }}</option>{% endfor %}
      </select></label>
    <label class="{{ 'on' if fl.tipo }}">Tipo de festa
      <select name="tipo" onchange="rxEnviar()"><option value="">todos</option>
        {% for t in rxd.TIPOS_FESTA %}<option value="{{ t }}" {% if fl.tipo==t %}selected{% endif %}>{{ t }}</option>{% endfor %}
        <option value="outro" {% if fl.tipo=='outro' %}selected{% endif %}>outro</option>
        <option value="sem" {% if fl.tipo=='sem' %}selected{% endif %}>sem tipo</option>
      </select></label>
    <label class="{{ 'on' if fl.mes }}">Mês da festa
      <select name="mes" onchange="rxEnviar()"><option value="">todos</option>
        {% for k, r in meses %}<option value="{{ k }}" {% if fl.mes==k %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select></label>
    <label class="{{ 'on' if fl.dia }}">Dia da festa
      <select name="dia" onchange="rxEnviar()"><option value="">todos</option>
        {% for k, r in rxd.DIAS_FESTA %}<option value="{{ k }}" {% if fl.dia==k %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select></label>
    <label class="{{ 'on' if fl.conv }}">Convidados
      <select name="conv" onchange="rxEnviar()"><option value="">todos</option>
        {% for k, r, lo, hi in rxd.FAIXAS_CONVIDADOS %}<option value="{{ k }}" {% if fl.conv==k %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select></label>
    <label class="{{ 'on' if fl.origem }}">Origem
      <select name="origem" onchange="rxEnviar()"><option value="">todas</option>
        {% for k, r in rxd.ORIGENS %}<option value="{{ k }}" {% if fl.origem==k %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select></label>
    <label class="{{ 'on' if fl.hora }}">Chegou
      <select name="hora" onchange="rxEnviar()"><option value="">qualquer hora</option>
        {% for k, r in rxd.HORAS %}<option value="{{ k }}" {% if fl.hora==k %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select></label>
    {% if fl.vendedor or fl.tipo or fl.mes or fl.dia or fl.conv or fl.origem or fl.hora or fl.periodo!='mes' %}
    <a class="limpar" href="/painel/raio-x">limpar filtros</a>{% endif %}
  </form>
  <script>
    function rxEnviar(){document.getElementById('rx-form').submit();}
    function rxPeriodo(s){var d=document.getElementById('rx-datas');if(s.value==='datas'){d.hidden=false;}else{rxEnviar();}}
  </script>

  {% if p %}
  <div class="rx-kpis">
    <div class="kpi"><b>{{ p.leads }}</b><span>leads</span>
      <em>{{ p.leads_por_dia }}/dia{% if p.pico %} · pico {{ p.pico }}{% endif %}</em>
      {% if comp.leads %}<em class="{{ comp.leads[0] }}">{{ comp.leads[1] }}</em>{% endif %}</div>
    <div class="kpi {{ 'ok' if p.primeira_min is not none and p.primeira_min <= 5 else 'amb' if p.primeira_min is not none and p.primeira_min <= 60 else 'ruim' if p.primeira_min is not none else '' }}">
      <b>{{ fmt_min(p.primeira_min) }}</b><span>1ª resposta (mediana · meta 5 min)</span>
      <em>{% if p.primeira_n %}{{ p.primeira_em_5 }} de {{ p.primeira_n }} no alvo · comercial {{ fmt_min(p.primeira_comercial) }} · noite/fds {{ fmt_min(p.primeira_noite) }}{% else %}nenhum lead respondido no período{% endif %}</em>
      {% if comp.primeira %}<em class="{{ comp.primeira[0] }}">{{ comp.primeira[1] }}</em>{% endif %}</div>
    <div class="kpi {{ 'ruim' if p.rascunhos else 'ok' if p.propostas else '' }}"><b>{{ p.propostas }}</b><span>propostas · {{ brl(p.propostas_valor) }}</span>
      <em>{% if p.rascunhos %}{{ p.rascunhos }} em rascunho, nunca enviada(s){% else %}nenhum rascunho parado{% endif %}</em>
      {% if comp.propostas %}<em class="{{ comp.propostas[0] }}">{{ comp.propostas[1] }}</em>{% endif %}</div>
    <div class="kpi {{ 'ok' if p.contratos else 'amb' if p.sem_assinar else '' }}"><b>{{ p.contratos }}</b><span>contratos · {{ brl(p.contratos_valor) }}</span>
      <em>{% if p.sem_assinar %}+{{ p.sem_assinar }} aprovado(s) sem assinatura{% else %}nenhum aprovado esperando assinatura{% endif %}</em>
      {% if comp.contratos %}<em class="{{ comp.contratos[0] }}">{{ comp.contratos[1] }}</em>{% endif %}</div>
    <div class="kpi {{ 'ok' if p.visitas_pct is not none and p.visitas_pct >= 70 else 'amb' if p.visitas_pct is not none else '' }}">
      <b>{% if p.visitas_pct is not none %}{{ p.visitas_pct }}%{% else %}—{% endif %}</b><span>visitas que aconteceram</span>
      <em>{% if p.visitas_ok + p.visitas_nao + p.visitas_sem_resposta %}{{ p.visitas_ok }} sim · {{ p.visitas_nao }} não · {{ p.visitas_sem_resposta }} sem resposta{% if not p.visitas_confiavel %} · pouco confiável{% endif %}{% else %}nenhuma visita no período{% endif %}</em></div>
  </div>
  {% else %}
  <div class="rx-dado">Não deu pra montar o placar agora. Tenta de novo em instantes.</div>
  {% endif %}

  {% if d.vendedores %}
  <div class="rx-ey">Por vendedor · {{ d.rotulo }}</div>
  <div class="rx-tab"><table class="rx-vend">
    <tr><th>Vendedor</th><th class="n">Leads</th><th class="n">1ª resposta</th><th class="n">Propostas</th><th class="n">Rascunho</th><th class="n">Toques</th><th class="n">Parou na 1ª</th><th class="n">Responda hoje</th><th>Contratos</th></tr>
    {% for v in d.vendedores %}{% set s = v.semana %}
    <tr><td><b>{{ v.nome }}</b></td>
      <td class="n">{{ s.leads }}</td>
      <td class="n {{ rxd.cor('primeira', s) if s.primeira_min is not none else '' }}">{{ fmt_min(s.primeira_min) }}{% if s.primeira_n %} <small>({{ s.primeira_em_5 }}/{{ s.primeira_n }})</small>{% endif %}</td>
      <td class="n">{{ s.propostas_enviadas }}</td>
      <td class="n {{ 'ruim' if s.rascunhos else '' }}">{{ s.rascunhos }}</td>
      <td class="n">{{ s.toques }}</td>
      <td class="n {{ 'ruim' if s.paradas_1a > 5 else 'amb' if s.paradas_1a else 'ok' }}">{{ s.paradas_1a }}</td>
      <td class="n"><b>{{ v.hoje }}</b></td>
      <td>{% if s.contratos %}<span class="ok">{{ s.contratos|length }} · {{ brl(s.contratos_valor) }}</span>{% elif s.sem_assinar %}<span class="amb">{{ s.sem_assinar|length }} sem assinar</span>{% else %}—{% endif %}</td></tr>
    {% endfor %}
  </table></div>
  {% endif %}

  <div class="rx-ey">O que o Zaq enriquece sozinho</div>
  <div class="rx-grade">
    <div class="bloco">
      <h4>Demanda × agenda <small>leads pedindo o mês vs festas marcadas</small></h4>
      {% if d.demanda_agenda %}{% set mx = maximo(1, (d.demanda_agenda|map(attribute='pedindo')|max), (d.demanda_agenda|map(attribute='agenda')|max)) %}
      <div class="duas">
        {% for m in d.demanda_agenda %}<div><span>{{ m.rotulo }}</span><i class="a" style="width:{{ (100 * m.pedindo / mx)|round|int }}%" title="{{ m.pedindo }} pedindo"></i><i class="b" style="width:{{ (100 * m.agenda / mx)|round|int }}%" title="{{ m.agenda }} na agenda"></i></div>{% endfor %}
        <div class="lg"><span><i class="a"></i>pedindo: {{ d.demanda_agenda|map(attribute='pedindo')|join(' · ') }}</span><span><i class="b"></i>na agenda: {{ d.demanda_agenda|map(attribute='agenda')|join(' · ') }}</span></div>
      </div>
      <div class="acha">{% if quente %}Em <b>{{ quente|join(', ') }}</b> tem mais cliente pedindo do que festa marcada: a agenda tem espaço e o cliente está pedindo. É onde a proposta rápida vira contrato.{% else %}Nenhum mês com mais pedido do que festa marcada. A demanda está coberta pela agenda.{% endif %}</div>
      {% else %}<div class="vazio">Sem dado pra este corte.</div>{% endif %}
    </div>

    <div class="bloco">
      {% set tot_dia = (d.dia_festa|map(attribute='n')|sum) if d.dia_festa else 0 %}
      <h4>Dia da festa <small>{{ tot_dia }} lead(s) com data</small></h4>
      {% if d.dia_festa and tot_dia %}{% set mxd = maximo(1, d.dia_festa|map(attribute='n')|max) %}
      <div class="sem">{% for x in d.dia_festa %}<div><i class="{{ 'on' if x.n == mxd }}" style="height:{{ (100 * x.n / mxd)|round|int }}%"></i>{{ x.rotulo }}<br>{{ x.n }}</div>{% endfor %}</div>
      {% set sab = d.dia_festa[6].n %}
      <div class="acha"><b>{{ (100 * sab / tot_dia)|round|int }}% das festas pedidas caem no sábado.</b> {% if sab / tot_dia >= 0.5 %}Sábado é o produto escasso: vale tabela própria, lista de espera por data, e sexta e domingo com condição melhor pra quem tem data flexível.{% else %}A demanda está espalhada na semana; o sábado não é o gargalo neste corte.{% endif %}</div>
      {% else %}<div class="vazio">Nenhum lead com data neste corte.</div>{% endif %}
    </div>

    <div class="bloco">
      <h4>Tipo de festa e ticket <small>proposta média por tipo</small></h4>
      {% if d.tipos %}{% set com_ticket = d.tipos|selectattr('ticket_centavos')|list %}{% set mxt = maximo(1, (com_ticket|map(attribute='ticket_centavos')|max) if com_ticket else 1) %}
      <div class="tipos">{% for t in d.tipos if t.tipo != 'sem tipo' %}<div><span>{{ t.tipo }} <small>({{ t.n }})</small></span><i style="width:{{ ((100 * (t.ticket_centavos or 0) / mxt)|round|int) }}%"></i><span>{% if t.ticket_centavos %}{{ brl(t.ticket_centavos) }}{% else %}sem proposta{% endif %}</span></div>{% endfor %}</div>
      {% set tot_t = d.tipos|map(attribute='n')|sum %}{% set sem_t = (d.tipos|selectattr('tipo', 'equalto', 'sem tipo')|map(attribute='n')|sum) %}
      <div class="acha">{% if sem_t %}<b>{{ sem_t }} dos {{ tot_t }} leads ({{ (100 * sem_t / tot_t)|round|int }}%) estão sem tipo de festa.</b> Sem o tipo, o Zaq não sabe o ticket nem qual pacote sugerir: é a 2ª pergunta da primeira resposta.{% else %}Todo lead deste corte tem tipo de festa. O ticket por tipo é o que orienta a proposta.{% endif %}</div>
      {% else %}<div class="vazio">Sem dado pra este corte.</div>{% endif %}
    </div>

    <div class="bloco">
      <h4>Do lead à proposta, da proposta ao contrato <small>dias, mediana</small></h4>
      {% if d.ciclo %}
      <p>Lead → proposta: {% if d.ciclo.lead_proposta_dias is not none %}<b>{{ d.ciclo.lead_proposta_dias }} dias</b> ({{ d.ciclo.lead_proposta_n }} proposta(s)){% for v in d.ciclo.por_vendedor %} · {{ v.nome }} <b>{{ v.dias }}</b>{% endfor %}{% else %}nenhuma proposta enviada no período{% endif %}.
        Proposta → contrato: {% if d.ciclo.proposta_contrato_dias is not none %}<b>{{ d.ciclo.proposta_contrato_dias }} dias</b> ({{ d.ciclo.proposta_contrato_n }} contrato(s)){% else %}nenhum contrato assinado no período{% endif %}.</p>
      <div class="acha">{% if d.ciclo.lead_proposta_dias is not none and d.ciclo.lead_proposta_dias > 1 %}Meta sugerida: proposta em 24h depois de data e convidados. Hoje a mediana é {{ d.ciclo.lead_proposta_dias }} dias.{% if d.ciclo.proposta_contrato_dias is not none and d.ciclo.proposta_contrato_dias <= 2 %} Quando a proposta sai, o contrato vem rápido: o gargalo é a proposta sair.{% endif %}{% elif d.ciclo.lead_proposta_dias is not none %}Proposta em até um dia: dentro da meta.{% else %}Sem proposta no período não há ciclo pra medir.{% endif %}</div>
      {% else %}<div class="vazio">Sem dado pra este corte.</div>{% endif %}
    </div>

    <div class="bloco">
      <h4>Por que perdeu <small>{{ d.perdas.total if d.perdas else 0 }} perdido(s) no período</small></h4>
      {% if d.perdas %}
      <div class="perdas">{% for x in d.perdas.itens %}<span class="{{ 'on' if x.n }}">{{ x.rotulo|lower }} · {{ x.n }}</span>{% endfor %}<span>sem motivo · {{ d.perdas.sem_motivo }}</span></div>
      <div class="acha">{% if d.perdas.sem_motivo and d.perdas.total and d.perdas.sem_motivo * 2 >= d.perdas.total %}<b>{{ d.perdas.sem_motivo }} de {{ d.perdas.total }} sem motivo.</b> O motivo é um toque numa lista de seis ao marcar perdido, no app e na ficha. "Data indisponível" alimenta a lista de espera; "achou caro" alimenta a tabela de sábado.{% elif d.perdas.total %}"Data indisponível" alimenta a lista de espera por data; "achou caro" alimenta a tabela de sábado.{% else %}Nenhum lead perdido no período.{% endif %}</div>
      {% else %}<div class="vazio">Sem dado pra este corte.</div>{% endif %}
    </div>

    <div class="bloco">
      <h4>Hora que chegou <small>o mesmo placar, cortado pela chegada</small></h4>
      {% if p %}
      <p>Primeira resposta no horário comercial: <b>{{ fmt_min(p.primeira_comercial) }}</b>. À noite e no fim de semana: <b>{{ fmt_min(p.primeira_noite) }}</b>.{% if p.pico %} O pico de chegada é <b>{{ p.pico }}</b>.{% endif %}</p>
      <div class="acha">{% if p.primeira_noite is not none and p.primeira_comercial is not none and p.primeira_noite > p.primeira_comercial * 2 %}Fora do comercial o cliente espera mais que o dobro. É o número que decide o plantão do agente e a escala de sábado.{% else %}Use o filtro "Chegou" pra ver leads, propostas e contratos só de quem chegou fora do horário.{% endif %}</div>
      {% else %}<div class="vazio">Sem dado pra este corte.</div>{% endif %}
    </div>
  </div>

  {% if confianca_txt %}<div class="rx-dado">📡 Confiança do dado: {{ confianca_txt }}</div>{% endif %}
</div>
{% endblock %}"""

_env.loader.mapping["raio_x"] = _RAIO_X_TPL
