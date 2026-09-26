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


def _linhas_da_visita(d: dict, vendedores) -> list[dict]:
    """As linhas por vendedor do bloco "Da visita ao contrato", com os leads da
    tabela de cima. O que não é de nenhum vendedor ativo (o orçamento feito pelo
    dono, o lead sem vendedor) entra numa linha "Outros" — assim a soma das
    linhas bate com o total do time."""
    dv = d.get("da_visita")
    if not dv:
        return []
    por = dict(dv.get("por_vendedor") or {})
    leads = {v["id"]: v["semana"].get("leads", 0) for v in (d.get("vendedores") or [])}
    out = []
    for vid, nome in vendedores:
        x = por.pop(vid, {})
        out.append({"nome": nome, "leads": leads.get(vid, 0), **{k: x.get(k, 0) for k in
                    ("visitas", "vis_orc", "prop_ass", "contratos", "contratos_valor")}})
    resto = {k: sum(x.get(k, 0) for x in por.values())
             for k in ("visitas", "vis_orc", "prop_ass", "contratos", "contratos_valor")}
    if any(resto.values()):
        out.append({"nome": "Outros (dono, sem vendedor)", "leads": None, **resto})
    return out


@router.get("/painel/raio-x", response_class=HTMLResponse)
def painel_raio_x(request: Request):
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    pool = get_pool()
    perfil = rxd.perfil_da_conta(pool, conta[0])
    if not perfil["aplica"]:
        # conta só de produto: vende no caixa, não tem funil nem vendedor
        return RedirectResponse("/painel", status_code=303)
    f = rxd.filtros(request.query_params, perfil)
    d = rxd.dono(pool, conta[0], f, perfil=perfil)
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
    dv_linhas = _linhas_da_visita(d, vendedores)
    # as UFs e os serviços que existem nesta conta, pros selects do recorrente
    ufs, servicos = [], []
    if "uf" in perfil["filtros"] or "servico" in perfil["filtros"]:
        try:
            with pool.connection() as c:
                ufs = [r[0] for r in c.execute("""select distinct upper(uf) from prospeccao
                                                   where conta_id = %s and coalesce(uf, '') <> '' order by 1""", (conta[0],)).fetchall()]
                servicos = [r[0] for r in c.execute("""select nome from servicos_catalogo
                                                        where conta_id = %s and coalesce(ativo, true) order by ordem, nome""", (conta[0],)).fetchall()]
        except Exception:  # noqa: BLE001
            ufs, servicos = [], []
    return _render("raio_x", request, titulo="Raio-X", secao_ativa="raio_x", perfil=perfil, raio_x_perfil=perfil,
                   ufs=ufs, servicos=servicos, familias=rxd.familias(), portes=[(k, r) for k, r, _ in rxd.PORTES],
                   d=d, f=f, p=p, comp=comp, vendedores=vendedores, meses=meses, quente=quente,
                   dv_linhas=dv_linhas,
                   rxd=rxd, brl=_brl, fmt_min=rxd.fmt_min,
                   confianca_txt=(rxd.texto_confianca(d["confianca"]) if d["confianca"] else ""),
                   maximo=max)


_RAIO_X_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
/* max-width:100% NÃO É ENFEITE. O `body` do painel é `display:flex` com
   `align-items:center` — que NÃO estica os filhos: cada um nasce do tamanho do
   próprio conteúdo. Um `overflow-x:auto` zera o min-content do filho, mas o
   MAX-content continua sendo a tabela inteira: medido em 21/09/2026, a tabela
   por vendedor pede 611px e fazia o `.rx` inteiro nascer com 611px numa tela de
   390. O efeito não era a tabela rolar — era a PÁGINA rolar, levando junto o
   título, os filtros e os cards, com o nome do vendedor saindo pela esquerda.
   O teto devolve a largura da tela ao `.rx` e deixa a rolagem onde ela deve
   ficar: dentro do `.rx-tab`. Desktop não muda (lá o conteúdo pede 1147px e o
   teto não morde). */
.rx{display:flex;flex-direction:column;gap:1rem;max-width:100%}
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
/* da visita ao contrato (24/09/2026) */
.rx-dv{display:grid;grid-template-columns:repeat(4,1fr);gap:.6rem}
@media (max-width:900px){.rx-dv{grid-template-columns:repeat(2,1fr)}}
.rx-dv .st{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:.7rem .8rem;display:flex;flex-direction:column;gap:.15rem}
.rx-dv .st.fim{border-color:var(--neon-border,var(--line));background:var(--neon-bg,var(--surface))}
.rx-dv .l{font:500 .62rem var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--text-faint)}
.rx-dv b{font:700 1.4rem var(--mono)}
.rx-dv em{font-style:normal;font-size:.76rem;color:var(--text-dim)}
.rx-dv .ok{color:var(--neon-bright)}
.rx-dv-nota{font-size:.8rem;color:var(--text-dim)}
.rx-dv-cli .grp{margin-top:.4rem}
.rx-dv-cli h4{font:500 .62rem var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--text-faint);margin:0 0 .2rem}
.rx-dv-cli .chip{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:.05rem .5rem;margin:.12rem .1rem;font-size:.74rem;color:var(--text-dim)}
.rx-dv-cli .chip.ok{color:var(--neon-bright)} .rx-dv-cli .chip.amb{color:var(--ambar)}
.rx-vend tr.tot td{font-weight:700;border-top:1px solid var(--line)}
.rx-dado{border:1px solid var(--azul-borda);background:var(--azul-fundo);border-radius:10px;padding:.55rem .7rem;font:400 .7rem/1.5 var(--mono);color:var(--azul)}
.rx-ey{font:500 .66rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--text-faint);margin:.2rem 0 -.4rem}

