"""Os pacientes da clínica (finance/clinica_pacientes.py).

  /painel/clinica/pacientes                   a lista: filtros (novos contatos, com horário, em
                                              tratamento, retorno vencido, assinantes, sem vir),
                                              busca e cidade; + novo paciente
  /painel/clinica/pacientes/{id}              a ficha, em abas (?aba=)
  /painel/clinica/pacientes/{id}/cadastro     salvar o cadastro
  /painel/clinica/pacientes/novo              cadastrar na mão

A mesma porta da agenda da clínica (dono, gestor e recepção). Desenho aprovado:
docs/mockups/clinica_prontuario.html, seções 11.1 e 11.2.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import clinica_agenda as ca
from finance import clinica_config as cc
from finance import clinica_ficha_link as cfl
from finance import clinica_pacientes as cpa
from finance import clinica_preconsulta as cpc
from web.painel_clinica_agenda import _acesso
from web.portal import _env, _render

router = APIRouter()
URL = "/painel/clinica/pacientes"
ABAS = (("resumo", "Resumo"), ("agenda", "Agenda"), ("tratamento", "Plano e pacotes"),
        ("financeiro", "Financeiro"), ("produtos", "Produtos"), ("cadastro", "Cadastro"))
_AVISOS = {"salvo": "Cadastro salvo.", "criado": "Paciente cadastrado.",
           "link": "Link da ficha enviado no WhatsApp.", "link_novo": "Link novo gerado: o antigo não abre mais."}


def _ir(request: Request, url: str, aviso: str = "", erro: str = "") -> RedirectResponse:
    if erro:
        request.session["pacientes_erro"] = erro[:300]
    if aviso in _AVISOS:
        url += ("&" if "?" in url else "?") + f"aviso={aviso}"
    return RedirectResponse(url, status_code=303)


@router.get(URL, response_class=HTMLResponse)
def lista(request: Request):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    q = request.query_params
    agora = datetime.now(timezone.utc)
    # nome e telefone buscados moram na sessão: dado de paciente não vai na URL
    busca = request.session.get("pacientes_busca", "")
    with get_pool().connection() as c:
        d = cpa.listar(c, conta[0], agora, filtro=q.get("f") or "todos", busca=busca,
                       cidade=q.get("cidade") or "", etiqueta=q.get("etiqueta") or "")
    return _render("clinica_pacientes.html", request, titulo="Pacientes", secao_ativa="pacientes",
                   aviso=_AVISOS.get(q.get("aviso") or "", ""), erro=request.session.pop("pacientes_erro", ""),
                   d=d, filtros=cpa.FILTROS, f=q.get("f") or "todos", busca=busca,
                   cidade=q.get("cidade") or "", etiqueta=q.get("etiqueta") or "")


def _ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for", "")
    return (xf.split(",")[-1].strip() if xf else (request.client.host if request.client else ""))[:60]


@router.post(URL + "/buscar")
async def buscar(request: Request):
    form = dict(await request.form())
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    request.session["pacientes_busca"] = " ".join(str(form.get("q") or "").split())[:60]
    from urllib.parse import urlencode
    volta = {k: v for k, v in (("f", form.get("f") or ""), ("cidade", form.get("cidade") or ""),
                                ("etiqueta", form.get("etiqueta") or "")) if v}
    return RedirectResponse(URL + ("?" + urlencode(volta) if volta else ""), status_code=303)


@router.post(URL + "/novo")
async def novo(request: Request):
    from starlette.concurrency import run_in_threadpool
    form = dict(await request.form())
    return await run_in_threadpool(_novo, request, form)


def _novo(request: Request, form: dict):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    kid, erro = cpa.novo(get_pool(), conta[0], form)
    if erro:
        return _ir(request, URL, erro=erro)
    return _ir(request, f"{URL}/{kid}?aba=cadastro", "criado")


@router.get(URL + "/{cliente_id}", response_class=HTMLResponse)
def ver(request: Request, cliente_id: int):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    agora = datetime.now(timezone.utc)
    aba = request.query_params.get("aba") or "resumo"
    with get_pool().connection() as c:
        p = cpa.ficha(c, conta[0], cliente_id, agora)
        if not p:
            return RedirectResponse(URL, status_code=303)
        # quem pode ser o responsável: os pacientes do mesmo WhatsApp, e o atual sempre
        # (senão salvar apagaria calado o responsável que mora noutro número)
        opcoes = list(p["mesmo_card"])
        if p["responsavel"] and all(o["id"] != p["responsavel"]["id"] for o in opcoes):
            opcoes.append(p["responsavel"])
        # a pré-consulta é conteúdo clínico: só o profissional de saúde lê (seção 01)
        from finance import clinica_acesso_clinico as acc
        pode_ler = acc.leitor(c, conta[0], request.session) is not None
        abas = ABAS + ((("pre", "Pré-consulta"),) if pode_ler else ())
        aba = aba if aba in dict(abas) else "resumo"
        pre, pre_erro = None, False
        if pode_ler and aba == "pre" and cpc.resumo(c, conta[0], [cliente_id]):
            try:
                if acc.ler(c, conta[0], request.session, cliente_id, "pré-consulta", _ip(request)):
                    pre = cpc.ultima(c, conta[0], cliente_id)
            except acc.SemRegistro:
                pre_erro = True
        ve_registro = acc.pode_ver_registro(c, conta[0], request.session) != (False, None)
        ligado = cfl.ligado(c, conta[0])
        link = cfl.link(cfl.token(c, conta[0], cliente_id)) if ligado and p["falta"] else ""
        c.commit()
    return _render("clinica_paciente.html", request, titulo=p["nome"], secao_ativa="pacientes",
                   aviso=_AVISOS.get(request.query_params.get("aviso") or "", ""),
                   erro=request.session.pop("pacientes_erro", ""), p=p, aba=aba,
                   abas=abas, opcoes_resp=opcoes, brl=cc.reais, gerencia=gerencia, hoje=ca.hoje_br(agora),
                   pre=pre, pre_erro=pre_erro, ficha_ligado=ligado, link_ficha=link, SEXO=cpa.SEXO,
                   ve_registro=ve_registro)


@router.get(URL + "/{cliente_id}/termos/{aceite_id}.pdf")
def termo_pdf(request: Request, cliente_id: int, aceite_id: int):
    """A cópia em PDF do termo que o paciente aceitou (o texto exato, quem, quando)."""
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    from fastapi.responses import Response
    from finance import clinica_termos as ct
    with get_pool().connection() as c:
        doc = ct.pdf(c, conta[0], cliente_id, aceite_id)
    if not doc:
        return RedirectResponse(f"{URL}/{cliente_id}?aba=cadastro", status_code=303)
    return Response(doc, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="termo-{aceite_id}.pdf"',
                             "Cache-Control": "no-store, max-age=0"})


@router.post(URL + "/{cliente_id}/balcao", response_class=HTMLResponse)
def balcao(request: Request, cliente_id: int):
    """Check-in no balcão: um QR e um link que valem uma vez, por 15 minutos. O paciente
    abre no tablet da clínica (sem login no painel) ou no celular dele e preenche a ficha
    na frente da recepção — sem a data de nascimento, porque quem confere é ela."""
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        p = cpa.ficha(c, conta[0], cliente_id, agora)
        if not p:
            return RedirectResponse(URL, status_code=303)
        if not cfl.ligado(c, conta[0]):
            return _ir(request, f"{URL}/{cliente_id}", erro="O link da ficha está desligado (Agenda › Link da ficha).")
        par = cfl.gerar_balcao(c, conta[0], cliente_id, agora)
        c.commit()
    if not par:
        return RedirectResponse(URL, status_code=303)
    url = cfl.link_balcao(*par)
    from finance.pix import qr_svg
    return _render("clinica_paciente_balcao.html", request, titulo=p["nome"], secao_ativa="pacientes",
                   p=p, url=url, qr=qr_svg(url) or "", minutos=cfl.BALCAO_MIN)


@router.post(URL + "/{cliente_id}/link/novo")
def link_novo(request: Request, cliente_id: int):
    """Troca o link da ficha (foi pro número errado): o antigo para de abrir."""
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        if not cpa.ficha(c, conta[0], cliente_id, datetime.now(timezone.utc)):
            return RedirectResponse(URL, status_code=303)
        cfl.novo_token(c, conta[0], cliente_id)
        c.commit()
    return _ir(request, f"{URL}/{cliente_id}", "link_novo")


@router.post(URL + "/{cliente_id}/link")
def mandar_link(request: Request, cliente_id: int):
    """A recepção manda o link "complete sua ficha" pelo WhatsApp do paciente (ou do
    responsável: a mãe que marcou pro filho)."""
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    volta = f"{URL}/{cliente_id}"
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        p = cpa.ficha(c, conta[0], cliente_id, agora)
        if not p or not cfl.ligado(c, conta[0]):
            return _ir(request, volta, erro="O link da ficha está desligado (Agenda › Link da ficha).")
        texto = cfl.texto_manual(c, conta[0], cliente_id)
        fone = p["fone"]
        if p["responsavel"] and not ca._digitos(fone):
            r = cpa.ficha(c, conta[0], p["responsavel"]["id"], agora)
            fone = r["fone"] if r else ""
        res = ca.enviar(c, conta[0], {"id": None, "lead": p["lead"], "fone": fone}, texto or "", autor="humano",
                        membro_id=request.session.get("membro_id")) if texto else {"ok": False}
        c.commit()
    if not res.get("ok"):
        return _ir(request, volta, erro="Não deu pra mandar: confira o celular da ficha ou mande pela conversa.")
    return _ir(request, volta, "link")


@router.post(URL + "/{cliente_id}/cadastro")
async def cadastro(request: Request, cliente_id: int):
    from starlette.concurrency import run_in_threadpool
    form = dict(await request.form())
    return await run_in_threadpool(_cadastro, request, cliente_id, form)


def _cadastro(request: Request, cliente_id: int, form: dict):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    erro = cpa.salvar_cadastro(get_pool(), conta[0], cliente_id, form)
    return _ir(request, f"{URL}/{cliente_id}?aba=cadastro", "" if erro else "salvo", erro or "")


_CSS = r"""<style>
.pc-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.pc-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.pc-topo h2{margin:0;font-size:1.5rem}.pc-topo .sub{color:var(--txt-mut);font-size:.86rem;margin-top:.2rem}
.pc-chips{display:flex;gap:.35rem;flex-wrap:wrap;margin:.8rem 0 .4rem}
.pc-chip{font-size:.78rem;padding:.25rem .7rem;border-radius:999px;border:1px solid var(--borda);color:var(--txt-mut);text-decoration:none}
.pc-chip.on{border-color:var(--verde);color:var(--txt);background:rgba(28,122,79,.12)}
.pc-busca{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center;margin:.4rem 0}
.pc-busca input,.pc-busca select{width:auto;min-width:200px;margin:0}
.pc-t{width:100%;border-collapse:collapse;font-size:.88rem}.pc-t th,.pc-t td{padding:.45rem .35rem;border-bottom:1px solid var(--borda);text-align:left;vertical-align:top}
.pc-t th{font-size:.68rem;text-transform:uppercase;letter-spacing:.05em;color:var(--txt-mut)}
.pc-t a.nm{font-weight:600;color:var(--txt);text-decoration:none}.pc-m{font-size:.78rem;color:var(--txt-mut)}
.pc-tag{display:inline-block;font-size:.68rem;padding:.05rem .45rem;border-radius:999px;border:1px solid var(--borda);color:var(--txt-mut);margin:0 .2rem .15rem 0}
.pc-tag.g{border-color:#2C6E52;color:#7EE7B8}.pc-tag.y{border-color:var(--ambar-borda);color:#F0DCA6;background:var(--ambar-fundo)}
.pc-acoes{display:flex;gap:.35rem;flex-wrap:wrap}.pc-acoes a,.pc-acoes button{width:auto;margin:0;min-height:32px;padding:.2rem .6rem;font-size:.78rem;border-radius:8px;background:transparent;border:1px solid var(--borda);color:var(--txt);text-decoration:none;display:inline-flex;align-items:center}
.pc-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin-top:1rem}
.pc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.6rem}
.pc-grid label{display:block;font-size:.78rem;color:var(--txt-mut);margin-bottom:.15rem}
.pc-abas{display:flex;gap:.3rem;flex-wrap:wrap;margin:.9rem 0 .6rem;border-bottom:1px solid var(--borda)}
.pc-abas a{padding:.4rem .7rem;font-size:.84rem;color:var(--txt-mut);text-decoration:none;border-bottom:2px solid transparent}
.pc-abas a.on{color:var(--txt);border-bottom-color:var(--verde)}
.pc-l{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.2rem .6rem;padding:.45rem .1rem;border-top:1px solid var(--borda)}
.pc-2{display:grid;grid-template-columns:1fr 1fr;gap:.6rem}@media(max-width:640px){.pc-2{grid-template-columns:1fr}}
</style>"""

_TPL = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="pc-pag">
  <div class="pc-topo"><div><h2>Pacientes</h2><div class="sub">{{ d.total }} {{ 'na lista' if f != 'todos' or busca or cidade else 'na clínica' }}</div></div>
    <div class="pc-acoes"><a href="/painel/clinica/agenda">‹ Agenda</a></div></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  <div class="pc-chips">{% for k, r in filtros %}<a class="pc-chip {% if f == k %}on{% endif %}" href="/painel/clinica/pacientes?f={{ k }}{% if cidade %}&cidade={{ cidade|urlencode }}{% endif %}{% if etiqueta %}&etiqueta={{ etiqueta|urlencode }}{% endif %}">{{ r }} {{ d.contagem[k] }}</a>{% endfor %}</div>
  <form class="pc-busca" method="post" action="/painel/clinica/pacientes/buscar">
    <input type="hidden" name="f" value="{{ f }}">
    <input name="q" value="{{ busca }}" placeholder="🔍 Buscar por nome ou telefone" autocomplete="off">
    <select name="cidade" onchange="this.form.submit()"><option value="">Todas as cidades</option>{% for cd in d.cidades %}<option value="{{ cd }}" {% if cd == cidade %}selected{% endif %}>{{ cd }}</option>{% endfor %}</select>
    {% if d.etiquetas %}<select name="etiqueta" onchange="this.form.submit()"><option value="">Todas as etiquetas</option>{% for e in d.etiquetas %}<option value="{{ e }}" {% if e == etiqueta %}selected{% endif %}>{{ e }}</option>{% endfor %}</select>{% endif %}
    <button class="sec" style="width:auto">Buscar</button>
  </form>
  {% if busca %}<form method="post" action="/painel/clinica/pacientes/buscar" style="margin:0"><input type="hidden" name="q" value=""><input type="hidden" name="f" value="{{ f }}"><input type="hidden" name="cidade" value="{{ cidade }}"><input type="hidden" name="etiqueta" value="{{ etiqueta }}"><button class="sec" style="width:auto">Limpar a busca “{{ busca }}”</button></form>{% endif %}
  {% if d.pacientes %}
  <table class="pc-t"><tr><th>Paciente</th><th>WhatsApp</th><th>Cidade</th><th>Último atendimento</th><th>Próximo</th><th>Situação</th><th></th></tr>
  {% for p in d.pacientes %}<tr>
    <td>{% if p.id %}<a class="nm" href="/painel/clinica/pacientes/{{ p.id }}">{{ p.nome }}</a>{% else %}<b>{{ p.nome }}</b>{% endif %}
      <div class="pc-m">{% if p.idade is not none %}{{ p.idade }} anos{% elif p.id %}idade a completar{% else %}ainda sem ficha{% endif %}{% if p.responsavel %} · responsável: {{ p.responsavel }}{% endif %}{% if p.nome_social %} · nome social: {{ p.nome_social }}{% endif %}</div>
      {% for e in p.etiquetas %}<span class="pc-tag">{{ e }}</span>{% endfor %}</td>
    <td>{{ p.fone }}</td><td>{{ p.cidade }}</td>
    <td>{% if p.ultimo %}{{ p.ultimo.strftime('%d/%m/%Y') }}{% else %}—{% endif %}</td>
    <td>{% if p.proximo %}{{ p.proximo.strftime('%d/%m %H:%M') }}{% else %}—{% endif %}</td>
    <td>{% if p.tipo == 'contato' %}<span class="pc-tag">novo contato{% if p.ultima_msg %} · escreveu {{ p.ultima_msg.strftime('%d/%m') }}{% endif %}</span>{% endif %}
      {% if p.proximo %}<span class="pc-tag g">com horário</span>{% endif %}{% if p.em_tratamento %}<span class="pc-tag g">em tratamento</span>{% endif %}
      {% if p.retorno_vencido %}<span class="pc-tag y">retorno vencido</span>{% endif %}{% if p.assinante %}<span class="pc-tag g">assinante</span>{% endif %}
      {% if p.inativo %}<span class="pc-tag">sem vir há 6 meses</span>{% endif %}{% if p.tipo == 'paciente' and p.novo %}<span class="pc-tag">ainda não veio</span>{% endif %}
      {% if p.ficha and not p.ficha.completa %}<span class="pc-tag y">{{ p.ficha_txt }}</span>{% endif %}</td>
    <td><div class="pc-acoes"><a href="/painel/clinica/agenda/novo?{% if p.id %}cliente={{ p.id }}{% else %}lead={{ p.lead or '' }}{% endif %}" title="Agendar">📅</a>{% if p.id %}<a href="/painel/clinica/pacientes/{{ p.id }}" title="Ficha">Ficha</a>{% endif %}</div></td>
  </tr>{% endfor %}</table>
  {% if d.total > d.pacientes|length %}<div class="pc-m" style="margin-top:.5rem">Mostrando {{ d.pacientes|length }} de {{ d.total }}: use a busca ou um filtro.</div>{% endif %}
  {% else %}<div class="pc-m" style="margin-top:.8rem">Ninguém aqui ainda. Quem escreve no WhatsApp ou é agendado entra sozinho na lista.</div>{% endif %}

  <form class="pc-cx" method="post" action="/painel/clinica/pacientes/novo">
    <b>Novo paciente</b>
    <div class="pc-grid" style="margin-top:.5rem">
      <div><label>Nome completo</label><input name="nome" maxlength="120" required></div>
      <div><label>Celular (com DDD)</label><input name="fone" inputmode="tel" maxlength="40" required></div>
    </div>
    <div class="pc-acoes" style="margin-top:.6rem"><button>Cadastrar</button></div>
  </form>
</div>
{% endblock %}"""

