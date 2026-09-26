"""Plano de tratamento da clínica (finance/clinica_planos.py).

  /painel/clinica/planos              a lista e a configuração (desconto, formas, cobrança)
  /painel/clinica/planos/novo         montar a partir da consulta (?evento=) ou do card (?lead=)
  /painel/clinica/planos/{id}         o plano: editar, aprovar desconto, enviar, cancelar
  /plano/{token}                      a página que o PACIENTE abre (sem login)

A mesma porta da agenda da clínica (dono, gestor, vendedor). Desconto acima do teto
só sai com o dono ou o gestor.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import clinica_agenda as ca
from finance import clinica_config as cc
from finance import clinica_planos as cp
from web.painel_clinica_agenda import _acesso, _int
from web.portal import _env, _render

router = APIRouter()
_log = logging.getLogger("openclaw.painel_clinica_planos")
URL = "/painel/clinica/planos"

_AVISOS = {
    "salvo": "Plano salvo.",
    "esperando": "Plano salvo. O desconto passou do teto: ele sai depois que o dono ou o gestor aprovar.",
    "aprovado": "Desconto aprovado. Agora é só enviar.",
    "enviado": "Plano enviado no WhatsApp, com o link. O Zaq cobra a decisão em D+1 e D+3.",
    "cancelado": "Plano cancelado.",
    "aceito": "Anotado como aceito. As parcelas viraram contas a receber e o card foi para Fechado.",
    "config": "Configuração salva.",
}


def _ir(request: Request, url: str, aviso: str = "", erro: str = "") -> RedirectResponse:
    if erro:
        request.session["planos_erro"] = erro[:300]
    if aviso in _AVISOS:
        url += ("&" if "?" in url else "?") + f"aviso={aviso}"
    return RedirectResponse(url, status_code=303)


def _ctx(request: Request) -> dict:
    return {"aviso": _AVISOS.get(request.query_params.get("aviso") or "", ""),
            "erro": request.session.pop("planos_erro", ""), "secao_ativa": "agenda", "brl": cp._brl,
            "FORMAS": cp.FORMAS}


@router.get(URL, response_class=HTMLResponse)
def lista(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        planos = cp.listar(c, conta[0])
        cfg = cp.config(c, conta[0])
    return _render("clinica_planos.html", request, titulo="Planos de tratamento", **_ctx(request),
                   planos=planos, cfg=cfg, gerencia=gerencia)


@router.post(URL + "/config")
def salvar_config(request: Request, teto: str = Form(""), pix: str = Form(""), parcelas: str = Form(""),
                  validade: str = Form(""), cobranca: str = Form("ligado")):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return _ir(request, URL, erro="Só o dono ou o gestor muda o teto de desconto e as formas.")
    with get_pool().connection() as c:
        erro = cp.salvar_config(c, conta[0], teto=teto, pix=pix, parcelas=parcelas, validade=validade,
                                cobranca=cobranca)
        (c.rollback if erro else c.commit)()
    return _ir(request, URL, "" if erro else "config", erro or "")


def _tipos(c, conta_id: int) -> dict[int, dict]:
    return {t["id"]: t for t in cc.listar_tipos(c, conta_id)}


@router.get(URL + "/novo", response_class=HTMLResponse)
def novo(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = conta[0]
    q = request.query_params
    evento_id, lead = _int(q.get("evento")), _int(q.get("lead"))
    with get_pool().connection() as c:
        base = {"paciente": "", "fone": "", "lead": lead, "evento_id": None, "profissional_id": None}
        if evento_id:
            ja = cp.do_evento(c, conta_id, evento_id)
            if ja:
                return RedirectResponse(f"{URL}/{ja['id']}", status_code=303)
            ev = ca.evento(c, conta_id, evento_id)
            if ev:
                base = {"paciente": ev["paciente"], "fone": ev["fone"], "lead": ev["lead"],
                        "evento_id": ev["id"], "profissional_id": ev["profissional_id"]}
        elif lead:
            r = c.execute("""select coalesce(nullif(contato,''), empresa, ''), coalesce(nullif(whatsapp,''), telefone, '')
                               from prospeccao where id=%s and conta_id=%s""", (lead, conta_id)).fetchone()
            if r:
                base.update(paciente=r[0], fone=r[1])
        cfg = cp.config(c, conta_id)
        tipos = list(_tipos(c, conta_id).values())
        profs = ca._profs_que_atendem(c, conta_id)
    form = request.session.pop("plano_form", None) or {}
    return _render("clinica_plano_form.html", request, titulo="Plano de tratamento", **_ctx(request),
                   base=base, cfg=cfg, tipos=tipos, profs=profs, p=None, form=form, gerencia=gerencia,
                   linhas=range(cp.MAX_ITENS))


def _itens_do_form(form) -> list[dict]:
    out = []
    for i in range(cp.MAX_ITENS):
        sid = (form.get(f"tipo_{i}") or "").strip()
        out.append({"servico_id": int(sid) if sid.isdigit() else None, "nome": form.get(f"nome_{i}") or "",
                    "sessoes": form.get(f"sessoes_{i}") or "", "valor": form.get(f"valor_{i}") or ""})
    return [x for x in out if x["servico_id"] or x["nome"].strip()]


@router.post(URL + "/salvar")
async def salvar(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = conta[0]
    form = await request.form()
    plano_id = _int(form.get("plano_id"))
    volta = f"{URL}/{plano_id}" if plano_id else (
        f"{URL}/novo?evento={form.get('evento_id')}" if _int(form.get("evento_id")) else
        f"{URL}/novo?lead={form.get('lead')}" if _int(form.get("lead")) else f"{URL}/novo")
    with get_pool().connection() as c:
        itens, erro = cp.limpar_itens(_itens_do_form(form), _tipos(c, conta_id))
        if erro:
            request.session["plano_form"] = {k: str(v)[:200] for k, v in form.items()}
            return _ir(request, volta, erro=erro)
        pid, erro = cp.salvar(
            c, conta_id, plano_id=plano_id, lead=_int(form.get("lead")), evento_id=_int(form.get("evento_id")),
            profissional_id=_int(form.get("profissional_id")), paciente=form.get("paciente") or "",
            fone=form.get("fone") or "", itens=itens, desconto_pct=form.get("desconto") or "",
            pix_desconto_pct=form.get("pix") or "", cartao_parcelas=form.get("parcelas") or "",
            parcelado=bool(form.get("parcelado")), membro_id=request.session.get("membro_id"),
            pode_aprovar=gerencia)
        if erro:
            c.rollback()
            request.session["plano_form"] = {k: str(v)[:200] for k, v in form.items()}
            return _ir(request, volta, erro=erro)
        c.commit()
        p = cp.plano(c, conta_id, pid)
        if form.get("acao") == "enviar" and p["status"] == "rascunho":
            r = cp.enviar(c, conta_id, pid, request.session.get("membro_id"))
            return _ir(request, f"{URL}/{pid}", "enviado" if r["ok"] else "", r.get("erro") or "")
    return _ir(request, f"{URL}/{pid}", "esperando" if p["status"] == "aguardando_aprovacao" else "salvo")


@router.get(URL + "/{plano_id}", response_class=HTMLResponse)
def ver(request: Request, plano_id: int):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = conta[0]
    with get_pool().connection() as c:
        p = cp.plano(c, conta_id, plano_id)
        if not p:
            return RedirectResponse(URL, status_code=303)
        cfg = cp.config(c, conta_id)
        tipos = list(_tipos(c, conta_id).values())
        profs = ca._profs_que_atendem(c, conta_id)
        texto = cp.texto_envio(c, p) if p["token"] else ""
    form = request.session.pop("plano_form", None) or {}
    return _render("clinica_plano_form.html", request, titulo="Plano de tratamento", **_ctx(request),
                   base={"paciente": p["paciente"], "fone": p["fone"], "lead": p["lead"],
                         "evento_id": p["evento_id"], "profissional_id": p["profissional_id"]},
                   cfg=cfg, tipos=tipos, profs=profs, p=p, form=form, gerencia=gerencia,
                   linhas=range(cp.MAX_ITENS), texto=texto, link=cp.link(p) if p["token"] else "")


@router.post(URL + "/{plano_id}/{acao}")
def acao(request: Request, plano_id: int, acao: str, forma: str = Form("pix")):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id, membro = conta[0], request.session.get("membro_id")
    volta = f"{URL}/{plano_id}"
    with get_pool().connection() as c:
        if acao == "enviar":
            r = cp.enviar(c, conta_id, plano_id, membro)
            return _ir(request, volta, "enviado" if r["ok"] else "", r.get("erro") or "")
        if acao == "aprovar":
            if not gerencia:
                return _ir(request, volta, erro="Só o dono ou o gestor aprova desconto acima do teto.")
            ok = cp.aprovar_desconto(c, conta_id, plano_id, membro)
            c.commit()
            return _ir(request, volta, "aprovado" if ok else "")
        if acao == "cancelar":
            cp.cancelar(c, conta_id, plano_id)
            c.commit()
            return _ir(request, volta, "cancelado")
        if acao == "aceito":
            # a paciente fechou no balcão: a recepção registra (mesmo efeito do link)
            p = cp.plano(c, conta_id, plano_id)
    if acao == "aceito" and p and p["token"]:
        ok = cp.aceitar(get_pool(), p["token"], nome=p["paciente"], forma=forma, por="recepcao")
        return _ir(request, volta, "aceito" if ok else "", "" if ok else "Esse plano não está esperando decisão.")
    return _ir(request, volta)


# ------------------------------------------------------------------ a página do paciente

def _ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for", "")
    if xf:
        return xf.split(",")[0].strip()[:60]
    return (request.client.host if request.client else "")[:60]


@router.get("/plano/{token}", response_class=HTMLResponse)
def publico(request: Request, token: str):
    with get_pool().connection() as c:
        p = cp.por_token(c, token)
        if not p:
            return HTMLResponse(_env.get_template("plano_404.html").render(), status_code=404)
        cp.marcar_visto(c, token)
        empresa = c.execute("select coalesce(nome,'') from contas where id=%s", (p["conta_id"],)).fetchone()
        c.commit()
    q = request.query_params
    hoje = ca.hoje_br(datetime.now(timezone.utc))
    return HTMLResponse(_env.get_template("plano_publico.html").render(
        p=p, empresa=empresa[0] if empresa else "", brl=cp._brl, formas=cp.formas_txt(p), FORMA_D=cp.FORMA_D,
        vencido=bool(p["validade_ate"] and p["validade_ate"] < hoje), ok=(q.get("ok") or "").strip(),
        erro=(q.get("erro") or "").strip()[:0]))


@router.post("/plano/{token}/aceitar")
def aceitar(request: Request, token: str, nome: str = Form(""), forma: str = Form(""), concordo: str = Form("")):
    if not concordo or not nome.strip() or forma not in cp.FORMA_D:
        return RedirectResponse(f"/plano/{token}?ok=faltou", status_code=303)
    ok = cp.aceitar(get_pool(), token, nome=nome, forma=forma, ip=_ip(request), por="link")
    return RedirectResponse(f"/plano/{token}?ok={'aceito' if ok else 'nao'}", status_code=303)


@router.post("/plano/{token}/recusar")
def recusar(token: str):
    with get_pool().connection() as c:
        cp.recusar(c, token)
        c.commit()
    return RedirectResponse(f"/plano/{token}?ok=recusado", status_code=303)


_CSS = r"""<style>
.pl-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.pl-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.pl-topo h2{margin:0;font-size:1.5rem}.pl-topo .sub{color:var(--txt-mut);font-size:.86rem;margin-top:.2rem;max-width:64ch}
.pl-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin:.7rem 0}
.pl-cx h3{margin:0 0 .4rem;font-size:1.02rem}.pl-mut{font-size:.82rem;color:var(--txt-mut)}
.pl-linha{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:.5rem;padding:.45rem 0;border-top:1px solid var(--borda);align-items:center}
.pl-linha a{color:var(--txt);text-decoration:none;font-weight:600}
.pl-chip{font-size:.7rem;padding:.08rem .45rem;border-radius:999px;border:1px solid var(--borda);color:var(--txt-mut)}
.pl-chip.enviado{border-color:var(--ambar-borda);background:var(--ambar-fundo);color:#F0DCA6}
.pl-chip.aceito{border-color:var(--verde);color:var(--verde-claro)}
.pl-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.6rem}
.pl-grid label,.pl-item label{display:block;font-size:.78rem;color:var(--txt-mut);margin-bottom:.15rem}
.pl-item{display:grid;grid-template-columns:2fr 1.4fr .8fr 1fr;gap:.5rem;margin:.35rem 0}
@media (max-width:640px){.pl-item{grid-template-columns:1fr 1fr}}
.pl-acoes{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.7rem}.pl-acoes form{margin:0}
.pl-acoes button,.pl-acoes a{width:auto;margin:0;min-height:40px;padding:.4rem .85rem;font-size:.86rem;border-radius:8px}
.pl-acoes .sec,.pl-acoes a{background:transparent;border:1px solid var(--borda);color:var(--txt);text-decoration:none;display:inline-flex;align-items:center}
.pl-msg{white-space:pre-wrap;font-size:.86rem;background:var(--neon-fundo);border:1px solid var(--neon-borda);border-radius:9px;padding:.6rem .7rem}
.pl-tot{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem;margin-top:.5rem}
.pl-tot div{background:var(--card-2,rgba(255,255,255,.03));border:1px solid var(--borda);border-radius:9px;padding:.45rem .6rem}
.pl-tot b{display:block;font-size:1.05rem}
</style>"""

_TPL_LISTA = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="pl-pag">
  <div class="pl-topo"><div><h2>Planos de tratamento</h2>
    <div class="sub">Da consulta à proposta: os procedimentos que o médico indicou, o preço e como pagar. O Zaq cobra a decisão sozinho.</div></div>
    <div class="pl-acoes" style="margin-top:0"><a href="/painel/clinica/agenda">‹ Agenda</a><a href="/painel/clinica/planos/novo">+ Montar plano</a></div></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  <div class="pl-cx">
  {% for p in planos %}
    <div class="pl-linha"><div><a href="/painel/clinica/planos/{{ p.id }}">{{ p.paciente }}</a>
      <div class="pl-mut">{% for i in p.itens %}{{ i.nome }} · {{ i.sessoes }}{% if not loop.last %} + {% endif %}{% endfor %}{% if p.prof %} · {{ p.prof }}{% endif %}</div></div>
      <span>{{ brl(p.total) }}</span>
      <span class="pl-chip {{ p.status }}">{{ p.status_d }}{% if p.status == 'enviado' and p.validade_ate %} · vale até {{ p.validade_ate.strftime('%d/%m') }}{% endif %}{% if p.status == 'enviado' and p.visto_em %} · 👀 abriu{% endif %}{% if p.status == 'aceito' %} · {{ p.forma_d }}{% endif %}</span></div>
  {% else %}<div class="pl-mut">Nenhum plano ainda. Ele nasce da consulta: ao finalizar, "o médico propôs tratamento? sim" e depois "Montar plano de tratamento".</div>{% endfor %}
  </div>
  {% if gerencia %}
  <form class="pl-cx" method="post" action="/painel/clinica/planos/config">
    <h3>Desconto e formas de pagamento</h3>
    <div class="pl-mut" style="margin-bottom:.5rem">Sem teto, o desconto vira o preço: acima do teto, o plano só sai com o dono ou o gestor. A cobrança da decisão manda D+1 "conseguiu ver?" e D+3 o lembrete com as formas (sem dizer o procedimento), e avisa a recepção na véspera de vencer.</div>
    <div class="pl-grid">
      <div><label>Teto de desconto da recepção (%)</label><input name="teto" inputmode="decimal" value="{{ cfg.teto_desconto|round(2) }}"></div>
      <div><label>Desconto no Pix à vista (%)</label><input name="pix" inputmode="decimal" value="{{ cfg.pix_desconto|round(2) }}"></div>
      <div><label>Até quantas vezes no cartão</label><input name="parcelas" inputmode="numeric" value="{{ cfg.cartao_parcelas }}"></div>
      <div><label>Validade (dias)</label><input name="validade" inputmode="numeric" value="{{ cfg.validade_dias }}"></div>
      <div><label>Cobrar a decisão sozinho</label><select name="cobranca"><option value="ligado" {% if cfg.cobranca == 'ligado' %}selected{% endif %}>Ligado</option><option value="off" {% if cfg.cobranca == 'off' %}selected{% endif %}>Desligado</option></select></div>
    </div>
    <div class="pl-acoes"><button>Salvar</button></div>
  </form>
  {% endif %}
</div>
{% endblock %}"""