/* 05/09/2026: "3 em rascunho" não dizia QUAL — clique no número, aparece quem é */
.rx-vend .n.clic{cursor:pointer}
.rx-vend .n.clic b{border-bottom:1.5px dashed currentColor;padding-bottom:1px}
.rx-vend .n.clic:hover b{border-bottom-style:solid}
.rx-vend .n .cv{font-size:.62rem;opacity:.7;margin-left:.15rem}
.pend-row td{padding:0;border-bottom:1px solid var(--line)}
.pend-row.fechada{display:none}
.pend{padding:.55rem .9rem .75rem 2.2rem;background:var(--bg-2)}
.pend-cap{font:500 .62rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--text-faint);padding-bottom:.4rem}
/* LARGURA DE LEITURA. A tabela ocupa a tela toda; sem teto, o nome ficava numa
   ponta e o botão na outra, com meia tela de vão no meio. */
.pend-lista{max-width:640px;display:flex;flex-direction:column}
.pend-nome .fone{color:var(--text-faint);font-family:var(--mono);font-size:.7rem;margin-left:.35rem}
.pend-item{display:flex;align-items:center;gap:.5rem;padding:.34rem 0;border-top:1px dashed var(--line);flex-wrap:wrap}
/* OS DOIS DOCUMENTOS, na linha de baixo. `order` e não a posição no HTML: no
   markup eles vêm logo depois do nome (é o que se lê junto), e aqui descem pra
   segunda linha sem obrigar o valor e os botões a mudarem de lugar. */