_TPL_UM = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="pc-pag">
  <div class="pc-topo"><div><h2>{{ p.nome_social or p.nome }}</h2>{% if p.nome_social %}<div class="sub">nome civil: {{ p.nome }}</div>{% endif %}
    <div class="sub">{% if p.idade is not none %}{{ p.idade }} anos · {% endif %}{% if p.cidade %}{{ p.cidade }} · {% endif %}{% if p.desde %}paciente desde {{ p.desde.strftime('%m/%Y') }}{% endif %}{% if p.responsavel %} · responsável: <a href="/painel/clinica/pacientes/{{ p.responsavel.id }}">{{ p.responsavel.nome }}</a>{% endif %}</div>
    {% if p.etiquetas %}<div style="margin-top:.3rem">{% for e in p.etiquetas %}<a class="pc-tag" href="/painel/clinica/pacientes?etiqueta={{ e|urlencode }}">{{ e }}</a>{% endfor %}</div>{% endif %}</div>
    <div class="pc-acoes"><a href="/painel/clinica/pacientes">‹ Pacientes</a><a href="/painel/clinica/agenda/novo?cliente={{ p.id }}">Agendar</a>{% if ve_registro %}<a href="/painel/clinica/registro?paciente={{ p.id }}">Registro de acesso</a>{% endif %}{% if p.conversa_id %}<a href="/painel/prospeccao/comunicacao?abrir={{ p.conversa_id }}">WhatsApp</a>{% endif %}</div></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  {% if p.falta %}<div class="alerta" style="margin-top:.8rem">{{ p.ficha_txt|capitalize }}. <a href="/painel/clinica/pacientes/{{ p.id }}?aba=cadastro">Completar aqui</a>
    {% if link_ficha %}<div class="pc-acoes" style="margin-top:.5rem"><input readonly value="{{ link_ficha }}" onclick="this.select()" style="min-width:260px;margin:0">
      <form method="post" action="/painel/clinica/pacientes/{{ p.id }}/link" style="margin:0"><button class="sec" onclick="this.disabled=true;this.form.submit()">Mandar o link no WhatsApp</button></form>
      <form method="post" action="/painel/clinica/pacientes/{{ p.id }}/link/novo" style="margin:0" onsubmit="return confirm('Gerar outro link? O antigo para de abrir.')"><button class="sec">Gerar outro link</button></form>
      <form method="post" action="/painel/clinica/pacientes/{{ p.id }}/balcao" style="margin:0"><button class="sec">Preencher no balcão</button></form></div>
    <div class="pc-m" style="margin-top:.3rem">O paciente abre com a data de nascimento e preenche cadastro, pré-consulta e termos.</div>{% endif %}</div>
  {% elif p.situacao %}<div class="pc-m" style="margin-top:.6rem">✓ Ficha completa{% if p.situacao.pre_em %} · pré-consulta respondida em {{ p.situacao.pre_em.strftime('%d/%m') }}{% endif %}</div>{% endif %}
  {% if p.situacao and p.situacao.alergia %}<div style="margin-top:.5rem"><span class="pc-tag y">⚠ informou alergia na pré-consulta</span></div>{% endif %}
  <div class="pc-abas">{% for k, r in abas %}<a class="{% if aba == k %}on{% endif %}" href="/painel/clinica/pacientes/{{ p.id }}?aba={{ k }}">{{ r }}</a>{% endfor %}</div>

  {% if aba == 'resumo' %}
  <div class="pc-2">
    <div class="pc-cx" style="margin:0"><b>Próximo horário</b><div class="pc-m" style="margin-top:.3rem">{% if p.proximo %}{{ p.proximo.quando.strftime('%d/%m · %H:%M') }} · {{ p.proximo.tipo }}{% if p.proximo.prof %} · {{ p.proximo.prof }}{% endif %}{% else %}nenhum marcado{% endif %}</div>
      <b style="display:block;margin-top:.6rem">Tratamento</b><div class="pc-m" style="margin-top:.3rem">{% for k in p.pacotes if k.estado == 'ativo' %}{{ k.nome }}: {{ k.usadas }} de {{ k.total }} sessões<br>{% else %}nenhum pacote ativo{% endfor %}{% if p.assinatura %}<br>assinante {{ p.assinatura.plano }}{% endif %}</div></div>
    <div class="pc-cx" style="margin:0"><b>Financeiro</b><div class="pc-m" style="margin-top:.3rem">{% if p.atrasado %}<span class="pc-tag y">{{ brl(p.atrasado) }} em atraso</span>{% else %}nada atrasado{% endif %} · {{ p.titulos|length }} conta(s) a receber</div>
      <b style="display:block;margin-top:.6rem">Produtos</b><div class="pc-m" style="margin-top:.3rem">{% for x in p.produtos[:3] %}{{ x.nome }} ({{ x.quando.strftime('%d/%m') }}){% if x.recompra %} · reposição {{ x.recompra.strftime('%d/%m') }}{% endif %}<br>{% else %}nenhum{% endfor %}</div></div>
  </div>
  {% if p.mesmo_card %}<div class="pc-m" style="margin-top:.8rem">No mesmo WhatsApp: {% for o in p.mesmo_card %}<a href="/painel/clinica/pacientes/{{ o.id }}">{{ o.nome }}</a>{% if not loop.last %}, {% endif %}{% endfor %}</div>{% endif %}

  {% elif aba == 'agenda' %}
  {% for a in p.agenda %}<div class="pc-l"><div><b>{{ a.quando.strftime('%d/%m/%Y %H:%M') }}</b> · {{ a.tipo }}{% if a.prof %} · {{ a.prof }}{% endif %}</div><div class="pc-acoes"><span class="pc-tag">{{ a.situacao }}</span><a href="/painel/clinica/agenda/evento/{{ a.id }}">abrir</a></div></div>
  {% else %}<div class="pc-m">Nenhum atendimento ainda.</div>{% endfor %}

  {% elif aba == 'tratamento' %}
  <b>Planos de tratamento</b>
  {% for x in p.planos %}<div class="pc-l"><div>{{ x.criado.strftime('%d/%m/%Y') if x.criado }} · {{ x.status }} · {{ brl(x.total) }}</div><div class="pc-acoes"><a href="/painel/clinica/planos/{{ x.id }}">abrir</a></div></div>
  {% else %}<div class="pc-m">Nenhum plano.</div>{% endfor %}
  <b style="display:block;margin-top:.8rem">Pacotes</b>
  {% for k in p.pacotes %}<div class="pc-l"><div>{{ k.nome }} · {{ k.usadas }} de {{ k.total }} · {{ k.estado }}</div><div class="pc-acoes"><a href="/painel/clinica/pacotes/{{ k.id }}">abrir</a></div></div>
  {% else %}<div class="pc-m">Nenhum pacote.</div>{% endfor %}
  {% if p.assinatura %}<div class="pc-m" style="margin-top:.8rem">Assinante {{ p.assinatura.plano }} desde {{ p.assinatura.desde.strftime('%d/%m/%Y') }}.</div>{% endif %}

  {% elif aba == 'financeiro' %}
  {% for t in p.titulos %}<div class="pc-l"><div>{{ t.descricao }}<div class="pc-m">vence {{ t.vencimento.strftime('%d/%m/%Y') if t.vencimento }}</div></div><div><b>{{ brl(t.valor) }}</b> <span class="pc-tag {% if t.status == 'aberto' and t.vencimento and t.vencimento < hoje %}y{% endif %}">{{ t.status }}</span></div></div>
  {% else %}<div class="pc-m">Nenhuma conta a receber ligada a esta ficha.</div>{% endfor %}

  {% elif aba == 'produtos' %}
  {% for x in p.produtos %}<div class="pc-l"><div>{{ x.nome }}<div class="pc-m">levou em {{ x.quando.strftime('%d/%m/%Y') }}{% if x.recompra %} · reposição prevista {{ x.recompra.strftime('%d/%m/%Y') }}{% endif %}</div></div><div></div></div>
  {% else %}<div class="pc-m">Nenhum produto.</div>{% endfor %}

  {% elif aba == 'cadastro' %}
  <form class="pc-cx" method="post" action="/painel/clinica/pacientes/{{ p.id }}/cadastro" style="margin-top:0">
    <div class="pc-grid">
      <div><label>Nome completo</label><input name="nome" value="{{ p.nome }}" maxlength="120" required></div>
      <div><label>Data de nascimento</label><input type="date" name="nascimento" value="{{ p.nascimento.isoformat() if p.nascimento else '' }}"></div>
      <div><label>CPF</label><input name="cpf" value="{{ p.cpf }}" inputmode="numeric" maxlength="14"></div>
      <div><label>Celular</label><input name="fone" value="{{ p.fone }}" inputmode="tel" maxlength="40"></div>
      <div><label>E-mail</label><input name="email" type="email" value="{{ p.email }}"></div>
      <div><label>Cidade</label><input name="cidade" value="{{ p.cidade }}" maxlength="80"></div>
      <div><label>UF</label><input name="uf" value="{{ p.uf }}" maxlength="2"></div>
      <div><label>Nome social</label><input name="nome_social" value="{{ p.nome_social }}" maxlength="80"></div>
      <div><label>Sexo</label><select name="sexo"><option value="">—</option>{% for v, r in SEXO %}<option value="{{ v }}" {% if p.sexo == v %}selected{% endif %}>{{ r }}</option>{% endfor %}</select></div>
      <div><label>Profissão</label><input name="profissao" value="{{ p.profissao }}" maxlength="80"></div>
      <div><label>RG</label><input name="rg" value="{{ p.rg }}" maxlength="20"></div>
      <div><label>Nome da mãe</label><input name="nome_mae" value="{{ p.nome_mae }}" maxlength="120"></div>
      <div><label>Rua</label><input name="endereco" value="{{ p.endereco }}" maxlength="200"></div>
      <div><label>Número</label><input name="numero" value="{{ p.numero }}" maxlength="10"></div>
      <div><label>Complemento</label><input name="complemento" value="{{ p.complemento }}" maxlength="60"></div>
      <div><label>Bairro</label><input name="bairro" value="{{ p.bairro }}" maxlength="80"></div>
      <div><label>CEP</label><input name="cep" value="{{ p.cep }}" inputmode="numeric" maxlength="9"></div>
      <div><label>Contato de emergência</label><input name="contato_emergencia" value="{{ p.contato_emergencia }}" maxlength="120" placeholder="Nome"></div>
      <div><label>Telefone de emergência</label><input name="fone_emergencia" value="{{ p.fone_emergencia }}" inputmode="tel" maxlength="30"></div>
      <div><label>Como conheceu a clínica</label><input name="como_conheceu" value="{{ p.como_conheceu }}" maxlength="80" placeholder="Instagram, indicação, anúncio…"></div>
      <div><label>Etiquetas (separe por vírgula)</label><input name="etiquetas" value="{{ p.etiquetas|join(', ') }}" maxlength="300" placeholder="VIP, pós-operatório…"></div>
      <div><label>Responsável (menor de idade)</label><select name="responsavel_id"><option value="">nenhum</option>{% for o in opcoes_resp %}<option value="{{ o.id }}" {% if p.responsavel_id == o.id %}selected{% endif %}>{{ o.nome }}</option>{% endfor %}</select></div>
    </div>
    <div class="pc-m" style="margin-top:.5rem">O responsável é escolhido entre quem usa o mesmo WhatsApp. O CPF é o que vai na nota fiscal.</div>
    <div class="pc-acoes" style="margin-top:.6rem"><button>Salvar</button></div>
  </form>
  <div class="pc-cx"><b>Termos</b>
    {% for t in p.termos %}<div class="pc-m" style="margin-top:.3rem">{{ t.titulo }}: aceito por {{ t.por }}{% if t.papel == 'responsavel' %} (responsável){% endif %} em {{ t.quando.strftime('%d/%m/%Y %H:%M') }}{% if t.opcao_txt %}<br>{{ t.opcao_txt }}{% endif %} · <a href="/painel/clinica/pacientes/{{ p.id }}/termos/{{ t.id }}.pdf">PDF</a></div>
    {% else %}<div class="pc-m" style="margin-top:.3rem">Nenhum termo aceito ainda. O paciente aceita pelo link da ficha.</div>{% endfor %}</div>

  {% elif aba == 'pre' and pre %}
  <div class="pc-cx" style="margin-top:0"><b>Contado pelo {{ 'responsável' if pre.por == 'responsavel' else 'paciente' }} em {{ pre.quando.strftime('%d/%m/%Y') }}</b>{% if pre.curta %} · retorno{% endif %}
    {% for pergunta, resposta in pre.linhas %}<div style="margin-top:.5rem"><div class="pc-m">{{ pergunta }}</div><div>{{ resposta }}</div></div>{% endfor %}
    <div class="pc-m" style="margin-top:.7rem">Só os profissionais de saúde da clínica veem estas respostas. Confira na consulta.</div></div>
  {% elif aba == 'pre' and pre_erro %}<div class="erro">Não foi possível abrir a pré-consulta agora (a leitura precisa ficar no registro de acesso). Tente de novo em instantes.</div>
  {% elif aba == 'pre' %}<div class="pc-m">O paciente ainda não respondeu a pré-consulta.</div>
  {% endif %}
