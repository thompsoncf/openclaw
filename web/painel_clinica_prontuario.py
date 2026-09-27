"""O prontuário do paciente (fase 2): a ficha clínica e as evoluções.

/painel/clinica/prontuario/{paciente}                     a tela (seção 02 do desenho)
/painel/clinica/prontuario/{paciente}/ficha               salva a ficha clínica (versão nova)
/painel/clinica/prontuario/{paciente}/evolucao/nova       rascunho novo (do atendimento, se veio da agenda)
/painel/clinica/prontuario/{paciente}/evolucao/{id}       o rascunho (só o autor)
/painel/clinica/prontuario/{paciente}/evolucao/{id}/salvar   salva (o autosave também)
/painel/clinica/prontuario/{paciente}/evolucao/{id}/assinar  assina (vira imutável)
/painel/clinica/prontuario/{paciente}/evolucao/{id}/adendo   a correção, assinada

Só o profissional liberado (finance/clinica_acesso_clinico): todo o resto volta pra ficha
do paciente. Toda abertura e toda assinatura vão pro registro de acesso.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from db.conexao import get_pool
from finance import clinica_acesso_clinico as acc
from finance import clinica_agenda as ca
from finance import clinica_pacientes as cpa
from finance import clinica_preconsulta as cpc
from finance import clinica_prontuario as prt
from web.painel_clinica_agenda import _acesso, _int
from web.portal import _env, _render

router = APIRouter()
URL = "/painel/clinica/prontuario"


def _ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for", "")
    return (xf.split(",")[-1].strip() if xf else (request.client.host if request.client else ""))[:60]


def _volta_ficha(cliente_id: int) -> RedirectResponse:
    return RedirectResponse(f"/painel/clinica/pacientes/{cliente_id}", status_code=303)


def _ir(request: Request, cliente_id: int, aviso: str = "", erro: str = "", evo: int | None = None) -> RedirectResponse:
    if erro:
        request.session["prontuario_erro"] = erro
    if aviso:
        request.session["prontuario_aviso"] = aviso
    return RedirectResponse(f"{URL}/{cliente_id}" + (f"/evolucao/{evo}" if evo else ""), status_code=303)


def cpa_do_evento(c, conta_id: int, evento_id: int) -> int | None:
    r = c.execute("select cliente_id from eventos_agenda where id=%s and conta_id=%s", (evento_id, conta_id)).fetchone()
    return r[0] if r else None


def _pode(request: Request, c, cliente_id: int):
    """(conta, leitor) ou (None, resposta): só profissional liberado, e o paciente da conta."""
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return None, redir
    q = acc.leitor(c, conta[0], request.session)
    if not q:
        return None, _volta_ficha(cliente_id)
    if not c.execute("select 1 from clientes where id=%s and dono_id=%s", (cliente_id, conta[0])).fetchone():
        return None, RedirectResponse("/painel/clinica/pacientes", status_code=303)
    return (conta, q), None


@router.get(URL + "/{cliente_id}", response_class=HTMLResponse)
def prontuario(request: Request, cliente_id: int):
    agora = datetime.now(timezone.utc)
    evento = _int(request.query_params.get("evento"))
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        try:
            acc.ler(c, conta[0], request.session, cliente_id, "prontuário", _ip(request))
        except acc.SemRegistro:
            request.session["pacientes_erro"] = "Não foi possível abrir o prontuário agora (a abertura precisa ficar no registro)."
            return _volta_ficha(cliente_id)
        p = cpa.ficha(c, conta[0], cliente_id, agora)
        if not p:
            return RedirectResponse("/painel/clinica/pacientes", status_code=303)
        fc = prt.ficha_clinica(c, conta[0], cliente_id)
        pre = cpc.ultima(c, conta[0], cliente_id)
        evos = prt.historia(c, conta[0], cliente_id, q)
        ev = ca.evento(c, conta[0], evento) if evento else None
        if ev and cpa_do_evento(c, conta[0], evento) != cliente_id:
            ev = None                             # atendimento de outro paciente: nada
        c.commit()
    return _render("clinica_prontuario.html", request, titulo=f"Prontuário · {p['nome']}", secao_ativa="pacientes",
                   p=p, fc=fc, pre=pre, evos=evos, q=q, ev=ev, MODELOS=prt.MODELOS,
                   aviso=request.session.pop("prontuario_aviso", ""), erro=request.session.pop("prontuario_erro", ""))


@router.post(URL + "/{cliente_id}/ficha")
async def ficha(request: Request, cliente_id: int):
    form = dict(await request.form())
    return await run_in_threadpool(_ficha, request, cliente_id, form)


def _ficha(request: Request, cliente_id: int, form: dict):
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        if form.get("levar_alergia"):
            # "um clique leva pra ficha" (seção 12): a alergia que o paciente contou
            try:
                acc.ler(c, conta[0], request.session, cliente_id, "levou a alergia da pré-consulta", _ip(request))
            except acc.SemRegistro:
                return _ir(request, cliente_id, erro="Não foi possível agora. Tente de novo.")
            pre = cpc.ultima(c, conta[0], cliente_id)
            txt = next((v for k, v in (pre or {}).get("linhas", []) if "alergia" in k.lower()), "")
            atual = prt.ficha_clinica(c, conta[0], cliente_id)
            form = dict(atual)
            if txt.startswith("Sim"):
                al = txt[4:].lstrip(": ").strip() or "alergia informada na pré-consulta (sem dizer qual)"
                ja = {x.strip().lower() for x in atual["alergias"].replace(",", "\n").split("\n") if x.strip()}
                if al.lower() not in ja:
                    form["alergias"] = (atual["alergias"] + "\n" + al).strip()
            else:
                c.commit()
                return _ir(request, cliente_id, "A pré-consulta não informou alergia.")
        mudou = prt.salvar_ficha_clinica(c, conta[0], cliente_id, q, form)
        if mudou:
            acc.registrar(c, conta[0], q, cliente_id, "atualizou a ficha clínica", _ip(request))
        c.commit()
    return _ir(request, cliente_id, "Ficha clínica salva." if mudou else "Nada mudou na ficha clínica.")


@router.post(URL + "/{cliente_id}/evolucao/nova")
async def evolucao_nova(request: Request, cliente_id: int):
    form = dict(await request.form())
    return await run_in_threadpool(_evolucao_nova, request, cliente_id, form)


def _evolucao_nova(request: Request, cliente_id: int, form: dict):
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        eid = prt.nova(c, conta[0], cliente_id, q, str(form.get("modelo") or "livre"), _int(form.get("evento")))
        c.commit()
    if not eid:
        return _ir(request, cliente_id, erro="Modelo inválido.")
    return _ir(request, cliente_id, evo=eid)


@router.get(URL + "/{cliente_id}/evolucao/{evo_id}", response_class=HTMLResponse)
def evolucao(request: Request, cliente_id: int, evo_id: int):
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        e = prt.evolucao(c, conta[0], cliente_id, evo_id)
        if not e or e["profissional_id"] != q["profissional_id"] or e["status"] != "rascunho":
            return _ir(request, cliente_id)
        try:
            acc.ler(c, conta[0], request.session, cliente_id, "evolução (rascunho)", _ip(request))
        except acc.SemRegistro:
            return _ir(request, cliente_id, erro="Não foi possível abrir agora (a abertura precisa ficar no registro).")
        p = cpa.ficha(c, conta[0], cliente_id, datetime.now(timezone.utc))
        fc = prt.ficha_clinica(c, conta[0], cliente_id)
        ev = ca.evento(c, conta[0], e["evento_id"]) if e["evento_id"] else None
        c.commit()
    return _render("clinica_evolucao.html", request, titulo=f"Evolução · {p['nome']}", secao_ativa="pacientes",
                   p=p, e=e, fc=fc, ev=ev, MODELOS=prt.MODELOS, erro=request.session.pop("prontuario_erro", ""))


@router.post(URL + "/{cliente_id}/evolucao/{evo_id}/salvar")
async def salvar(request: Request, cliente_id: int, evo_id: int):
    form = dict(await request.form())
    return await run_in_threadpool(_salvar, request, cliente_id, evo_id, form)


def _salvar(request: Request, cliente_id: int, evo_id: int, form: dict):
    auto = form.get("auto") == "1"
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return JSONResponse({"ok": False}, status_code=403) if auto else resp
        conta, q = ok
        erro = prt.salvar(c, conta[0], cliente_id, evo_id, q, form)
        (c.rollback if erro else c.commit)()
    if auto:
        return JSONResponse({"ok": not erro, "erro": erro or "",
                             "salvo": ca.local(datetime.now(timezone.utc)).strftime("%H:%M:%S")})
    if form.get("trocar_modelo"):
        return _ir(request, cliente_id, evo=evo_id)
    return _ir(request, cliente_id, "Rascunho salvo." if not erro else "", erro or "", evo=None if not erro else evo_id)


@router.post(URL + "/{cliente_id}/evolucao/{evo_id}/assinar")
async def assinar(request: Request, cliente_id: int, evo_id: int):
    form = dict(await request.form())
    return await run_in_threadpool(_assinar, request, cliente_id, evo_id, form)


def _assinar(request: Request, cliente_id: int, evo_id: int, form: dict):
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        erro = prt.salvar(c, conta[0], cliente_id, evo_id, q, form) if form else None
        erro = erro or prt.assinar(c, conta[0], cliente_id, evo_id, q, agora)
        if erro:
            c.rollback()
            return _ir(request, cliente_id, erro=erro, evo=evo_id)
        acc.registrar(c, conta[0], q, cliente_id, "assinou uma evolução", _ip(request))
        prt.retorno_depois_de_finalizar(c, conta[0], cliente_id, evo_id)
        c.commit()
    return _ir(request, cliente_id, "Evolução assinada.")


@router.post(URL + "/{cliente_id}/evolucao/{evo_id}/descartar")
def descartar(request: Request, cliente_id: int, evo_id: int):
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        foi = prt.descartar(c, conta[0], cliente_id, evo_id, q)
        c.commit()
    return _ir(request, cliente_id, "Rascunho descartado." if foi else "")


@router.post(URL + "/{cliente_id}/evolucao/{evo_id}/adendo")
async def adendo(request: Request, cliente_id: int, evo_id: int):
    form = dict(await request.form())
    return await run_in_threadpool(_adendo, request, cliente_id, evo_id, form)


def _adendo(request: Request, cliente_id: int, evo_id: int, form: dict):
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        erro = prt.adendo(c, conta[0], cliente_id, evo_id, q, str(form.get("texto") or ""), agora)
        if erro:
            c.rollback()
            return _ir(request, cliente_id, erro=erro)
        acc.registrar(c, conta[0], q, cliente_id, "escreveu um adendo", _ip(request))
        c.commit()
    return _ir(request, cliente_id, "Adendo assinado.")


_CSS = r"""<style>
.pr-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.pr-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.pr-topo h2{margin:0;font-size:1.45rem}.pr-m{font-size:.84rem;color:var(--txt-mut)}
.pr-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin:.8rem 0}
.pr-alerta{background:rgba(224,87,79,.12);border:1px solid #E0574F;border-radius:9px;padding:.5rem .7rem;margin-top:.6rem;font-weight:600}
.pr-3{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.6rem}
.pr-3 div b{display:block;font-size:.8rem;color:var(--txt-mut);font-weight:600}
.pr-txt{white-space:pre-wrap}
.pr-evo{border-top:1px solid var(--borda);padding:.7rem 0}
.pr-evo h4{margin:0 0 .3rem;font-size:.95rem}
.pr-ad{margin:.5rem 0 0 1rem;padding-left:.7rem;border-left:3px solid var(--borda)}
.pr-tag{font-size:.72rem;padding:.08rem .45rem;border-radius:999px;border:1px solid var(--borda);color:var(--txt-mut)}
.pr-tag.g{border-color:var(--verde);color:var(--verde-claro)}.pr-tag.y{border-color:var(--ambar-borda);color:#F0DCA6}.pr-tag.r{border-color:#E0574F;color:#F4A9A4}
.pr-acoes{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.5rem}.pr-acoes form{margin:0}
.pr-acoes button,.pr-acoes a{width:auto;margin:0;min-height:38px;padding:.3rem .8rem;font-size:.86rem;border-radius:8px}
.pr-acoes .sec,.pr-acoes a{background:transparent;border:1px solid var(--borda);color:var(--txt);text-decoration:none;display:inline-flex;align-items:center}
.pr-cx textarea{min-height:5.5rem}
</style>"""

_TPL = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="pr-pag">
  <div class="pr-topo"><div><h2>{{ p.nome_social or p.nome }}</h2>
    <div class="pr-m">{% if p.idade is not none %}{{ p.idade }} anos · {% endif %}{% if p.desde %}paciente desde {{ p.desde.strftime('%m/%Y') }} · {% endif %}prontuário · só os profissionais de saúde da clínica leem, e cada abertura fica registrada</div></div>
    <div class="pr-acoes"><a href="/painel/clinica/pacientes/{{ p.id }}">‹ Ficha</a>{% if ev %}<a href="/painel/clinica/agenda/evento/{{ ev.id }}">Agendamento {{ ev.hora }}</a>{% endif %}</div></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  {% if fc.alergias %}<div class="pr-alerta">ALERGIA: {{ fc.alergias }}</div>{% endif %}

  <div class="pr-cx">
    <div class="pr-3">
      <div><b>Alergias</b><div class="pr-txt">{{ fc.alergias or 'nenhuma registrada' }}</div></div>
      <div><b>Medicamentos em uso</b><div class="pr-txt">{{ fc.medicamentos or '—' }}</div></div>
      <div><b>Problemas e antecedentes</b><div class="pr-txt">{{ fc.problemas or '—' }}</div></div>
    </div>
    {% if fc.quando %}<div class="pr-m" style="margin-top:.4rem">Atualizada por {{ fc.por }} em {{ fc.quando.strftime('%d/%m/%Y %H:%M') }}</div>{% endif %}
    <details style="margin-top:.5rem"><summary>Editar a ficha clínica</summary>
      <form method="post" action="/painel/clinica/prontuario/{{ p.id }}/ficha" style="margin-top:.5rem">
        <label>Alergias<textarea name="alergias">{{ fc.alergias }}</textarea></label>
        <label>Medicamentos em uso<textarea name="medicamentos">{{ fc.medicamentos }}</textarea></label>
        <label>Problemas e antecedentes<textarea name="problemas">{{ fc.problemas }}</textarea></label>
        <div class="pr-acoes"><button>Salvar a ficha clínica</button></div>
        <div class="pr-m">Cada salvamento é uma versão nova (quem e quando); nada se apaga.</div>
      </form></details>
  </div>

  {% if pre %}<div class="pr-cx"><b>Pré-consulta</b> <span class="pr-m">contado pelo {{ 'responsável' if pre.por == 'responsavel' else 'paciente' }} em {{ pre.quando.strftime('%d/%m/%Y') }}{% if pre.curta %} · retorno{% endif %} — confira na consulta</span>
    {% for pergunta, resposta in pre.linhas %}<div style="margin-top:.35rem"><span class="pr-m">{{ pergunta }}</span> {{ resposta }}</div>{% endfor %}
    <form method="post" action="/painel/clinica/prontuario/{{ p.id }}/ficha" class="pr-acoes"><input type="hidden" name="levar_alergia" value="1"><button class="sec">Levar a alergia contada para a ficha clínica</button></form>
  </div>{% endif %}

  <div class="pr-cx"><b>Nova evolução</b>{% if ev %} <span class="pr-m">· do atendimento de {{ ev.hora }} ({{ ev.tipo }})</span>{% endif %}
    <form method="post" action="/painel/clinica/prontuario/{{ p.id }}/evolucao/nova" class="pr-acoes">
      {% if ev %}<input type="hidden" name="evento" value="{{ ev.id }}">{% endif %}
      <select name="modelo" style="width:auto;margin:0">{% for k, m in MODELOS.items() if k != 'adendo' %}<option value="{{ k }}">{{ m[0] }}</option>{% endfor %}</select>
      <button>Começar</button></form></div>

  <div class="pr-cx"><b>Atendimentos</b>
  {% for e in evos %}<div class="pr-evo">
    <h4>{{ e.quando.strftime('%d/%m/%Y') }}{% if e.tipo %} · {{ e.tipo }}{% endif %} · {{ e.prof }} <span class="pr-m">· {{ e.modelo_txt }}</span>
      {% if e.status == 'rascunho' %}<span class="pr-tag y">rascunho (só você vê)</span>{% elif e.integra %}<span class="pr-tag g">assinado · íntegro</span>{% else %}<span class="pr-tag r">assinado · ALTERADO NO BANCO</span>{% endif %}</h4>
    {% for rot, txt in e.linhas %}<div style="margin-top:.2rem"><span class="pr-m">{{ rot }}:</span> <span class="pr-txt">{{ txt }}</span></div>{% endfor %}
    {% if e.cid %}<div class="pr-m">CID: {{ e.cid }}</div>{% endif %}{% if e.retorno_dias %}<div class="pr-m">Retorno em {{ e.retorno_dias }} dias</div>{% endif %}
    {% if e.status == 'assinado' %}<div class="pr-m">Assinado por {{ e.prof }} ({{ e.conselho }}) em {{ e.assinado_em.strftime('%d/%m/%Y %H:%M') }} · impressão {{ e.hash[:12] }}</div>{% endif %}
    {% for a in e.adendos %}<div class="pr-ad"><b>Adendo</b> de {{ a.prof }} em {{ a.assinado_em.strftime('%d/%m/%Y %H:%M') if a.assinado_em else '' }} {% if a.integra %}<span class="pr-tag g">íntegro</span>{% else %}<span class="pr-tag r">ALTERADO</span>{% endif %}
      <div class="pr-txt">{{ a.campos.texto }}</div></div>{% endfor %}
    <div class="pr-acoes">{% if e.meu_rascunho %}<a href="/painel/clinica/prontuario/{{ p.id }}/evolucao/{{ e.id }}">Continuar o rascunho</a>{% endif %}
      {% if e.status == 'assinado' %}<details><summary class="pr-m">Escrever adendo (a correção)</summary>
        <form method="post" action="/painel/clinica/prontuario/{{ p.id }}/evolucao/{{ e.id }}/adendo"><textarea name="texto" required></textarea><div class="pr-acoes"><button class="sec">Assinar o adendo</button></div></form></details>{% endif %}</div>
  </div>{% else %}<div class="pr-m" style="margin-top:.4rem">Nenhuma evolução ainda.</div>{% endfor %}
  </div>
</div>
{% endblock %}"""

_TPL_EVO = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="pr-pag">
  <div class="pr-topo"><div><h2>Evolução · {{ p.nome_social or p.nome }}</h2>
    <div class="pr-m">{% if ev %}{{ ev.tipo }} de {{ ev.hora }} · {% endif %}{{ MODELOS[e.modelo][0] }} · <span id="salvo">rascunho{% if e.atualizado_em %} salvo às {{ e.atualizado_em.strftime('%H:%M:%S') }}{% endif %}</span></div></div>
    <div class="pr-acoes"><a href="/painel/clinica/prontuario/{{ p.id }}">‹ Prontuário</a></div></div>
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  {% if fc.alergias %}<div class="pr-alerta">ALERGIA: {{ fc.alergias }}</div>{% endif %}
  <form class="pr-cx" id="evo" method="post" action="/painel/clinica/prontuario/{{ p.id }}/evolucao/{{ e.id }}/salvar">
    {% if not e.adendo_de %}<label>Modelo<select name="modelo" onchange="if(confirm('Trocar o modelo? O que já está escrito vai para o primeiro campo do novo.')){document.getElementById('tm').value='1';this.form.submit();}else{this.value='{{ e.modelo }}';}">{% for k, m in MODELOS.items() if k != 'adendo' %}<option value="{{ k }}" {% if k == e.modelo %}selected{% endif %}>{{ m[0] }}</option>{% endfor %}</select></label>{% endif %}
    <input type="hidden" name="trocar_modelo" id="tm" value="">
    {% for k, rot in MODELOS[e.modelo][1] %}<label>{{ rot }}<textarea name="{{ k }}">{{ e.campos.get(k, '') }}</textarea></label>{% endfor %}
    {% if e.modelo == 'dermatologia' %}<label>CID-10 (opcional)<input name="cid" maxlength="20" value="{{ e.cid }}" placeholder="L70.0"></label>{% endif %}
    <label>Retorno em quantos dias (opcional: vira o retorno da agenda)<input name="retorno_dias" inputmode="numeric" value="{{ e.retorno_dias or '' }}"></label>
    <div class="pr-acoes"><button class="sec">Salvar rascunho</button>
      <button formaction="/painel/clinica/prontuario/{{ p.id }}/evolucao/{{ e.id }}/assinar" onclick="return confirm('Assinar? Depois de assinada, a evolução não muda: a correção é um adendo.')">Assinar e finalizar</button>
      <button class="sec" formaction="/painel/clinica/prontuario/{{ p.id }}/evolucao/{{ e.id }}/descartar" formnovalidate onclick="return confirm('Descartar este rascunho? Ele ainda não é prontuário e some.')">Descartar rascunho</button></div>
    <div class="pr-m">O rascunho se salva sozinho e só você vê. Assinada, fica com a hora do servidor, seu conselho e a impressão digital do texto.</div>
  </form>
</div>
<script>
(function(){var f=document.getElementById('evo'),mudou=false,s=document.getElementById('salvo');
f.addEventListener('input',function(){mudou=true;});
setInterval(function(){if(!mudou)return;mudou=false;var d=new FormData(f);d.append('auto','1');d.delete('trocar_modelo');
fetch(f.action,{method:'POST',body:d,credentials:'same-origin'}).then(function(r){return r.json();}).then(function(j){if(j.ok)s.textContent='rascunho salvo às '+j.salvo;else s.textContent='não salvou: '+(j.erro||'tente de novo');}).catch(function(){s.textContent='sem conexão: o rascunho não salvou';mudou=true;});},15000);})();
</script>
{% endblock %}"""

_env.loader.mapping["clinica_prontuario.html"] = _TPL
_env.loader.mapping["clinica_evolucao.html"] = _TPL_EVO