_TPL_FORM = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
{% set editavel = (not p) or p.status in ('rascunho','aguardando_aprovacao') %}
<div class="pl-pag">
  <div class="pl-topo"><div><h2>Plano de tratamento{% if p %} · {{ p.paciente }}{% endif %}</h2>
    <div class="sub">{% if p %}{{ p.status_d|capitalize }}{% if p.enviado_em %} · enviado {{ p.enviado_em.strftime('%d/%m %H:%M') }}{% endif %}{% if p.visto_em %} · o paciente abriu o link{% endif %}{% else %}O que o médico indicou, o preço e como pagar. A proposta não é prontuário: procedimento e valor, nunca diagnóstico.{% endif %}</div></div>
    <div class="pl-acoes" style="margin-top:0"><a href="/painel/clinica/planos">‹ Planos</a></div></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}

  {% if p %}
  <div class="pl-cx"><div class="pl-tot">
    <div><span class="pl-mut">Total</span><b>{{ brl(p.total) }}</b>{% if p.desconto %}<span class="pl-mut">desconto de {{ p.desconto_pct|round(2) }}%</span>{% endif %}</div>
    <div><span class="pl-mut">Pix à vista</span><b>{{ brl(p.pix) }}</b></div>
    <div><span class="pl-mut">Cartão</span><b>{{ p.parcelas }}× {{ brl(p.parcela) }}</b></div>
    {% if p.status == 'aceito' %}<div><span class="pl-mut">Aceito</span><b>{{ p.forma_d }}</b><span class="pl-mut">{{ p.aceito_em.strftime('%d/%m %H:%M') }} · {{ {'link':'pelo link','whatsapp':'no WhatsApp','recepcao':'na recepção'}[p.aceito_por] }}</span></div>{% endif %}
  </div>
  <div class="pl-acoes">
    {% if p.status == 'aguardando_aprovacao' and gerencia %}<form method="post" action="/painel/clinica/planos/{{ p.id }}/aprovar"><button>Aprovar desconto de {{ p.desconto_pct|round(2) }}%</button></form>{% endif %}
    {% if p.status == 'rascunho' %}<form method="post" action="/painel/clinica/planos/{{ p.id }}/enviar"><button>Enviar no WhatsApp</button></form>{% endif %}
    {% if p.status == 'enviado' %}<form method="post" action="/painel/clinica/planos/{{ p.id }}/aceito" style="display:flex;gap:.4rem;flex-wrap:wrap">
      <select name="forma" style="width:auto">{% for k, rot in FORMAS %}{% if k != 'parcelado' or p.parcelado %}<option value="{{ k }}">{{ rot }}</option>{% endif %}{% endfor %}</select>
      <button class="sec">Fechou na recepção</button></form>{% endif %}
    {% if p.status in ('rascunho','aguardando_aprovacao','enviado') %}<form method="post" action="/painel/clinica/planos/{{ p.id }}/cancelar"><button class="sec">Cancelar plano</button></form>{% endif %}
    {% if link %}<a href="{{ link }}" target="_blank" rel="noopener">Ver o que o paciente vê</a>{% endif %}
  </div>
  {% if texto %}<div class="pl-mut" style="margin-top:.7rem">A mensagem que saiu:</div><div class="pl-msg">{{ texto }}</div>{% endif %}
  </div>
  {% endif %}

  {% if editavel %}
  <form class="pl-cx" method="post" action="/painel/clinica/planos/salvar">
    <input type="hidden" name="plano_id" value="{{ p.id if p else '' }}">
    <input type="hidden" name="lead" value="{{ base.lead or '' }}">
    <input type="hidden" name="evento_id" value="{{ base.evento_id or '' }}">
    <div class="pl-grid">
      <div><label>Paciente</label><input name="paciente" required value="{{ form.paciente or base.paciente }}"></div>
      <div><label>Celular</label><input name="fone" inputmode="tel" value="{{ form.fone or base.fone }}"></div>
      <div><label>Quem indicou</label><select name="profissional_id"><option value="">—</option>{% for pr in profs %}<option value="{{ pr.id }}" {% if (form.profissional_id or base.profissional_id)|string == pr.id|string %}selected{% endif %}>{{ pr.nome }}</option>{% endfor %}</select></div>
    </div>
    <h3 style="margin-top:.9rem">Procedimentos</h3>
    <div class="pl-mut">O valor é por sessão; vazio, vale o preço do catálogo.</div>
    {% for i in linhas %}{% set it = p.itens[i] if (p and i < p.itens|length) else none %}
    <div class="pl-item">
      <div><label>Do catálogo</label><select name="tipo_{{ i }}"><option value="">—</option>{% for t in tipos %}<option value="{{ t.id }}" {% if (form['tipo_' ~ i] or (it.servico_id if it else ''))|string == t.id|string %}selected{% endif %}>{{ t.nome }}{% if t.preco_centavos %} · {{ t.preco }}{% endif %}</option>{% endfor %}</select></div>
      <div><label>Ou outro item (produto, protocolo)</label><input name="nome_{{ i }}" value="{{ form['nome_' ~ i] or (it.nome if it and not it.servico_id else '') }}"></div>
      <div><label>Sessões</label><input name="sessoes_{{ i }}" inputmode="numeric" value="{{ form['sessoes_' ~ i] or (it.sessoes if it else '') }}"></div>
      <div><label>Valor por sessão</label><input name="valor_{{ i }}" inputmode="decimal" placeholder="R$" value="{{ form['valor_' ~ i] or ((it.valor_unit_centavos / 100)|round(2) if it else '') }}"></div>
    </div>{% endfor %}
    <h3 style="margin-top:.9rem">Desconto e pagamento</h3>
    <div class="pl-grid">
      <div><label>Desconto no total (%) · seu teto: {{ cfg.teto_desconto|round(2) }}%</label><input name="desconto" inputmode="decimal" value="{{ form.desconto or (p.desconto_pct|round(2) if p else '0') }}"></div>
      <div><label>Desconto no Pix à vista (%)</label><input name="pix" inputmode="decimal" value="{{ form.pix or (p.pix_desconto_pct|round(2) if p else cfg.pix_desconto|round(2)) }}"></div>
      <div><label>Até quantas vezes no cartão</label><input name="parcelas" inputmode="numeric" value="{{ form.parcelas or (p.cartao_parcelas if p else cfg.cartao_parcelas) }}"></div>
      <div><label style="display:flex;gap:.4rem;align-items:center;margin-top:1.2rem"><input type="checkbox" name="parcelado" value="1" style="width:auto" {% if (p and p.parcelado) or (not p) %}checked{% endif %}> Oferecer entrada + parcelas no boleto/Pix</label></div>
    </div>
    <div class="pl-acoes"><button name="acao" value="salvar" class="sec">Salvar</button><button name="acao" value="enviar">Salvar e enviar no WhatsApp</button></div>
  </form>
  {% endif %}
