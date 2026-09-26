"""Produto junto do atendimento (finance/clinica_produtos.py).

  /painel/clinica/produtos                    reposição chegando, o que vence logo, os produtos
                                              (duração), entrada com validade, venda (?lead=)
  /painel/clinica/produtos/vender             vender (do agendamento: evento_id; da reposição: lead)
  /painel/clinica/produtos/entrada            entrada de estoque com a validade do lote
  /painel/clinica/produtos/{id}/duracao       a duração do produto (dono e gestor)
  /painel/clinica/produtos/recompra/{id}/dispensar
  /painel/clinica/produtos/config             lembrete de reposição ligado/desligado (dono e gestor)

O cadastro completo do produto (nome, preço, foto, perda) continua em /painel/produtos.
"""
from __future__ import annotations

from datetime import datetime, time, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import clinica_agenda as ca
from finance import clinica_config as cc
from finance import clinica_produtos as cpr
from web.painel_clinica_agenda import _acesso
from web.portal import _env, _render

router = APIRouter()
URL = "/painel/clinica/produtos"
_AVISOS = {"vendido": "Venda registrada: o estoque baixou e a receita está no Financeiro.",
           "entrada": "Entrada registrada, com a validade do lote.", "duracao": "Duração salva.",
           "dispensado": "Reposição tirada da lista.", "config": "Configuração salva."}


def _int(v) -> int | None:
    s = str(v or "").strip()
    return int(s) if s.isdigit() else None


def _ir(request: Request, url: str, aviso: str = "", erro: str = "") -> RedirectResponse:
    if erro:
        # a venda do agendamento volta pra tela dele, que lê o próprio erro
        chave = "agenda_erro" if url.startswith("/painel/clinica/agenda/") else "produtos_erro"
        request.session[chave] = erro[:300]
    if aviso in _AVISOS:
        url += ("&" if "?" in url else "?") + f"aviso={aviso}"
    return RedirectResponse(url, status_code=303)


def _volta(evento_id: int | None, lead: int | None) -> str:
    if evento_id:
        return f"/painel/clinica/agenda/evento/{evento_id}"
    return URL + (f"?lead={lead}" if lead else "")


