"""A aba Obras: /painel/obras — cada casa e cada reforma da construtora.

PR 2 de 4 do desenho aprovado pelo dono em 25/09/2026
(docs/mockups/nicho_construcao.html, seções 05 e 06). A primeira conta é a PX2
(conta 33), que constrói casa popular pelo Minha Casa Minha Vida e faz reforma.

SÓ APARECE PRA CONSTRUÇÃO (regra 6): o perfil do nicho tem que ser `obras`, como
Renovações só abre pra `seguros`. Quem barra o nicho é a rota; a whitelist de
papel (contas.equipe.rotas_do_papel) não conhece nicho.

QUEM VÊ: dono, gestor e financeiro — obra aqui é custo, e custo é financeiro. O
vendedor não tem a aba.

A OBRA NASCE AQUI e em nenhum outro lugar (decisão 1 do dono): o agente do
WhatsApp manda o link desta tela quando pedem obra nova, porque criar obra cria
centro de custo, e o agente não cria centro (regra do dono de 23/09).

Tudo que escreve é ação explícita de quem está logado na conta dele: criar,
editar, marcar etapa e pôr um gasto na obra. Nada apaga lançamento.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import obras as ob
from finance import raio_x_perfil as rxp
from web.portal import _env, _render, conta_logada, nicho_da_conta

router = APIRouter()
_log = logging.getLogger("openclaw.painel_obras")

_PAPEIS_OK = ("dono", "gestor", "financeiro")


def _acesso(request: Request):
    """Devolve (conta, redirect)."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if request.session.get("papel", "dono") not in _PAPEIS_OK:
        return None, RedirectResponse("/painel", status_code=303)
    # Regra 6: a tela segue o nicho. `obras` e mais nenhum.
    if rxp.perfil_por_nicho(nicho_da_conta(conta)) != "obras":
        return None, RedirectResponse("/painel", status_code=303)
    return conta, None


def _brl(centavos) -> str:
    return ob._brl(centavos)