</div>
<script>
document.querySelectorAll('.pl-acoes form, form.pl-cx').forEach(function(f){
  f.addEventListener('submit', function(){ f.querySelectorAll('button').forEach(function(b){ setTimeout(function(){ b.disabled = true; }, 0); }); });
});
</script>
{% endblock %}"""

_TPL_PUB = r"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Plano de tratamento · {{ empresa }}</title>
<style>
body{margin:0;background:#f4f2ee;font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#1d2433}
.pg{max-width:680px;margin:0 auto;padding:1.4rem 1rem 3rem}h1{font-size:1.3rem;margin:.2rem 0}.mut{color:#667085;font-size:.88rem}
.cx{background:#fff;border:1px solid #e4e0d8;border-radius:12px;padding:1rem 1.1rem;margin:.9rem 0}
table{width:100%;border-collapse:collapse;font-size:.92rem}td{padding:.45rem .3rem;border-bottom:1px solid #eee}td.v{text-align:right;white-space:nowrap}
.tot{font-size:1.15rem;font-weight:700}.ok{background:#e8f6ee;border:1px solid #a6d9b8;border-radius:10px;padding:.7rem .9rem}
.erro{background:#fdecec;border:1px solid #f1b5b5;border-radius:10px;padding:.7rem .9rem}
label{display:block;margin:.5rem 0 .2rem;font-size:.88rem}input[type=text]{width:100%;padding:.6rem;border:1px solid #d0d5dd;border-radius:8px;font-size:1rem;box-sizing:border-box}
.forma{display:flex;gap:.5rem;align-items:center;margin:.3rem 0}button{background:#1d6f42;color:#fff;border:0;border-radius:9px;padding:.7rem 1.1rem;font-size:1rem;margin-top:.8rem}
button.sec{background:#fff;color:#1d2433;border:1px solid #d0d5dd}
</style></head><body><div class="pg">
<div class="mut">{{ empresa }}</div>
<h1>Plano de tratamento</h1>
<div class="mut">{{ p.paciente }}{% if p.prof %} · indicado por {{ p.prof }}{% endif %}{% if p.validade_ate %} · vale até {{ p.validade_ate.strftime('%d/%m/%Y') }}{% endif %}</div>
{% if ok == 'aceito' or p.status == 'aceito' %}<div class="cx ok">Plano aceito{% if p.forma_d %} · {{ p.forma_d }}{% endif %}. A recepção vai te chamar pra combinar o pagamento e marcar a 1ª sessão. Obrigado!</div>
{% elif ok == 'recusado' or p.status == 'recusado' %}<div class="cx">Tudo bem. Se mudar de ideia, é só chamar a clínica pelo WhatsApp.</div>
{% elif ok == 'faltou' %}<div class="cx erro">Escreva seu nome, escolha a forma de pagamento e marque que leu o plano.</div>
{% elif ok == 'nao' %}<div class="cx erro">Não deu pra registrar: o plano pode ter vencido. Chame a clínica pelo WhatsApp.</div>{% endif %}
<div class="cx"><table>
{% for i in p.itens %}<tr><td>{{ i.nome }}<div class="mut">{{ i.sessoes }} {{ 'sessão' if i.sessoes == 1 else 'sessões' }} · {{ brl(i.valor_unit_centavos) }} cada</div></td><td class="v">{{ brl(i.valor_unit_centavos * i.sessoes) }}</td></tr>{% endfor %}
{% if p.desconto %}<tr><td>Desconto ({{ p.desconto_pct|round(2) }}%)</td><td class="v">−{{ brl(p.desconto) }}</td></tr>{% endif %}
<tr><td class="tot">Total</td><td class="v tot">{{ brl(p.total) }}</td></tr>
</table></div>
<div class="cx"><b>Como pagar</b>{% for f in formas %}<div>{{ f }}</div>{% endfor %}</div>
{% if p.status == 'enviado' and not vencido %}
<form class="cx" method="post" action="/plano/{{ p.token }}/aceitar">
  <b>Aceitar o plano</b>
  <label>Seu nome completo</label><input type="text" name="nome" required value="">
  <label>Como prefere pagar</label>
  <div class="forma"><input type="radio" name="forma" value="pix" id="f1" required><label for="f1" style="margin:0">Pix à vista · {{ brl(p.pix) }}</label></div>
  <div class="forma"><input type="radio" name="forma" value="cartao" id="f2"><label for="f2" style="margin:0">Cartão · {{ p.parcelas }}× {{ brl(p.parcela) }}</label></div>
  {% if p.parcelado and p.parcelas > 1 %}<div class="forma"><input type="radio" name="forma" value="parcelado" id="f3"><label for="f3" style="margin:0">Entrada {{ brl(p.primeira) }} + {{ p.parcelas - 1 }}× {{ brl(p.parcela) }} (boleto/Pix)</label></div>{% endif %}
  <div class="forma"><input type="checkbox" name="concordo" value="1" id="c1" required><label for="c1" style="margin:0">Li o plano e quero fazer</label></div>
  <button>Aceitar</button>
</form>
<form method="post" action="/plano/{{ p.token }}/recusar"><button class="sec">Não quero agora</button></form>
{% elif vencido and p.status == 'enviado' %}<div class="cx">Esse plano venceu. Chame a clínica pelo WhatsApp que a recepção monta de novo.</div>{% endif %}
<p class="mut">Este plano descreve os procedimentos, as sessões e o preço. Não é diagnóstico nem promessa de resultado.</p>
</div></body></html>"""

_TPL_404 = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Plano de tratamento</title></head>
<body style="font-family:system-ui,sans-serif;max-width:640px;margin:3rem auto;padding:0 1rem;color:#1d2433">
<h2>Plano não encontrado</h2><p>O link pode ter sido digitado errado. Peça um novo à clínica.</p></body></html>"""

# ".html" liga o autoescape: nome de paciente e de procedimento nunca viram HTML
_env.loader.mapping["clinica_planos.html"] = _TPL_LISTA
_env.loader.mapping["clinica_plano_form.html"] = _TPL_FORM
_env.loader.mapping["plano_publico.html"] = _TPL_PUB
_env.loader.mapping["plano_404.html"] = _TPL_404