.pend-docs{order:9;flex-basis:100%;display:flex;flex-direction:column;gap:.08rem;padding:0 0 .2rem}
.pd{font-size:.72rem;color:var(--text-dim);display:flex;gap:.4rem;align-items:baseline}
.pd>span{width:.85rem;flex:none;text-align:center}
.pd.ok>span{color:var(--neon-bright)}
.pd.amb{color:var(--ambar)}.pd.amb>span{color:var(--ambar)}
.pd.cor{color:var(--coral)}.pd.cor>span{color:var(--coral)}
.pend-cap.coral{color:var(--coral)}
.pend-cap+.pend-lista{margin-bottom:.7rem}
.pend-item:first-child{border-top:0}
.pend-nome{flex:1;min-width:0;font-size:.8rem;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pend-meta{font:500 .7rem var(--mono);color:var(--text-faint);white-space:nowrap;flex:none}
.pend-meta.velha{color:var(--coral)}
.pend-acoes{display:flex;gap:.35rem;flex:none}
.pend-btn{display:inline-flex;align-items:center;gap:.3rem;font-size:.7rem;font-weight:600;
  border-radius:7px;padding:.22rem .5rem;text-decoration:none;border:1px solid var(--line);
  color:var(--text-dim);background:var(--surface)}
.pend-btn.doc{border-color:var(--azul-borda);color:var(--azul);background:var(--azul-fundo)}
.pend-btn.zap{border-color:var(--neon-borda);color:var(--neon-bright);background:var(--neon-fundo)}
.pend-mais{font-size:.72rem;color:var(--text-faint);padding:.3rem 0 0;max-width:640px}
</style>
{# o balão de conversa é o MESMO do funil (web/balao_conversa.py) #}
<style>{{ balao_css }}</style>
<div class="rx">
  <div>
    <h1>Raio-X</h1>
    <p class="lede">O placar de <b>{{ d.rotulo }}</b>, comparado com o período anterior. Os filtros cortam tudo que está abaixo; a linha por vendedor é a mesma que o grupo recebe na segunda.</p>
    {% if perfil.chave == 'clinica' %}<p class="lede">Ocupação da agenda, horários vazios, consulta → plano → fechado, sessões devidas, faltas e retornos: <a href="/painel/clinica/numeros">Números da clínica ›</a></p>{% endif %}
    {% if not perfil.nicho_escolhido %}<p class="lede" style="color:var(--ambar)">Sua conta ainda não escolheu o nicho. O Raio-X está usando o perfil de serviço; escolha o nicho em <a href="/painel/empresa">Empresa</a> pra ele acertar o vocabulário.</p>{% endif %}
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
        {% for vid, vnome in vendedores %}<option value="{{ vid }}" {% if fl.vendedor==vid %}selected{% endif %}>{{ vnome|e }}</option>{% endfor %}
      </select></label>
    {% if 'tipo' in perfil.filtros %}<label class="{{ 'on' if fl.tipo }}">Tipo de festa
      <select name="tipo" onchange="rxEnviar()"><option value="">todos</option>
        {% for t in rxd.TIPOS_FESTA %}<option value="{{ t }}" {% if fl.tipo==t %}selected{% endif %}>{{ t }}</option>{% endfor %}
        <option value="outro" {% if fl.tipo=='outro' %}selected{% endif %}>outro</option>
        <option value="sem" {% if fl.tipo=='sem' %}selected{% endif %}>sem tipo</option>
      </select></label>{% endif %}
    {% if 'mes' in perfil.filtros %}<label class="{{ 'on' if fl.mes }}">Mês da festa
      <select name="mes" onchange="rxEnviar()"><option value="">todos</option>
        {% for k, r in meses %}<option value="{{ k }}" {% if fl.mes==k %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select></label>{% endif %}
    {% if 'dia' in perfil.filtros %}<label class="{{ 'on' if fl.dia }}">Dia da festa
      <select name="dia" onchange="rxEnviar()"><option value="">todos</option>
        {% for k, r in rxd.DIAS_FESTA %}<option value="{{ k }}" {% if fl.dia==k %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select></label>{% endif %}
    {% if 'conv' in perfil.filtros %}<label class="{{ 'on' if fl.conv }}">Convidados
      <select name="conv" onchange="rxEnviar()"><option value="">todos</option>
        {% for k, r, lo, hi in rxd.FAIXAS_CONVIDADOS %}<option value="{{ k }}" {% if fl.conv==k %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select></label>{% endif %}
    {% if 'segmento' in perfil.filtros %}<label class="{{ 'on' if fl.segmento }}">Segmento
      <select name="segmento" onchange="rxEnviar()"><option value="">todos</option>
        {% for k, r in familias %}<option value="{{ k }}" {% if fl.segmento==k %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select></label>{% endif %}
    {% if 'porte' in perfil.filtros %}<label class="{{ 'on' if fl.porte }}">Porte
      <select name="porte" onchange="rxEnviar()"><option value="">todos</option>
        {% for k, r in portes %}<option value="{{ k }}" {% if fl.porte==k %}selected{% endif %}>{{ r }}</option>{% endfor %}
        <option value="sem" {% if fl.porte=='sem' %}selected{% endif %}>sem porte</option>
      </select></label>{% endif %}
    {% if 'uf' in perfil.filtros %}<label class="{{ 'on' if fl.uf }}">UF
      <select name="uf" onchange="rxEnviar()"><option value="">todas</option>
        {% for u in ufs %}<option value="{{ u }}" {% if fl.uf==u %}selected{% endif %}>{{ u }}</option>{% endfor %}
      </select></label>{% endif %}
    {% if 'servico' in perfil.filtros %}<label class="{{ 'on' if fl.servico }}">Serviço
      <select name="servico" onchange="rxEnviar()"><option value="">todos</option>
        {% for sv in servicos %}<option value="{{ sv }}" {% if fl.servico==sv %}selected{% endif %}>{{ sv }}</option>{% endfor %}
      </select></label>{% endif %}
    <label class="{{ 'on' if fl.origem }}">Origem
      <select name="origem" onchange="rxEnviar()"><option value="">todas</option>
        {% for k, r in rxd.ORIGENS %}<option value="{{ k }}" {% if fl.origem==k %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select></label>
    <label class="{{ 'on' if fl.hora }}">Chegou
      <select name="hora" onchange="rxEnviar()"><option value="">qualquer hora</option>
        {% for k, r in rxd.HORAS %}<option value="{{ k }}" {% if fl.hora==k %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select></label>
    {% if fl.vendedor or fl.tipo or fl.mes or fl.dia or fl.conv or fl.origem or fl.hora or fl.segmento or fl.porte or fl.uf or fl.servico or fl.periodo!='mes' %}
    <a class="limpar" href="/painel/raio-x">limpar filtros</a>{% endif %}
  </form>
  <script>
    function rxEnviar(){document.getElementById('rx-form').submit();}
    function rxPeriodo(s){var d=document.getElementById('rx-datas');if(s.value==='datas'){d.hidden=false;}else{rxEnviar();}}
    {{ balao_js }}
    function rxTogg(id){
      var row=document.getElementById(id);if(!row)return;
      var cv=document.getElementById(id+'-cv');
      var fechada=row.classList.toggle('fechada');
      if(cv)cv.textContent=fechada?'▾':'▴';
    }
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
    <div class="kpi {{ 'ruim' if p.rascunhos else 'ok' if p.propostas else '' }}"><b>{{ p.propostas }}</b><span>propostas · {% if perfil.chave == 'recorrente' %}{{ brl(p.propostas_mensal) }}/mês{% else %}{{ brl(p.propostas_valor) }}{% endif %}</span>
      <em>{% if p.rascunhos %}{{ p.rascunhos }} em rascunho, nunca enviada(s){% else %}nenhum rascunho parado{% endif %}</em>
      {% if comp.propostas %}<em class="{{ comp.propostas[0] }}">{{ comp.propostas[1] }}</em>{% endif %}</div>
    <div class="kpi {{ 'ok' if p.contratos else 'amb' if p.sem_assinar else '' }}"><b>{{ p.contratos }}</b><span>contratos · {% if perfil.chave == 'recorrente' %}{{ brl(p.contratos_mensal) }}/mês{% else %}{{ brl(p.contratos_valor) }}{% endif %}</span>
      <em>{% if p.sem_assinar %}+{{ p.sem_assinar }} aprovado(s) sem assinatura{% else %}nenhum aprovado esperando assinatura{% endif %}</em>
      {# PARADO EM CASA — a espera que é NOSSA, e que o placar não mostrava.
         Só onde existe contrato pra assinar (§6): no recorrente não há documento,
         e prometer o número ali seria falar de papel pra quem vende mensalidade.
         O portão é `perfil.contrato`, o mesmo que o resto da tela usa. #}
      {% if perfil.contrato and (p.parado_em_casa is not none or p.parado_em_casa_agora) %}
      <em class="{{ 'ruim' if p.parado_em_casa_agora else '' }}">parado em casa:
        {%- if p.parado_em_casa is not none %} {{ p.parado_em_casa }} dia{{ 's' if p.parado_em_casa != 1 }} (mediana){% else %} —{% endif %}
        {%- if p.parado_em_casa_agora %} · {{ p.parado_em_casa_agora }} sem enviar agora{% endif %}</em>
      {% endif %}
      {% if comp.contratos %}<em class="{{ comp.contratos[0] }}">{{ comp.contratos[1] }}</em>{% endif %}</div>
    <div class="kpi {{ 'ok' if p.visitas_pct is not none and p.visitas_pct >= 70 else 'amb' if p.visitas_pct is not none else '' }}">
      <b>{% if p.visitas_pct is not none %}{{ p.visitas_pct }}%{% else %}—{% endif %}</b><span>{{ perfil.vocab.compromisso_kpi }}</span>
      <em>{% if p.visitas_ok + p.visitas_nao + p.visitas_sem_resposta %}{{ p.visitas_ok }} sim · {{ p.visitas_nao }} não · {{ p.visitas_sem_resposta }} sem resposta{% if not p.visitas_confiavel %} · pouco confiável{% endif %}{% else %}nenhuma {{ perfil.vocab.compromisso }} no período{% endif %}{% if p.visitas_futuras %} · {{ p.visitas_futuras }} ainda por vir{% endif %}</em></div>
  </div>
  {% else %}
  <div class="rx-dado">Não deu pra montar o placar agora. Tenta de novo em instantes.</div>
  {% endif %}

  {# DA VISITA AO CONTRATO (24/09/2026, docs/mockups/prime_visita_ao_contrato.html):
     quatro degraus do mesmo período, cada um pela sua data, e os clientes de cada
     ponta. O vocabulário é do perfil (§6): visita/propostas assinadas na festa,
     reunião/propostas aceitas no recorrente. #}
  {% set dv = d.da_visita %}
  {% if dv and 'da_visita' in perfil.blocos %}
  <div class="rx-ey">Da {{ perfil.vocab.compromisso }} ao contrato · {{ d.rotulo }}</div>
  <div class="rx-dv">
    <div class="st"><span class="l">{{ perfil.vocab.compromissos|capitalize }} realizadas</span><b>{{ dv.visitas }}</b>
      <em>{{ dv.marcadas }} marcada{{ 's' if dv.marcadas != 1 }}</em></div>
    <div class="st"><span class="l">Viraram orçamento</span><b>{{ dv.vis_orc }}</b>
      <em>{{ brl(dv.vis_orc_valor) }}{% if dv.vis_orc_pct is not none %} · <span class="ok">{{ dv.vis_orc_pct }}% das {{ perfil.vocab.compromissos }}{% if dv.sem_card %} com card{% endif %}</span>{% endif %}</em></div>
    <div class="st"><span class="l">{{ perfil.vocab.proposta_aceita|capitalize }}</span><b>{{ dv.prop_ass }}</b>
      <em>{{ brl(dv.prop_ass_valor) }}</em></div>
    <div class="st fim"><span class="l">Contratos assinados</span><b>{{ dv.contratos }}</b>
      <em>{{ brl(dv.contratos_valor) }}{% if dv.prop_ass %} · <span class="ok">{{ dv.prop_ass_com_contrato }} de {{ dv.prop_ass }} propostas</span>{% endif %}</em></div>
  </div>
  {% if dv.contratos_de_antes %}
  <div class="rx-dv-nota">{{ dv.contratos_de_antes }} contrato{{ 's' if dv.contratos_de_antes != 1 }} do período {{ 'vieram' if dv.contratos_de_antes != 1 else 'veio' }} de proposta aceita antes dele — por isso os contratos passam das propostas.</div>
  {% endif %}
  <div class="rx-dv-cli">
    {% if dv.sem_orcamento %}<div class="grp"><h4>{{ perfil.vocab.compromissos|capitalize }} sem orçamento ainda · {{ dv.sem_orcamento|length }}</h4>
      {% for n in dv.sem_orcamento %}<span class="chip">{{ n|e }}</span>{% endfor %}</div>{% endif %}
    {#- Marcada na Agenda sem dizer de qual card é: conta como {{ compromisso }},
        mas não dá pra saber se virou orçamento. Ligar ao card resolve. -#}
    {% if dv.sem_card %}<div class="grp"><h4>{{ perfil.vocab.compromissos|capitalize }} sem card no funil · {{ dv.sem_card|length }}</h4>
      {% for n in dv.sem_card %}<span class="chip amb">{{ n|e }}</span>{% endfor %}</div>{% endif %}
    {% if dv.em_jogo %}<div class="grp"><h4>Com orçamento, sem contrato ainda · {{ dv.em_jogo|length }} · {{ brl(dv.em_jogo_valor) }} em jogo</h4>
      {% for i in dv.em_jogo %}<span class="chip amb">{{ i.nome|e }} · {{ brl(i.valor_centavos) }}</span>{% endfor %}</div>{% endif %}
    {% if dv.assinaram %}<div class="grp"><h4>{{ perfil.vocab.compromissos|capitalize }} que viraram contrato · {{ dv.assinaram|length }}</h4>
      {% for n in dv.assinaram %}<span class="chip ok">{{ n|e }}</span>{% endfor %}</div>{% endif %}
  </div>
  {% if dv.linhas %}
  <div class="rx-tab"><table class="rx-vend">
    <tr><th>Contrato</th><th>Cliente</th><th>Proposta aceita</th><th>Contrato assinado</th><th class="n">Espera</th><th class="n">Valor</th></tr>
    {% for l in dv.linhas %}
    <tr><td>nº {{ l.numero }}</td><td>{{ l.nome|e }}</td>
      <td class="{{ 'amb' if l.proposta_antes }}">{{ l.proposta_em.strftime('%d/%m') if l.proposta_em else '—' }}</td>
      <td>{{ l.contrato_em.strftime('%d/%m') if l.contrato_em else '—' }}</td>
      <td class="n {{ 'ok' if l.espera is not none and l.espera <= 2 else 'amb' if l.espera is not none and l.espera <= 14 else 'ruim' if l.espera is not none else '' }}">{{ (l.espera ~ ' d') if l.espera is not none else '—' }}</td>
      <td class="n">{{ brl(l.valor_centavos) }}</td></tr>
    {% endfor %}
    <tr class="tot"><td></td><td>{{ dv.contratos }} contrato{{ 's' if dv.contratos != 1 }}</td><td>{{ dv.prop_ass }} no período</td><td>{{ dv.contratos }} no período</td>
      <td class="n">{% if dv.espera_mediana is not none %}mediana {{ dv.espera_mediana|round|int }} d{% endif %}</td><td class="n">{{ brl(dv.contratos_valor) }}</td></tr>
  </table></div>
  {% endif %}
  {% if dv_linhas %}
  <div class="rx-tab"><table class="rx-vend">
    <tr><th>Vendedor</th><th class="n">Leads</th><th class="n">{{ perfil.vocab.compromissos|capitalize }}</th><th class="n">Orçamentos</th><th class="n">{{ perfil.vocab.proposta_aceita|capitalize }}</th><th class="n">Contratos</th><th class="n">Valor</th></tr>
    {% for x in dv_linhas %}
    <tr><td><b>{{ x.nome|e }}</b></td><td class="n">{{ x.leads if x.leads is not none else '—' }}</td><td class="n">{{ x.visitas }}</td>
      <td class="n {{ 'ruim' if x.visitas and not x.vis_orc }}">{{ x.vis_orc }}</td><td class="n">{{ x.prop_ass }}</td>
      <td class="n">{{ x.contratos }}</td><td class="n">{{ brl(x.contratos_valor) }}</td></tr>
    {% endfor %}
    <tr class="tot"><td>Total do time</td><td class="n">{{ p.leads if p else '—' }}</td><td class="n">{{ dv.visitas }}</td><td class="n">{{ dv.vis_orc }}</td>
      <td class="n">{{ dv.prop_ass }}</td><td class="n">{{ dv.contratos }}</td><td class="n">{{ brl(dv.contratos_valor) }}</td></tr>
  </table></div>
  {% endif %}
  {% endif %}

  {% if d.vendedores %}
  <div class="rx-ey">Por vendedor · {{ d.rotulo }} — clique num número sublinhado pra ver quem é</div>
  <div class="rx-tab"><table class="rx-vend">
    <tr><th>Vendedor</th><th class="n">Leads</th><th class="n">1ª resposta</th><th class="n">Propostas</th><th class="n">Rascunho</th><th class="n">Toques</th><th class="n">Parou na 1ª</th><th class="n">Responda hoje</th><th>Contratos</th></tr>
    {% for v in d.vendedores %}{% set s = v.semana %}
    <tr><td><b>{{ v.nome|e }}</b></td>
      <td class="n">{{ s.leads }}</td>
      <td class="n {{ rxd.cor('primeira', s) if s.primeira_min is not none else '' }}">{{ fmt_min(s.primeira_min) }}{% if s.primeira_n %} <small>({{ s.primeira_em_5 }}/{{ s.primeira_n }})</small>{% endif %}</td>
      <td class="n">{{ s.propostas_enviadas }}</td>
      <td class="n {{ 'ruim' if s.rascunhos else '' }}{{ ' clic' if s.rascunhos_itens }}" {% if s.rascunhos_itens %}onclick="rxTogg('rx-{{ v.id }}-rasc')"{% endif %}><b>{{ s.rascunhos }}</b>{% if s.rascunhos_itens %}<span class="cv" id="rx-{{ v.id }}-rasc-cv">▾</span>{% endif %}</td>
      <td class="n">{{ s.toques }}</td>
      <td class="n {{ 'ruim' if s.paradas_1a > 5 else 'amb' if s.paradas_1a else 'ok' }}{{ ' clic' if s.paradas_1a_itens }}" {% if s.paradas_1a_itens %}onclick="rxTogg('rx-{{ v.id }}-par')"{% endif %}><b>{{ s.paradas_1a }}</b>{% if s.paradas_1a_itens %}<span class="cv" id="rx-{{ v.id }}-par-cv">▾</span>{% endif %}</td>
      <td class="n"><b>{{ v.hoje }}</b></td>
      {# CONTRATO ASSINADO E ESPERA DE ASSINATURA CONVIVEM. Era `elif`: quem
         fechou um contrato no período perdia de vista os aprovados que ainda não
         assinaram — justo o que o dono foi procurar aqui. #}
      <td>{% if s.contratos %}<span class="ok">{{ s.contratos|length }} · {{ brl(s.contratos_valor) }}</span>{% endif %}
        {%- if s.sem_assinar %}{% if s.contratos %} · {% endif %}<span class="amb clic" onclick="rxTogg('rx-{{ v.id }}-ass')"><b>{{ s.sem_assinar|length }}</b> sem assinar</span> <span class="cv" id="rx-{{ v.id }}-ass-cv">▾</span>
        {%- elif not s.contratos %}—{% endif %}</td></tr>
    {% if s.rascunhos_itens %}
    <tr class="pend-row fechada" id="rx-{{ v.id }}-rasc"><td colspan="9"><div class="pend">
      <div class="pend-cap">Rascunho · {{ s.rascunhos }} proposta(s) que nunca saíram</div>
      <div class="pend-lista">
      {% for i in s.rascunhos_itens %}<div class="pend-item"><div class="pend-nome">{{ i.nome|e }}{% if i.fone %}<span class="fone">{{ i.fone|e }}</span>{% endif %}</div>
        <div class="pend-meta">há {{ i.dias }} dia{{ 's' if i.dias != 1 }}</div>
        <div class="pend-acoes"><a class="pend-btn doc" href="/painel/servicos?abrir={{ i.orcamento_id }}">📄 abrir</a>{% if i.conversa_id %}<button type="button" class="pend-btn zap" onclick="kbAbrirChat(event,{{ i.conversa_id }},'{{ i.aba }}',this,{{ i.nome|tojson|forceescape }})">💬 conversa</button>{% endif %}</div></div>{% endfor %}
      </div>
    </div></td></tr>
    {% endif %}
    {% if s.paradas_1a_itens %}
    <tr class="pend-row fechada" id="rx-{{ v.id }}-par"><td colspan="9"><div class="pend">
      <div class="pend-cap">Parou na 1ª resposta · {{ s.paradas_1a }} lead(s) — {% if s.paradas_1a > s.paradas_1a_itens|length %}os {{ s.paradas_1a_itens|length }} que esperam há mais tempo{% else %}quem está esperando{% endif %}</div>
      <div class="pend-lista">
      {% for i in s.paradas_1a_itens %}<div class="pend-item"><div class="pend-nome">{{ i.nome|e }}{% if i.fone %}<span class="fone">{{ i.fone|e }}</span>{% endif %}</div>
        <div class="pend-meta{{ ' velha' if i.horas >= 48 }}">{{ rxd.fmt_espera(i.horas) }}</div>
        <div class="pend-acoes">{% if i.conversa_id %}<button type="button" class="pend-btn zap" onclick="kbAbrirChat(event,{{ i.conversa_id }},'{{ i.aba }}',this,{{ i.nome|tojson|forceescape }})">💬 conversa</button>{% endif %}</div></div>{% endfor %}
      </div>
      {% if s.paradas_1a > s.paradas_1a_itens|length %}<div class="pend-mais">+ {{ s.paradas_1a - s.paradas_1a_itens|length }} outro(s) — lista completa em Prospecção, filtrada por {{ v.primeiro_nome|e }}</div>{% endif %}
    </div></td></tr>
    {% endif %}
    {# DE QUEM É A BOLA, e não só "esperando assinatura". O bloco juntava dois
       estados opostos: contrato que o cliente recebeu e não assinou, e contrato
       que nunca saiu daqui. Medido na conta 34 em 14/09/2026: 35 dias somados
       parados em casa contra 1 dia esperando cliente, nos 6 contratos assinados
       — todos assinaram no mesmo dia em que receberam.
       Mockup: docs/mockups/raio_x_assinado_e_falta.html #}
    {% if s.sem_assinar %}
    {% set meus = s.sem_assinar|selectattr('estado','in',['nunca_enviado','sem_contrato'])|list %}
    {% set deles = s.sem_assinar|selectattr('estado','equalto','aguardando')|list %}
    <tr class="pend-row fechada" id="rx-{{ v.id }}-ass"><td colspan="9"><div class="pend">
      {% for grupo, itens, cap in [
           ('meu', meus, 'A bola está com você — contrato pronto e não enviado'),
           ('deles', deles, 'A bola está com o cliente — enviado, aguardando assinatura')] %}
      {% if itens %}
      <div class="pend-cap {{ 'coral' if grupo == 'meu' else '' }}">{{ cap }} · {{ itens|length }}</div>
      <div class="pend-lista">
      {% for i in itens %}<div class="pend-item"><div class="pend-nome">{{ i.nome|e }}{% if i.fone %}<span class="fone">{{ i.fone|e }}</span>{% endif %}</div>
        <div class="pend-docs">
          <div class="pd ok"><span>✓</span> Orçamento{% if i.aprovada_em %} · aprovado {{ i.aprovada_em|dia }}{% endif %}{% if i.aprovada_por %} por {{ i.aprovada_por }}{% endif %}</div>
          {% if i.estado == 'aguardando' %}
          <div class="pd amb"><span>◔</span> Contrato{% if i.contrato_numero %} nº {{ i.contrato_numero }}{% endif %} · enviado há {{ i.dias }} dia{{ 's' if i.dias != 1 }}, sem assinatura</div>
          {% elif i.estado == 'nunca_enviado' %}
          <div class="pd cor"><span>●</span> Contrato{% if i.contrato_numero %} nº {{ i.contrato_numero }}{% endif %} · pronto há {{ i.dias }} dia{{ 's' if i.dias != 1 }} — <b>nunca enviado</b></div>
          {% else %}
          <div class="pd cor"><span>●</span> Contrato ainda não foi criado — há {{ i.dias }} dia{{ 's' if i.dias != 1 }}</div>
          {% endif %}
        </div>
        <div class="pend-meta">{{ brl(i.valor_centavos) }}</div>
        <div class="pend-acoes"><a class="pend-btn doc" href="/painel/servicos?abrir={{ i.orcamento_id }}">📄 abrir</a>{% if i.conversa_id %}<button type="button" class="pend-btn zap" onclick="kbAbrirChat(event,{{ i.conversa_id }},'{{ i.aba }}',this,{{ i.nome|tojson|forceescape }})">💬 conversa</button>{% endif %}</div></div>{% endfor %}
      </div>
      {% endif %}
      {% endfor %}
    </div></td></tr>
    {% endif %}
    {% endfor %}
  </table></div>
  {% endif %}

  <div class="rx-ey">O que o Zaq enriquece sozinho</div>
  <div class="rx-grade">
    {% if 'mrr' in perfil.blocos %}
    <div class="bloco">
      <h4>Mensalidade proposta × fechada <small>MRR novo, por mês</small></h4>
      {% if d.mrr %}{% set mxm = maximo(1, (d.mrr|map(attribute='proposta')|max), (d.mrr|map(attribute='fechada')|max)) %}
      <div class="duas">
        {% for m in d.mrr %}<div><span>{{ m.rotulo }}</span><i class="a" style="width:{{ (100 * m.proposta / mxm)|round|int }}%" title="proposta {{ brl(m.proposta) }}/mês"></i><i class="b" style="width:{{ (100 * m.fechada / mxm)|round|int }}%" title="fechada {{ brl(m.fechada) }}/mês"></i></div>{% endfor %}
        <div class="lg"><span><i class="a"></i>proposta: {{ brl(d.mrr|map(attribute='proposta')|sum) }}/mês</span><span><i class="b"></i>fechada: {{ brl(d.mrr|map(attribute='fechada')|sum) }}/mês</span></div>
      </div>
      {% set prop_tot = d.mrr|map(attribute='proposta')|sum %}{% set fech_tot = d.mrr|map(attribute='fechada')|sum %}
      <div class="acha">{% if not p or not p.propostas %}<b>Nenhuma proposta enviada no período{% if p %}, com {{ p.leads }} leads novos{% endif %}.</b> O funil não está sendo trabalhado.{% elif fech_tot %}{{ (100 * fech_tot / prop_tot)|round|int if prop_tot else 0 }}% da mensalidade proposta virou contrato.{% else %}{{ brl(prop_tot) }}/mês em propostas e nenhum contrato fechado no período. O gargalo é depois da proposta.{% endif %}</div>
      {% else %}<div class="vazio">Sem dado pra este corte.</div>{% endif %}
    </div>
    {% endif %}

    {# COMISSÃO — o mesmo recorte do MRR pra quem fatura por venda, não por mês
       (perfil `seguros`). Guardas separados de propósito: até 13/09/2026 os três
       blocos abaixo estavam presos num único `{% if 'mrr' %}`, então um perfil que
       trocasse só o primeiro perdia os outros dois de brinde. #}
    {% if 'comissao' in perfil.blocos %}
    <div class="bloco">
      <h4>Comissão proposta × fechada <small>por mês, valor da apólice</small></h4>
      {% if d.comissao %}{% set mxc = maximo(1, (d.comissao|map(attribute='proposta')|max), (d.comissao|map(attribute='fechada')|max)) %}
      <div class="duas">
        {% for m in d.comissao %}<div><span>{{ m.rotulo }}</span><i class="a" style="width:{{ (100 * m.proposta / mxc)|round|int }}%" title="proposta {{ brl(m.proposta) }}"></i><i class="b" style="width:{{ (100 * m.fechada / mxc)|round|int }}%" title="fechada {{ brl(m.fechada) }}"></i></div>{% endfor %}
        <div class="lg"><span><i class="a"></i>proposta: {{ brl(d.comissao|map(attribute='proposta')|sum) }}</span><span><i class="b"></i>fechada: {{ brl(d.comissao|map(attribute='fechada')|sum) }}</span></div>
      </div>
      {% set cprop = d.comissao|map(attribute='proposta')|sum %}{% set cfech = d.comissao|map(attribute='fechada')|sum %}
      <div class="acha">{% if not p or not p.propostas %}<b>Nenhuma cotação enviada no período{% if p %}, com {{ p.leads }} leads novos{% endif %}.</b> O funil não está sendo trabalhado.{% elif cfech %}{{ (100 * cfech / cprop)|round|int if cprop else 0 }}% do que foi cotado virou apólice.{% else %}{{ brl(cprop) }} cotados e nenhuma apólice fechada no período. O gargalo é depois da cotação.{% endif %}</div>
      {% else %}<div class="vazio">Sem dado pra este corte.</div>{% endif %}
    </div>
    {% endif %}

    {% if 'segmentos' in perfil.blocos %}
    <div class="bloco">
      <h4>Segmento que chega <small>do CNPJ · e quantos fecharam</small></h4>
      {% if d.segmentos %}{% set mxs = maximo(1, d.segmentos|map(attribute='n')|max) %}
      <div class="tipos">{% for sg in d.segmentos %}<div><span>{{ sg.rotulo }}</span><i style="width:{{ (100 * sg.n / mxs)|round|int }}%"></i><span>{{ sg.n }}{% if sg.fechou %} · {{ sg.fechou }} ✓{% endif %}</span></div>{% endfor %}</div>
      {% set tot_s = d.segmentos|map(attribute='n')|sum %}{% set top = d.segmentos[0] %}
      <div class="acha">{% if top.chave != 'sem' and top.n * 2 >= tot_s %}<b>{{ (100 * top.n / tot_s)|round|int }}% dos leads é {{ top.rotulo|lower }}.</b> É o segmento pra ter proposta pronta e responder em minutos.{% elif top.chave == 'sem' %}<b>{{ top.n }} de {{ tot_s }} leads sem segmento.</b> O CNPJ na ficha preenche sozinho.{% else %}A demanda está espalhada: {{ top.rotulo|lower }} lidera com {{ top.n }}.{% endif %}</div>
      {% else %}<div class="vazio">Sem dado pra este corte.</div>{% endif %}
    </div>
    {% endif %}

    {% if 'servicos' in perfil.blocos %}
    <div class="bloco">
      <h4>{{ perfil.vocab.oferta|capitalize }} mais proposto <small>{% if d.servicos and d.servicos.historico %}sem proposta no período · tudo que já foi orçado{% else %}itens das propostas{% if 'mrr' in perfil.blocos %} · mensalidade média{% endif %}{% endif %}</small></h4>
      {% if d.servicos and d.servicos.itens %}{% set mxv = maximo(1, d.servicos.itens|map(attribute='n')|max) %}
      <div class="tipos">{% for sv in d.servicos.itens %}<div><span>{{ sv.nome|e }}</span><i style="width:{{ (100 * sv.n / mxv)|round|int }}%"></i><span>{{ sv.n }}×{% if sv.mensal_centavos %} · {{ brl(sv.mensal_centavos) }}/mês{% endif %}</span></div>{% endfor %}</div>
      <div class="acha">{% if d.servicos.historico %}Ticket por {{ perfil.vocab.oferta }} só depois da primeira proposta enviada no período. Até lá, o que mais entrou em orçamento.{% else %}<b>{{ d.servicos.itens[0].nome }}</b> é o que mais entra em proposta. É o {{ perfil.vocab.oferta }} pra ter pacote e preço prontos.{% endif %}</div>
      {% else %}<div class="vazio">Nenhum orçamento com itens ainda.</div>{% endif %}
    </div>
    {% endif %}
    {% if 'demanda_agenda' in perfil.blocos %}
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
    {% endif %}

    {% if 'dia_festa' in perfil.blocos %}
    <div class="bloco">
      {% set tot_dia = (d.dia_festa|map(attribute='n')|sum) if d.dia_festa else 0 %}
      <h4>Dia da festa <small>{{ tot_dia }} lead(s) com data</small></h4>
      {% if d.dia_festa and tot_dia %}{% set mxd = maximo(1, d.dia_festa|map(attribute='n')|max) %}
      <div class="sem">{% for x in d.dia_festa %}<div><i class="{{ 'on' if x.n == mxd }}" style="height:{{ (100 * x.n / mxd)|round|int }}%"></i>{{ x.rotulo }}<br>{{ x.n }}</div>{% endfor %}</div>
      {% set sab = d.dia_festa[6].n %}
      <div class="acha"><b>{{ (100 * sab / tot_dia)|round|int }}% das festas pedidas caem no sábado.</b> {% if sab / tot_dia >= 0.5 %}Sábado é o produto escasso: vale tabela própria, lista de espera por data, e sexta e domingo com condição melhor pra quem tem data flexível.{% else %}A demanda está espalhada na semana; o sábado não é o gargalo neste corte.{% endif %}</div>
      {% else %}<div class="vazio">Nenhum lead com data neste corte.</div>{% endif %}
    </div>
    {% endif %}

    {% if 'tipos' in perfil.blocos %}
    <div class="bloco">
      {#- "no período" no subtítulo NÃO é enfeite: até 16/09/2026 o ticket vinha
          dos leads que entraram no período, e não das propostas feitas nele —
          proposta de setembro pra lead de agosto sumia, e o tipo aparecia como
          "sem proposta" tendo proposta fechada. Ver `_tipos_ticket`. -#}
      <h4>Tipo de festa e ticket <small>proposta média por tipo, no período</small></h4>
      {% if d.tipos %}{% set com_ticket = d.tipos|selectattr('ticket_centavos')|list %}{% set mxt = maximo(1, (com_ticket|map(attribute='ticket_centavos')|max) if com_ticket else 1) %}
      {#- OS DOIS NÚMEROS, e separados: o de leads é a procura ("entraram 38
          casamentos"), o de propostas é de onde sai o ticket ("de 3 propostas").
          Lado a lado sem rótulo, como era antes, "(38)" colado em "R$ 7.433"
          se lia como se os 38 tivessem dado aquele ticket. -#}
      <div class="tipos">{% for t in d.tipos if t.tipo != 'sem tipo' %}<div><span>{{ t.tipo }} <small>{{ t.n }} lead{{ '' if t.n == 1 else 's' }}{% if t.n_orc %} · {{ t.n_orc }} proposta{{ '' if t.n_orc == 1 else 's' }}{% endif %}</small></span><i style="width:{{ ((100 * (t.ticket_centavos or 0) / mxt)|round|int) }}%"></i><span>{% if t.ticket_centavos %}{{ brl(t.ticket_centavos) }}{% else %}sem proposta no período{% endif %}</span></div>{% endfor %}</div>
      {% set tot_t = d.tipos|map(attribute='n')|sum %}{% set sem_t = (d.tipos|selectattr('tipo', 'equalto', 'sem tipo')|map(attribute='n')|sum) %}
      <div class="acha">{% if sem_t %}<b>{{ sem_t }} dos {{ tot_t }} leads ({{ (100 * sem_t / tot_t)|round|int }}%) estão sem tipo de festa.</b> Sem o tipo, o Zaq não sabe o ticket nem qual pacote sugerir: é a 2ª pergunta da primeira resposta.{% else %}Todo lead deste corte tem tipo de festa. O ticket por tipo é o que orienta a proposta.{% endif %}</div>
      {% else %}<div class="vazio">Sem dado pra este corte.</div>{% endif %}
    </div>
    {% endif %}

    {% if 'reunioes' in perfil.blocos and p %}
    <div class="bloco">
      <h4>{{ perfil.vocab.compromisso_kpi|capitalize }} <small>na agenda, no período</small></h4>
      <p><b>{{ p.visitas_ok }}</b> aconteceram · <b>{{ p.visitas_nao }}</b> não · <b>{{ p.visitas_sem_resposta }}</b> sem desfecho marcado.</p>
      <div class="acha">{% if p.visitas_sem_resposta and p.visitas_sem_resposta >= p.visitas_ok %}<b>{{ p.visitas_sem_resposta }} {{ perfil.vocab.compromissos }} sem desfecho.</b> Sem o "aconteceu / não aconteceu" a taxa não vale: é um toque na Agenda depois de cada uma.{% elif p.visitas_ok + p.visitas_nao %}{{ p.visitas_pct }}% das {{ perfil.vocab.compromissos }} marcadas aconteceram.{% else %}Nenhuma {{ perfil.vocab.compromisso }} no período. Proposta sem {{ perfil.vocab.compromisso }} fecha menos.{% endif %}</div>
    </div>
    {% endif %}

    <div class="bloco">
      <h4>Do lead à proposta, da proposta ao contrato <small>dias, mediana</small></h4>
      {% if d.ciclo %}
      <p>Lead → proposta: {% if d.ciclo.lead_proposta_dias is not none %}<b>{{ d.ciclo.lead_proposta_dias }} dias</b> ({{ d.ciclo.lead_proposta_n }} proposta(s)){% for v in d.ciclo.por_vendedor %} · {{ v.nome|e }} <b>{{ v.dias }}</b>{% endfor %}{% else %}nenhuma proposta enviada no período{% endif %}.
        Proposta → contrato: {% if d.ciclo.proposta_contrato_dias is not none %}<b>{{ d.ciclo.proposta_contrato_dias }} dias</b> ({{ d.ciclo.proposta_contrato_n }} contrato(s)){% else %}nenhum contrato assinado no período{% endif %}.</p>
      <div class="acha">{% if d.ciclo.lead_proposta_dias is not none and d.ciclo.lead_proposta_dias > 1 %}Meta sugerida: proposta em 24h depois de data e convidados. Hoje a mediana é {{ d.ciclo.lead_proposta_dias }} dias.{% if d.ciclo.proposta_contrato_dias is not none and d.ciclo.proposta_contrato_dias <= 2 %} Quando a proposta sai, o contrato vem rápido: o gargalo é a proposta sair.{% endif %}{% elif d.ciclo.lead_proposta_dias is not none %}Proposta em até um dia: dentro da meta.{% else %}Sem proposta no período não há ciclo pra medir.{% endif %}</div>
      {% else %}<div class="vazio">Sem dado pra este corte.</div>{% endif %}
    </div>

    <div class="bloco">
      <h4>Por que perdeu <small>{{ d.perdas.total if d.perdas else 0 }} perdido(s) no período</small></h4>
      {% if d.perdas %}
      <div class="perdas">{% for x in d.perdas.itens %}<span class="{{ 'on' if x.n }}">{{ x.rotulo|lower }} · {{ x.n }}</span>{% endfor %}<span>sem motivo · {{ d.perdas.sem_motivo }}</span></div>
      <div class="acha">{% if d.perdas.sem_motivo and d.perdas.total and d.perdas.sem_motivo * 2 >= d.perdas.total %}<b>{{ d.perdas.sem_motivo }} de {{ d.perdas.total }} sem motivo.</b> O motivo é um toque numa lista de seis ao marcar perdido, no app e na ficha.{% elif d.perdas.total %}{% if perfil.chave == 'eventos' %}"Data indisponível" alimenta a lista de espera por data; "achou caro" alimenta a tabela de sábado.{% else %}"Ficou com o fornecedor atual" diz contra quem a proposta perdeu; "achou caro" alimenta a tabela.{% endif %}{% elif p and p.leads and not p.propostas %}Ninguém foi marcado como perdido, e nenhuma proposta saiu: parte dos {{ p.leads }} leads já esfriou sem ninguém dizer por quê.{% else %}Nenhum lead perdido no período.{% endif %}</div>
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