def _cent(txt: str):
    """'82.000,00' / '82000' / '' → centavos. Vazio é None: previsto e valor
    podem não existir ainda, e 0 diria que existem e são zero."""
    t = (txt or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        v = int(round(float(t) * 100))
    except ValueError:
        return None
    return v if v >= 0 else None


def _num(txt: str):
    t = (txt or "").strip().replace(",", ".")
    try:
        v = float(t) if t else None
    except ValueError:
        return None
    return v if v and v > 0 else None


def _data(txt: str):
    t = (txt or "").strip()
    try:
        return datetime.strptime(t, "%Y-%m-%d").date() if t else None
    except ValueError:
        return None


def _volta(url: str, erro: str = "") -> RedirectResponse:
    if erro:
        from urllib.parse import quote
        url += ("&" if "?" in url else "?") + "erro=" + quote(erro)
    return RedirectResponse(url, status_code=303)


# ─────────────────────────────────────────────────────────────── a lista
@router.get("/painel/obras", response_class=HTMLResponse)
def painel_obras(request: Request):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    obras = ob.listar_obras(pool, conta[0], incluir_arquivadas=True)
    abertas = [o for o in obras if o["status"] not in ("arquivada",)]
    andamento = [o for o in abertas if o["status"] in ("em_obra", "pronta")]
    return _render(
        "obras", request, titulo="Obras", secao_ativa="obras", brl=_brl,
        obras=abertas, arquivadas=[o for o in obras if o["status"] == "arquivada"],
        n_andamento=len(andamento),
        # o botão Dividir reparte entre as EM OBRA (a mesma regra de `lancamento_na_obra`)
        n_em_obra=sum(1 for o in abertas if o["status"] == "em_obra"),
        gasto_andamento=sum(o["custos"]["total"] for o in andamento),
        sem=ob.sem_obra(pool, conta[0]),
        escolhas=[o for o in abertas if o["status"] != "entregue"],
        tipos=ob.ROTULO_TIPO, hoje=date.today(),
        erro=(request.query_params.get("erro") or "").strip())


@router.post("/painel/obras/nova")
def criar(request: Request, nome: str = Form(""), tipo: str = Form("casa"),
          endereco: str = Form(""), area_m2: str = Form(""),
          custo_previsto: str = Form(""), valor: str = Form(""),
          inicio_em: str = Form(""), previsao_em: str = Form("")):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        o = ob.criar_obra(get_pool(), conta[0], nome, tipo, endereco=endereco,
                          area_m2=_num(area_m2), custo_previsto_centavos=_cent(custo_previsto),
                          valor_centavos=_cent(valor), inicio_em=_data(inicio_em),
                          previsao_em=_data(previsao_em),
                          criado_por=request.session.get("membro_id"))
    except ValueError as e:
        return _volta("/painel/obras", str(e))
    return RedirectResponse(f"/painel/obras/{o['id']}", status_code=303)


@router.post("/painel/obras/lancamento")
def lancamento_na_obra(request: Request, lancamento_id: int = Form(...),
                       obra_id: str = Form(""), acao: str = Form("por")):
    """Da lista "Sem obra": o gasto inteiro numa obra, ou dividido entre as obras
    em andamento. `voltar` é a própria lista, que é de onde o botão vem."""
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    try:
        if acao == "dividir":
            alvo = [o["id"] for o in ob.listar_obras(pool, conta[0], com_custos=False)
                    if o["status"] == "em_obra"]
            ob.dividir(pool, conta[0], lancamento_id, alvo)
        else:
            if not obra_id.strip().isdigit():
                raise ValueError("Escolha a obra.")
            ob.por_na_obra(pool, conta[0], lancamento_id, int(obra_id))
    except ValueError as e:
        return _volta("/painel/obras#sem-obra", str(e))
    return RedirectResponse("/painel/obras#sem-obra", status_code=303)


# ─────────────────────────────────────────────────────────────── a ficha
@router.get("/painel/obras/{obra_id}", response_class=HTMLResponse)
def ficha(request: Request, obra_id: int):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    o = ob.obter_obra(get_pool(), conta[0], obra_id)
    if not o:
        return RedirectResponse("/painel/obras", status_code=303)
    return _render("obra", request, titulo=o["nome"], secao_ativa="obras", brl=_brl,
                   o=o, tipos=ob.ROTULO_TIPO, status=ob.ROTULO_STATUS,
                   rotulo_custo=ob.ROTULO_CUSTO,
                   erro=(request.query_params.get("erro") or "").strip())


@router.post("/painel/obras/{obra_id}/editar")
def editar(request: Request, obra_id: int, nome: str = Form(""), tipo: str = Form("casa"),
           endereco: str = Form(""), area_m2: str = Form(""),
           custo_previsto: str = Form(""), valor: str = Form(""),
           status: str = Form("em_obra"), inicio_em: str = Form(""),
           previsao_em: str = Form(""), obs: str = Form("")):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        ob.editar_obra(get_pool(), conta[0], obra_id, nome=nome, tipo=tipo,
                       endereco=endereco.strip(), area_m2=_num(area_m2),
                       custo_previsto_centavos=_cent(custo_previsto),
                       valor_centavos=_cent(valor), status=status,
                       inicio_em=_data(inicio_em), previsao_em=_data(previsao_em),
                       obs=obs.strip())
    except ValueError as e:
        return _volta(f"/painel/obras/{obra_id}", str(e))
    return RedirectResponse(f"/painel/obras/{obra_id}", status_code=303)


@router.post("/painel/obras/{obra_id}/etapa")
def etapa(request: Request, obra_id: int, etapa_id: int = Form(...),
          concluida: str = Form("1")):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        ob.marcar_etapa(get_pool(), conta[0], obra_id, etapa_id, concluida=concluida == "1")
    except ValueError as e:
        return _volta(f"/painel/obras/{obra_id}", str(e))
    return RedirectResponse(f"/painel/obras/{obra_id}#etapas", status_code=303)


@router.post("/painel/obras/{obra_id}/etapas")
async def etapas(request: Request, obra_id: int):
    """A lista inteira de etapas, editada: nome e peso de cada uma, a linha vazia do
    fim pra acrescentar, e a marca de tirar. Chave vazia é etapa nova."""
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    form = await request.form()
    chaves, nomes, pesos = (form.getlist(k) for k in ("chave", "nome", "peso"))
    tirar = set(form.getlist("tirar"))
    linhas = [(ch or None, nm, _num(ps) or 0)
              for ch, nm, ps in zip(chaves, nomes, pesos) if not (ch and ch in tirar)]
    try:
        ob.salvar_etapas(get_pool(), conta[0], obra_id, linhas)
    except ValueError as e:
        return _volta(f"/painel/obras/{obra_id}", str(e))
    return RedirectResponse(f"/painel/obras/{obra_id}#etapas", status_code=303)


# ─────────────────────────────────────────────────────────────── as telas
_CSS = r"""<style>
/* mesma medida das outras telas de trabalho (Renovações, Raio-X): `--pag` vem de
   web/tema.py */
.ob-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.ob-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.ob-topo h2{margin:0;font-size:1.5rem;line-height:1.15}
.ob-sub{color:var(--txt-mut);font-size:.88rem;margin-top:.25rem}
.ob-volta{font-size:.85rem;color:var(--txt-mut);text-decoration:none}
.ob-faixas{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem;margin:1rem 0}
.ob-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.55rem .75rem;text-decoration:none;color:inherit}
.ob-cx .r{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut);display:block}
.ob-cx .v{font-size:1.3rem;font-weight:700;line-height:1.2;display:block}
.ob-cx .n{font-size:.72rem;color:var(--txt-mut)}
.ob-cx.alerta{background:var(--ambar-fundo);border-color:var(--ambar-borda)}
.ob-lista{display:flex;flex-direction:column;gap:.45rem}
.ob-card{display:grid;grid-template-columns:1.3fr 1.3fr 1fr 1fr;gap:.8rem;align-items:center;background:var(--card);
  border:1px solid var(--borda);border-radius:11px;padding:.65rem .85rem;text-decoration:none;color:inherit}
.ob-card:hover{border-color:var(--verde)}
.ob-card .nm{font-weight:700}
.ob-mut{color:var(--txt-mut);font-size:.78rem}
.ob-bar{height:7px;background:var(--borda);border-radius:4px;overflow:hidden;margin:.25rem 0 .1rem}
.ob-bar i{display:block;height:100%;background:var(--verde)}
.ob-bar i.alto{background:#D9932B}
.ob-pill{display:inline-block;border-radius:14px;padding:2px 9px;font-size:.72rem;border:1px solid var(--borda);color:var(--txt-mut);white-space:nowrap}
.ob-pill.pronta,.ob-pill.vendida{border-color:var(--verde);color:var(--verde-claro)}
.ob-sec{margin:1.6rem 0 .6rem;font-size:1.05rem}
.ob-box{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin-bottom:.6rem}
.ob-box summary{cursor:pointer;font-weight:600}
.ob-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.6rem;margin-top:.6rem}
.ob-box label{display:block;font-size:.7rem;color:var(--txt-mut);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.2rem}
.ob-box input,.ob-box select,.ob-box textarea{width:100%;box-sizing:border-box}
.ob-erro{background:var(--neon-fundo);border:1px solid var(--neon-borda);border-radius:9px;padding:.55rem .75rem;margin:.8rem 0}
.ob-tab{width:100%;border-collapse:collapse;font-size:.85rem}
.ob-tab td,.ob-tab th{padding:.45rem .5rem;border-bottom:1px solid var(--borda);text-align:left;vertical-align:middle}
.ob-tab th{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut)}
.ob-tab td.v{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.ob-rolo{overflow-x:auto}
.ob-acoes{display:flex;gap:.35rem;flex-wrap:wrap;align-items:center}
.ob-acoes select{width:auto;min-width:9rem}
.ob-bt{padding:.35rem .7rem;border-radius:7px;border:1px solid var(--borda);background:transparent;color:inherit;cursor:pointer;font-size:.8rem}
.ob-bt.prim{background:var(--verde);border-color:var(--verde);color:#fff}
.ob-etapas{display:flex;flex-direction:column;gap:.3rem}
.ob-et{display:flex;justify-content:space-between;align-items:center;gap:.6rem;padding:.35rem .5rem;border:1px solid var(--borda);border-radius:8px}
.ob-et.feita{border-color:var(--verde)}
.ob-et form{margin:0}
.ob-custo{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem}
@media (max-width:760px){.ob-card{grid-template-columns:1fr 1fr}}
</style>"""

_TPL_LISTA = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="ob-pag">
<div class="ob-topo"><div><h2>Obras</h2>
  <div class="ob-sub">Cada casa e cada reforma: o que já custou, contra o previsto, e em que etapa está.</div></div></div>
{% if erro %}<div class="ob-erro">{{ erro|e }}</div>{% endif %}

<div class="ob-faixas">
  <div class="ob-cx"><span class="r">Em andamento</span><span class="v">{{ n_andamento }}</span>
    <span class="n">em obra ou prontas</span></div>
  <div class="ob-cx"><span class="r">Gasto nessas obras</span><span class="v">{{ brl(gasto_andamento) }}</span>
    <span class="n">dinheiro que ainda está na obra</span></div>
  {% if sem.n %}<a class="ob-cx alerta" href="#sem-obra"><span class="r">Sem obra</span>
    <span class="v">{{ sem.n }}</span><span class="n">{{ brl(sem.total_centavos) }} de material e mão de obra sem casa</span></a>{% endif %}
</div>

{% if obras %}<div class="ob-lista">
{% for o in obras %}<a class="ob-card" href="/painel/obras/{{ o.id }}">
  <div><span class="nm">{{ '🏠' if o.tipo == 'casa' else '🔨' }} {{ o.nome|e }}</span>
    <div class="ob-mut">{{ o.endereco|e or o.rotulo_tipo }}{% if o.area_m2 %} · {{ '%g'|format(o.area_m2) }} m²{% endif %}</div></div>
  <div><span class="ob-mut">Etapas · {{ o.pct }}%</span>
    <div class="ob-bar"><i style="width:{{ o.pct }}%"></i></div>
    <span class="ob-mut">{{ ('próxima: ' ~ o.proxima_etapa|lower|e) if o.proxima_etapa else 'todas feitas' }}</span></div>
  <div><b>{{ brl(o.custos.total) }}</b>
    <div class="ob-mut">{% if o.custo_previsto_centavos %}de {{ brl(o.custo_previsto_centavos) }} previstos ({{ o.pct_previsto }}%){% else %}sem previsto{% endif %}</div></div>
  <div><span class="ob-pill {{ o.status }}">{{ o.rotulo_status }}</span></div>
</a>{% endfor %}
</div>{% endif %}

<details class="ob-box" {% if not obras %}open{% endif %}><summary>+ Nova obra</summary>
<form method="post" action="/painel/obras/nova">
  <div class="ob-grid">
    <div><label>Nome</label><input name="nome" placeholder="Casa 4" required></div>
    <div><label>Tipo</label><select name="tipo">{% for k, r in tipos.items() %}<option value="{{ k }}">{{ r }}</option>{% endfor %}</select></div>
    <div><label>Lote / endereço</label><input name="endereco" placeholder="Qd 4 · Lt 14"></div>
    <div><label>Área (m²)</label><input name="area_m2" inputmode="decimal" placeholder="45"></div>
    <div><label>Custo previsto (R$)</label><input name="custo_previsto" inputmode="decimal" placeholder="82.000,00"></div>
    <div><label>Venda prevista ou contrato (R$)</label><input name="valor" inputmode="decimal"></div>
    <div><label>Início</label><input type="date" name="inicio_em"></div>
    <div><label>Previsão de término</label><input type="date" name="previsao_em"></div>
  </div>
  <p class="ob-mut" style="margin:.6rem 0">A obra ganha um centro de custo com o mesmo nome — é por ele que o assistente
  do WhatsApp lança cada nota na casa certa. As etapas vêm da sua última obra do mesmo tipo.</p>
  <button class="ob-bt prim">Criar obra</button>
</form></details>

<h3 class="ob-sec" id="sem-obra">Sem obra{% if sem.n %} · {{ sem.n }}{% endif %}</h3>
{% if sem.n %}
<p class="ob-mut">Material e mão de obra da empresa que ainda não dizem de qual casa são. Ponha cada um na sua obra —
ou divida entre as obras em andamento, quando for de todas.</p>
<div class="ob-rolo"><table class="ob-tab">
<tr><th>Data</th><th>Descrição</th><th style="text-align:right">Valor</th><th>Obra</th></tr>
{% for i in sem.itens %}<tr>
  <td>{{ i.data.strftime('%d/%m/%Y') }}</td><td>{{ i.descricao|e }}</td><td class="v">{{ brl(i.valor_centavos) }}</td>
  <td>{% if escolhas %}<div class="ob-acoes">
    <form method="post" action="/painel/obras/lancamento" class="ob-acoes">
      <input type="hidden" name="lancamento_id" value="{{ i.id }}">
      <select name="obra_id">{% for o in escolhas %}<option value="{{ o.id }}">{{ o.nome|e }}</option>{% endfor %}</select>
      <button class="ob-bt">Pôr</button></form>
    {% if n_em_obra > 1 %}<form method="post" action="/painel/obras/lancamento">
      <input type="hidden" name="lancamento_id" value="{{ i.id }}"><input type="hidden" name="acao" value="dividir">
      <button class="ob-bt" title="partes iguais entre as obras em andamento">Dividir</button></form>{% endif %}
  </div>{% else %}<span class="ob-mut">crie uma obra primeiro</span>{% endif %}</td>
</tr>{% endfor %}
</table></div>
{% if sem.n > sem.itens|length %}<p class="ob-mut">Mostrando os {{ sem.itens|length }} mais recentes.</p>{% endif %}
{% else %}<p class="ob-mut">Nenhum gasto de obra sem obra. ✅</p>{% endif %}

{% if arquivadas %}<details class="ob-box" style="margin-top:1.4rem"><summary>Arquivadas · {{ arquivadas|length }}</summary>
<div class="ob-lista" style="margin-top:.6rem">{% for o in arquivadas %}<a class="ob-card" href="/painel/obras/{{ o.id }}">
  <div><span class="nm">{{ o.nome|e }}</span><div class="ob-mut">{{ o.rotulo_tipo }}</div></div>
  <div class="ob-mut">Etapas · {{ o.pct }}%</div><div><b>{{ brl(o.custos.total) }}</b></div>
  <div><span class="ob-pill">Arquivada</span></div></a>{% endfor %}</div></details>{% endif %}
</div>
{% endblock %}"""

_TPL_FICHA = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="ob-pag">
<a class="ob-volta" href="/painel/obras">← Obras</a>
<div class="ob-topo" style="margin-top:.4rem"><div>
  <h2>{{ '🏠' if o.tipo == 'casa' else '🔨' }} {{ o.nome|e }}</h2>
  <div class="ob-sub">{{ o.rotulo_tipo }}{% if o.endereco %} · {{ o.endereco|e }}{% endif %}{% if o.area_m2 %} · {{ '%g'|format(o.area_m2) }} m²{% endif %}
    · <span class="ob-pill {{ o.status }}">{{ o.rotulo_status }}</span></div></div></div>
{% if erro %}<div class="ob-erro">{{ erro|e }}</div>{% endif %}

<div class="ob-faixas">
  <div class="ob-cx"><span class="r">Gasto</span><span class="v">{{ brl(o.custos.total) }}</span>
    <span class="n">{% if o.custo_previsto_centavos %}{{ o.pct_previsto }}% de {{ brl(o.custo_previsto_centavos) }} previstos{% else %}sem previsto cadastrado{% endif %}</span></div>
  <div class="ob-cx"><span class="r">Etapas</span><span class="v">{{ o.pct }}%</span>
    <span class="n">{{ ('próxima: ' ~ o.proxima_etapa|lower|e) if o.proxima_etapa else 'todas feitas' }}</span></div>
  {% if o.custo_m2 %}<div class="ob-cx"><span class="r">Custo por m²</span><span class="v">{{ brl(o.custo_m2) }}</span>
    <span class="n">do que já foi gasto</span></div>{% endif %}
  <div class="ob-cx"><span class="r">{{ 'Venda prevista' if o.tipo == 'casa' else 'Contrato' }}</span>
    <span class="v">{{ brl(o.valor_centavos) if o.valor_centavos else '—' }}</span>
    <span class="n">recebido nesta obra: {{ brl(o.custos.recebido) }}</span></div>
</div>

<div class="ob-box"><b>O custo</b>
  <div class="ob-custo" style="margin-top:.5rem">
    {% for k in ('material', 'mao_de_obra', 'outros') %}<div class="ob-cx"><span class="r">{{ rotulo_custo[k] }}</span>
      <span class="v" style="font-size:1.1rem">{{ brl(o.custos[k]) }}</span></div>{% endfor %}
  </div>
  <p class="ob-mut" style="margin:.5rem 0 0">Material é o que foi lançado em Insumos (conta 3.1.03); mão de obra, o de
  Serviços (conta 3.1.04). O resto cai em outros.</p>
</div>

<h3 class="ob-sec" id="etapas">Etapas · {{ o.pct }}%</h3>
<div class="ob-bar" style="height:9px;margin-bottom:.6rem"><i style="width:{{ o.pct }}%"></i></div>
<div class="ob-etapas">{% for e in o.etapas %}
  <div class="ob-et{{ ' feita' if e.concluida_em }}">
    <span>{{ '✓' if e.concluida_em else '○' }} {{ e.nome|e }} <span class="ob-mut">· {{ '%g'|format(e.peso) }}%{% if e.concluida_em %} · {{ e.concluida_em.strftime('%d/%m') }}{% endif %}</span></span>
    <form method="post" action="/painel/obras/{{ o.id }}/etapa">
      <input type="hidden" name="etapa_id" value="{{ e.id }}"><input type="hidden" name="concluida" value="{{ '0' if e.concluida_em else '1' }}">
      <button class="ob-bt">{{ 'Desmarcar' if e.concluida_em else 'Concluída' }}</button></form>
  </div>{% endfor %}</div>

<details class="ob-box" style="margin-top:.6rem"><summary>Editar etapas e pesos</summary>
<form method="post" action="/painel/obras/{{ o.id }}/etapas">
  <p class="ob-mut">O peso é quanto a etapa vale no andamento da obra. A próxima obra do mesmo tipo começa com esta lista.</p>
  <table class="ob-tab"><tr><th>Etapa</th><th>Peso (%)</th><th>Tirar</th></tr>
  {% for e in o.etapas %}<tr><td><input type="hidden" name="chave" value="{{ e.chave|e }}"><input name="nome" value="{{ e.nome|e }}"></td>
    <td><input name="peso" value="{{ '%g'|format(e.peso) }}" inputmode="decimal" style="max-width:6rem"></td>
    <td><input type="checkbox" name="tirar" value="{{ e.chave|e }}" style="width:auto"></td></tr>{% endfor %}
  <tr><td><input type="hidden" name="chave" value=""><input name="nome" placeholder="etapa nova"></td>
    <td><input name="peso" inputmode="decimal" style="max-width:6rem"></td><td></td></tr>
  </table>
  <button class="ob-bt prim" style="margin-top:.5rem">Salvar etapas</button>
</form></details>

<h3 class="ob-sec">Os lançamentos desta obra</h3>
{% if o.lancamentos %}<div class="ob-rolo"><table class="ob-tab">
<tr><th>Data</th><th>Descrição</th><th>Tipo</th><th style="text-align:right">Valor</th></tr>
{% for l in o.lancamentos %}<tr><td>{{ l.data.strftime('%d/%m/%Y') }}</td><td>{{ l.descricao|e }}</td>
  <td>{{ 'Recebido' if l.tipo_custo == 'receita' else rotulo_custo[l.tipo_custo] }}</td>
  <td class="v">{{ brl(l.valor_centavos) }}{% if l.dividido %}<div class="ob-mut">parte de {{ brl(l.valor_inteiro_centavos) }}</div>{% endif %}</td></tr>{% endfor %}
</table></div>
{% else %}<p class="ob-mut">Nada lançado nesta obra ainda. No WhatsApp, é só mandar a foto da nota e dizer que é da {{ o.nome|e }}.</p>{% endif %}

<details class="ob-box" style="margin-top:1.2rem"><summary>Dados da obra</summary>
<form method="post" action="/painel/obras/{{ o.id }}/editar">
  <div class="ob-grid">
    <div><label>Nome</label><input name="nome" value="{{ o.nome|e }}" required></div>
    <div><label>Tipo</label><select name="tipo">{% for k, r in tipos.items() %}<option value="{{ k }}"{{ ' selected' if k == o.tipo }}>{{ r }}</option>{% endfor %}</select></div>
    <div><label>Situação</label><select name="status">{% for k, r in status.items() %}<option value="{{ k }}"{{ ' selected' if k == o.status }}>{{ r }}</option>{% endfor %}</select></div>
    <div><label>Lote / endereço</label><input name="endereco" value="{{ o.endereco|e }}"></div>
    <div><label>Área (m²)</label><input name="area_m2" value="{{ '%g'|format(o.area_m2) if o.area_m2 else '' }}" inputmode="decimal"></div>
    <div><label>Custo previsto (R$)</label><input name="custo_previsto" value="{{ '%.2f'|format(o.custo_previsto_centavos / 100) if o.custo_previsto_centavos is not none else '' }}" inputmode="decimal"></div>
    <div><label>Venda prevista ou contrato (R$)</label><input name="valor" value="{{ '%.2f'|format(o.valor_centavos / 100) if o.valor_centavos is not none else '' }}" inputmode="decimal"></div>
    <div><label>Início</label><input type="date" name="inicio_em" value="{{ o.inicio_em or '' }}"></div>
    <div><label>Previsão de término</label><input type="date" name="previsao_em" value="{{ o.previsao_em or '' }}"></div>
  </div>
  <div style="margin-top:.6rem"><label>Observação</label><textarea name="obs" rows="2">{{ o.obs|e }}</textarea></div>
  <p class="ob-mut" style="margin:.6rem 0">Mudar o nome muda o centro de custo junto. Arquivar tira a obra das listas sem
  apagar nada do que foi lançado nela.</p>
  <button class="ob-bt prim">Salvar</button>
</form></details>
</div>
{% endblock %}"""

_env.loader.mapping["obras"] = _TPL_LISTA
_env.loader.mapping["obra"] = _TPL_FICHA