@router.get(URL, response_class=HTMLResponse)
def lista(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    agora = datetime.now(timezone.utc)
    hoje = ca.hoje_br(agora)
    lead = _int(request.query_params.get("lead"))
    with get_pool().connection() as c:
        prods = cpr.produtos(c, conta[0], hoje)
        vencendo = cpr.perto_de_vencer(c, conta[0], hoje)
        reps = cpr.recompras(c, conta[0], hoje)
        cfg = cpr.config(c, conta[0])
        pac = cpr._paciente(c, conta[0], None, lead) if lead else None
        mes = cpr.vendido_no_periodo(c, conta[0], ca.utc(hoje.replace(day=1), time(0)), agora) if gerencia else 0
    return _render("clinica_produtos.html", request, titulo="Produtos da clínica", secao_ativa="agenda",
                   aviso=_AVISOS.get(request.query_params.get("aviso") or "", ""),
                   erro=request.session.pop("produtos_erro", ""), prods=prods, vencendo=vencendo, reps=reps,
                   cfg=cfg, pac=pac, gerencia=gerencia, hoje=hoje, mes=mes, pagamentos=cpr.PAGAMENTOS,
                   brl=cc.reais, produto_sel=_int(request.query_params.get("produto")))


@router.post(URL + "/vender")
def vender(request: Request, evento_id: str = Form(""), lead: str = Form(""), produto_id: str = Form(""),
           quantidade: str = Form("1"), pagamento: str = Form("")):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    eid, lid = _int(evento_id), _int(lead)
    r, erro = cpr.vender(get_pool(), conta[0], evento_id=eid, lead=None if eid else lid,
                         itens=[(produto_id, quantidade)], pagamento=pagamento,
                         membro_id=request.session.get("membro_id"))
    return _ir(request, _volta(eid, None if erro is None else lid), "" if erro else "vendido", erro or "")


@router.post(URL + "/entrada")
def entrada(request: Request, produto_id: str = Form(""), quantidade: str = Form(""), custo: str = Form(""),
            validade: str = Form("")):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    erro = cpr.entrada(get_pool(), conta[0], produto_id=_int(produto_id), quantidade=quantidade, custo=custo,
                       validade=validade, membro_id=request.session.get("membro_id"))
    return _ir(request, URL, "" if erro else "entrada", erro or "")


@router.post(URL + "/{produto_id}/duracao")
def duracao(request: Request, produto_id: int, dias: str = Form("")):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return _ir(request, URL, erro="Só o dono ou o gestor muda a duração do produto.")
    with get_pool().connection() as c:
        erro = cpr.salvar_recompra(c, conta[0], produto_id, dias)
        (c.rollback if erro else c.commit)()
    return _ir(request, URL, "" if erro else "duracao", erro or "")


@router.post(URL + "/recompra/{venda_id}/dispensar")
def dispensar(request: Request, venda_id: int):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        cpr.dispensar(c, conta[0], venda_id)
        c.commit()
    return _ir(request, URL, "dispensado")


@router.post(URL + "/config")
def salvar_config(request: Request, recompra: str = Form("ligado")):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return _ir(request, URL, erro="Só o dono ou o gestor muda a configuração.")
    with get_pool().connection() as c:
        erro = cpr.salvar_config(c, conta[0], recompra)
        (c.rollback if erro else c.commit)()
    return _ir(request, URL, "" if erro else "config", erro or "")


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.pr-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.pr-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.pr-topo h2{margin:0;font-size:1.5rem}.pr-topo .sub{color:var(--txt-mut);font-size:.86rem;margin-top:.2rem;max-width:66ch}
.pr-kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem;margin:1rem 0}
.pr-kpi div{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.55rem .75rem}
.pr-kpi b{display:block;font-size:1.3rem}.pr-kpi span{font-size:.72rem;color:var(--txt-mut)}
.pr-sec{margin:1.2rem 0 .4rem;font-size:1.02rem}
.pr-l{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.2rem .6rem;padding:.5rem .1rem;border-top:1px solid var(--borda);align-items:center}
.pr-m{font-size:.8rem;color:var(--txt-mut)}
.pr-chip{font-size:.7rem;padding:.08rem .45rem;border-radius:999px;border:1px solid var(--borda);color:var(--txt-mut);margin-left:.3rem}
.pr-chip.al{border-color:var(--ambar-borda);background:var(--ambar-fundo);color:#F0DCA6}
.pr-acoes{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center}.pr-acoes form{margin:0;display:flex;gap:.3rem;align-items:center}
.pr-acoes a,.pr-acoes button{width:auto;margin:0;min-height:36px;padding:.3rem .7rem;font-size:.82rem;border-radius:8px}
.pr-acoes .sec,.pr-acoes a{background:transparent;border:1px solid var(--borda);color:var(--txt);text-decoration:none;display:inline-flex;align-items:center}
.pr-acoes input{width:5.5rem;min-height:36px;margin:0}
.pr-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin-top:1rem}
.pr-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.6rem}
.pr-grid label{display:block;font-size:.78rem;color:var(--txt-mut);margin-bottom:.15rem}
</style>
<div class="pr-pag">
  <div class="pr-topo"><div><h2>Produtos da clínica</h2>
    <div class="sub">O produto é vendido no fim do atendimento (na tela do agendamento) e a reposição volta sozinha: na data prevista, o Zaq pergunta ao paciente se acabou — sem dizer o nome do produto. O que vence logo aparece como sugestão de venda antes de virar perda.</div></div>
    <div class="pr-acoes"><a href="/painel/clinica/agenda">‹ Agenda</a><a href="/painel/produtos">Cadastro e estoque</a></div></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}

  <div class="pr-kpi">
    <div><span>Reposição chegando</span><b>{{ reps|length }}</b><span>próximos 15 dias</span></div>
    <div><span>Vence em até 60 dias</span><b>{{ vencendo|length }}</b><span>lotes com saldo</span></div>
    <div><span>Produtos</span><b>{{ prods|length }}</b><span>{{ prods|selectattr('recompra_dias')|list|length }} com duração</span></div>
    {% if gerencia %}<div><span>Vendido no mês</span><b>{{ brl(mes) if mes else 'R$ 0' }}</b></div>{% endif %}
  </div>

  {% if pac %}
  <form class="pr-cx" method="post" action="/painel/clinica/produtos/vender">
    <b>Vender para {{ pac.paciente }}</b>
    <input type="hidden" name="lead" value="{{ pac.lead }}">
    <div class="pr-grid" style="margin-top:.5rem">
      <div><label>Produto</label><select name="produto_id">{% for p in prods %}<option value="{{ p.id }}" {% if p.id == produto_sel %}selected{% endif %}>{{ p.nome }} · {{ p.preco }} · {{ p.saldo_txt }} em estoque</option>{% endfor %}</select></div>
      <div><label>Quantidade</label><input name="quantidade" value="1" inputmode="decimal"></div>
      <div><label>Pagamento</label><select name="pagamento">{% for k, v in pagamentos.items() %}<option value="{{ k }}">{{ v }}</option>{% endfor %}</select></div>
    </div>
    <div class="pr-m" style="margin-top:.4rem">Assinante leva o desconto do plano sozinho.</div>
    <div class="pr-acoes" style="margin-top:.6rem"><button onclick="this.disabled=true;this.form.submit()">Vender</button><a href="/painel/clinica/produtos">cancelar</a></div>
  </form>
  {% endif %}

  <h3 class="pr-sec">Reposição chegando</h3>
  {% for r in reps %}<div class="pr-l"><div><b>{{ r.paciente }}</b><span class="pr-chip">{{ r.produto }}</span>
      {% if r.chegou %}<span class="pr-chip al">{{ 'lembrado' if r.estado == 'lembrado' else 'chegou' }} {{ r.recompra_em.strftime('%d/%m') }}</span>{% else %}<span class="pr-chip">{{ r.recompra_em.strftime('%d/%m') }}</span>{% endif %}
      <div class="pr-m">levou em {{ r.comprado_em.strftime('%d/%m/%Y') }}</div></div>
    <div class="pr-acoes">{% if r.lead %}<a href="/painel/clinica/produtos?lead={{ r.lead }}&produto={{ r.produto_id }}">Vender</a>{% endif %}
      <form method="post" action="/painel/clinica/produtos/recompra/{{ r.id }}/dispensar"><button class="sec">Tirar da lista</button></form></div></div>
  {% else %}<div class="pr-m">Nenhuma reposição chegando. Ela aparece quando o produto vendido tem duração cadastrada.</div>{% endfor %}

  <h3 class="pr-sec">Vence em até 60 dias</h3>
  {% for v in vencendo %}<div class="pr-l"><div><b>{{ v.nome }}</b><span class="pr-chip {% if v.vencido or v.dias <= 30 %}al{% endif %}">{% if v.vencido %}venceu {{ v.validade.strftime('%d/%m/%Y') }}{% else %}vence {{ v.validade.strftime('%d/%m/%Y') }}{% endif %}</span>
      <div class="pr-m">{{ v.quantidade }} no lote (estimado: o mais antigo sai primeiro)</div></div><div></div></div>
  {% else %}<div class="pr-m">Nada vencendo. A validade entra pela entrada de estoque aqui embaixo.</div>{% endfor %}

  <h3 class="pr-sec">Produtos e duração</h3>
  <div class="pr-m" style="margin-bottom:.3rem">A duração é quanto um produto dura pro paciente (ex.: protetor, 60 dias). Com ela, a reposição é lembrada sozinha.</div>
  {% for p in prods %}<div class="pr-l"><div><b>{{ p.nome }}</b>{% if p.vence_logo %}<span class="pr-chip al">validade {{ p.validade.strftime('%d/%m') }}</span>{% endif %}
      <div class="pr-m">{{ p.preco }} · {{ p.saldo_txt }} em estoque{% if p.recompra_dias %} · dura {{ p.recompra_dias }} dias{% endif %}</div></div>
    <div class="pr-acoes">{% if gerencia %}<form method="post" action="/painel/clinica/produtos/{{ p.id }}/duracao"><input name="dias" inputmode="numeric" value="{{ p.recompra_dias or '' }}" placeholder="dias" aria-label="Duração em dias"><button class="sec">Salvar</button></form>{% endif %}</div></div>
  {% else %}<div class="pr-m">Nenhum produto cadastrado. Cadastre em <a href="/painel/produtos">Cadastro e estoque</a>.</div>{% endfor %}

  {% if prods %}
  <form class="pr-cx" method="post" action="/painel/clinica/produtos/entrada">
    <b>Entrada de estoque com validade</b>
    <div class="pr-grid" style="margin-top:.5rem">
      <div><label>Produto</label><select name="produto_id">{% for p in prods %}<option value="{{ p.id }}">{{ p.nome }}</option>{% endfor %}</select></div>
      <div><label>Quantidade</label><input name="quantidade" inputmode="decimal" required></div>
      <div><label>Custo por unidade (R$; vazio: o custo médio)</label><input name="custo" inputmode="decimal" placeholder="45,90"></div>
      <div><label>Validade do lote</label><input type="date" name="validade"></div>
    </div>
    <div class="pr-acoes" style="margin-top:.6rem"><button>Registrar entrada</button></div>
  </form>
  {% endif %}

  {% if gerencia %}
  <form class="pr-cx" method="post" action="/painel/clinica/produtos/config">
    <b>Lembrete de reposição</b>
    <div class="pr-m" style="margin:.3rem 0 .5rem">Sai só no horário de atendimento, no máximo 1 mensagem automática por paciente por dia (somando vaga, plano e pacote), nunca para quem pediu pra sair, e nunca diz o nome do produto.</div>
    <div class="pr-acoes"><select name="recompra" style="width:auto"><option value="ligado" {% if cfg.recompra == 'ligado' %}selected{% endif %}>Ligado</option><option value="off" {% if cfg.recompra == 'off' %}selected{% endif %}>Desligado</option></select><button>Salvar</button></div>
  </form>
  {% endif %}
</div>
{% endblock %}"""

_env.loader.mapping["clinica_produtos.html"] = _TPL