</div>
{% endblock %}"""

_TPL_BALCAO = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="pc-pag">
  <div class="pc-topo"><div><h2>Preencher no balcão</h2><div class="sub">{{ p.nome_social or p.nome }}</div></div>
    <div class="pc-acoes"><a href="/painel/clinica/pacientes/{{ p.id }}">‹ Ficha</a></div></div>
  <div class="pc-cx" style="text-align:center">
    {% if qr %}<div style="display:inline-block;background:#fff;padding:.6rem;border-radius:10px">{{ qr|safe }}</div>{% endif %}
    <div style="margin-top:.7rem">Aponte a câmera do tablet (ou do celular do paciente) para o QR, ou abra:</div>
    <div style="margin-top:.4rem"><input readonly value="{{ url }}" onclick="this.select()" style="max-width:100%;width:560px"></div>
    <div class="pc-m" style="margin-top:.6rem">Vale uma vez, por {{ minutos }} minutos. Não use um aparelho logado no painel: o paciente preenche cadastro, pré-consulta e termos, e ao terminar a ficha fecha sozinha. As respostas da pré-consulta vão só para o profissional.</div>
  </div>
</div>
{% endblock %}"""

_env.loader.mapping["clinica_pacientes.html"] = _TPL
_env.loader.mapping["clinica_paciente_balcao.html"] = _TPL_BALCAO
_env.loader.mapping["clinica_paciente.html"] = _TPL_UM
