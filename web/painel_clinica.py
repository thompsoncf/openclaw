"""Configurar › Clínica: /painel/clinica/configurar — o cadastro que a agenda usa.

Fase 1 do plano aprovado (docs/mockups/clinica_visao_geral.html, seção 10), com
as abas do Configurar do protótipo: Profissionais, Atendimentos, Locais e Grade.
A lógica mora em finance/clinica_config.py; aqui é só a tela.

SÓ A CLÍNICA (CLAUDE.md §6) e só quem manda na conta: dono e gestor. A recepção
usa a agenda (fase 2); quem desenha a semana do médico é o dono.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import clinica_config as cc
from finance import raio_x_perfil as rxp
from web.portal import _env, _render, conta_logada, nicho_da_conta

router = APIRouter()
_log = logging.getLogger("openclaw.painel_clinica")

_ABAS = (("prof", "Profissionais"), ("tipos", "Atendimentos"), ("locais", "Locais"),
         ("grade", "Grade"))
_AVISOS = {
    "salvo": "Salvo.",
    "removido": "Removido.",
}


def _acesso(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if request.session.get("papel", "dono") not in ("dono", "gestor"):
        return None, RedirectResponse("/painel", status_code=303)
    if rxp.perfil_por_nicho(nicho_da_conta(conta)) != "clinica":
        return None, RedirectResponse("/painel", status_code=303)
    return conta, None


def _ir(request: Request, aba: str, aviso: str = "", erro: str = "") -> RedirectResponse:
    """O erro (texto livre) vai pela sessão, nunca pela URL: texto da URL impresso
    na tela é o link que alguém manda com um script dentro."""
    if erro:
        request.session["clinica_erro"] = erro[:300]
    aba = aba if aba in dict(_ABAS) else "prof"
    q = f"?aba={aba}" + (f"&aviso={aviso}" if aviso in _AVISOS else "")
    return RedirectResponse("/painel/clinica/configurar" + q, status_code=303)


def _int(txt) -> int | None:
    try:
        return int(str(txt).strip()) if str(txt or "").strip() else None
    except ValueError:
        return None


def _data(txt) -> date | None:
    try:
        return datetime.strptime((txt or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


_SEMANA = {1: "seg", 2: "ter", 3: "qua", 4: "qui", 5: "sex", 6: "sáb", 7: "dom"}


def _quando(utc: datetime) -> str:
    """UTC → "qui 01/10 09:00" em Brasília, sem depender do locale do servidor."""
    loc = utc + timedelta(hours=-3)
    return f"{_SEMANA[loc.isoweekday()]} {loc:%d/%m %H:%M}"


def _hoje_br() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=-3)).date()


@router.get("/painel/clinica/configurar", response_class=HTMLResponse)
def configurar(request: Request):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = conta[0]
    aba = request.query_params.get("aba") or "prof"
    aba = aba if aba in dict(_ABAS) else "prof"
    hoje = _hoje_br()
    with get_pool().connection() as c:
        profs = cc.listar_profissionais(c, conta_id)
        tipos = cc.listar_tipos(c, conta_id)
        locais = cc.listar_locais(c, conta_id)
        grade = cc.listar_grade(c, conta_id)
        bloqueios = cc.listar_bloqueios(c, conta_id, hoje)
        membros = cc.membros_da_conta(c, conta_id)
        resumo = cc.resumo(c, conta_id)
        todos_profs = cc.listar_profissionais(c, conta_id, so_ativos=False)
        # a prova de que a grade funciona: os próximos horários livres de cada um,
        # no primeiro atendimento que ele faz
        amostra = {}
        for p in profs:
            if p["tipos"]:
                t = next((x for x in tipos if x["id"] in p["tipos"]), None)
                if t:
                    livres = cc.livres(c, conta_id, p["id"], t["id"], hoje, dias=14, limite=6)
                    amostra[p["id"]] = {"tipo": t["nome"], "livres": [_quando(x["inicio"])
                                                                     for x in livres]}
    nome_tipo = {t["id"]: t["nome"] for t in tipos}
    nome_local = {x["id"]: x["nome"] for x in locais}
    nome_prof = {p["id"]: p["nome"] + ("" if p["ativo"] else " (fora da agenda)")
                 for p in todos_profs}
    grade_de = {}
    for g in grade:
        grade_de.setdefault(g["profissional_id"], []).append(dict(
            g, dias_txt=cc.dias_txt(cc.dias_de(g["dias"])), local=nome_local.get(g["local_id"], "?"),
            repete_txt=(dict(cc.REPETE)[g["repete"]] if g["repete"] != "mensal"
                        else f"{cc.ORDINAL.get(g['semana_do_mes'], '')} do mês")))
    q = request.query_params
    return _render("clinica_configurar.html", request, titulo="Configurar clínica",
                   secao_ativa="clinica", aba=aba, abas=_ABAS, profs=profs, tipos=tipos,
                   locais=locais, grade_de=grade_de, bloqueios=bloqueios, membros=membros,
                   resumo=resumo, amostra=amostra, nome_tipo=nome_tipo, nome_prof=nome_prof,
                   CAT=cc.CATEGORIAS, CAT_D=dict(cc.CATEGORIAS), FUNCOES=cc.FUNCOES,
                   CORES=cc.CORES, ACESSOS=cc.ACESSOS, ACESSO_D=dict(cc.ACESSOS),
                   REPETE=cc.REPETE, DIAS=cc.DIAS, hoje=hoje.isoformat(),
                   aviso=_AVISOS.get(q.get("aviso") or "", ""),
                   erro=request.session.pop("clinica_erro", ""))


def _salvar(request: Request, aba: str, fn, *args, **kw):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        erro = fn(c, conta[0], *args, **kw)
        if erro:
            c.rollback()
            return _ir(request, aba, erro=erro)
        c.commit()
    return _ir(request, aba, "salvo")


@router.post("/painel/clinica/profissional")
def salvar_profissional(request: Request, id: str = Form(""), nome: str = Form(""),
                        funcao: str = Form(""), especialidade: str = Form(""),
                        conselho: str = Form(""), cor: str = Form(""),
                        acesso: str = Form("sem_login"), membro_id: str = Form(""),
                        aviso_agenda: str = Form(""), tipos: list[str] = Form([])):
    return _salvar(request, "prof", cc.salvar_profissional, id=_int(id), nome=nome,
                   funcao=funcao, especialidade=especialidade, conselho=conselho, cor=cor,
                   acesso=acesso, membro_id=_int(membro_id), aviso_agenda=bool(aviso_agenda),
                   tipos=[t for t in (_int(x) for x in tipos) if t])


@router.post("/painel/clinica/tipo")
def salvar_tipo(request: Request, id: str = Form(""), nome: str = Form(""),
                duracao_min: str = Form("30"), categoria: str = Form("consulta"),
                cor: str = Form(""), preco: str = Form(""), volta_dias: str = Form(""),
                volta_motivo: str = Form(""), agente_diz_preco: str = Form(""),
                agente_marca: str = Form("")):
    return _salvar(request, "tipos", cc.salvar_tipo, id=_int(id), nome=nome,
                   duracao_min=_int(duracao_min) or 0, categoria=categoria, cor=cor,
                   preco_centavos=cc.centavos(preco), volta_dias=_int(volta_dias),
                   volta_motivo=volta_motivo, agente_diz_preco=bool(agente_diz_preco),
                   agente_marca=bool(agente_marca))


@router.post("/painel/clinica/local")
def salvar_local(request: Request, id: str = Form(""), nome: str = Form(""),
                 endereco: str = Form(""), cidade: str = Form(""), tipo: str = Form("sede")):
    return _salvar(request, "locais", cc.salvar_local, id=_int(id), nome=nome,
                   endereco=endereco, cidade=cidade, tipo=tipo)


@router.post("/painel/clinica/grade")
def salvar_grade(request: Request, profissional_id: str = Form(""), local_id: str = Form(""),
                 dias: list[str] = Form([]), inicio: str = Form(""), fim: str = Form(""),
                 repete: str = Form("semanal"), semana_do_mes: str = Form(""),
                 referencia: str = Form(""), encaixes: str = Form("0")):
    return _salvar(request, "grade", cc.salvar_grade, profissional_id=_int(profissional_id) or 0,
                   local_id=_int(local_id) or 0, dias=[d for d in (_int(x) for x in dias) if d],
                   inicio=inicio, fim=fim, repete=repete, semana_do_mes=_int(semana_do_mes),
                   referencia=_data(referencia), encaixes=_int(encaixes) or 0)


@router.post("/painel/clinica/bloqueio")
def salvar_bloqueio(request: Request, profissional_id: str = Form(""), de: str = Form(""),
                    ate: str = Form(""), inicio: str = Form(""), fim: str = Form(""),
                    motivo: str = Form("")):
    return _salvar(request, "grade", cc.salvar_bloqueio, profissional_id=_int(profissional_id),
                   de=_data(de), ate=_data(ate), inicio=inicio, fim=fim, motivo=motivo)


_REMOVER = {
    "profissional": ("prof", lambda c, conta, i: cc.desativar(c, conta, "clinica_profissionais", i)),
    "local": ("locais", lambda c, conta, i: cc.desativar(c, conta, "clinica_locais", i)),
    "grade": ("grade", lambda c, conta, i: cc.desativar(c, conta, "clinica_grade", i)),
    "tipo": ("tipos", cc.desativar_tipo),
    "bloqueio": ("grade", cc.remover_bloqueio),
}


@router.post("/painel/clinica/{oque}/{id_}/remover")
def remover(request: Request, oque: str, id_: int):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    if oque not in _REMOVER:
        return _ir(request, "prof")
    aba, fn = _REMOVER[oque]
    with get_pool().connection() as c:
        fn(c, conta[0], id_)
        c.commit()
    return _ir(request, aba, "removido")


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.cl-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.cl-topo h2{margin:0;font-size:1.5rem;line-height:1.15}
.cl-topo .sub{color:var(--txt-mut);font-size:.88rem;margin-top:.25rem}
.cl-abas{display:flex;gap:.15rem;border-bottom:1px solid var(--borda);margin:1rem 0 1rem;overflow-x:auto}
.cl-abas a{padding:.45rem .8rem;font-size:.85rem;color:var(--txt-mut);text-decoration:none;border-bottom:2px solid transparent;white-space:nowrap}
.cl-abas a.on{color:var(--verde-claro);border-bottom-color:var(--verde);font-weight:600}
.cl-falta{background:var(--ambar-fundo);border:1px solid var(--ambar-borda);border-radius:11px;padding:.6rem .8rem;font-size:.85rem;margin-top:.8rem}
.cl-falta ul{margin:.3rem 0 0;padding-left:1.1rem}
.cl-ok{background:var(--neon-fundo);border:1px solid var(--neon-borda);border-radius:11px;padding:.6rem .8rem;font-size:.85rem;margin-top:.8rem}
.cl-lista{display:flex;flex-direction:column;gap:.45rem}
.cl-card{background:var(--card);border:1px solid var(--borda);border-left:4px solid var(--borda);border-radius:11px;padding:.6rem .8rem}
.cl-card .cab{display:flex;gap:.5rem;align-items:baseline;flex-wrap:wrap}
.cl-card .nome{font-weight:700;font-size:.95rem}
.cl-card .meta{font-size:.8rem;color:var(--txt-mut)}
.cl-chips{display:flex;gap:.3rem;flex-wrap:wrap;margin-top:.35rem}
.cl-chip{font-size:.7rem;padding:.1rem .5rem;border-radius:999px;border:1px solid var(--borda);color:var(--txt-mut)}
.cl-chip.on{border-color:var(--neon-borda);background:var(--neon-fundo);color:var(--txt)}
.cl-livres{font-size:.8rem;margin-top:.35rem;color:var(--txt)}
.cl-livres b{color:var(--verde-claro)}
.cl-gl{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;font-size:.84rem;padding:.3rem 0;border-top:1px dashed var(--borda)}
.cl-gl:first-of-type{border-top:0}
.cl-gl .mut{color:var(--txt-mut)}
details.cl-ed{margin-top:.4rem}
details.cl-ed summary{cursor:pointer;font-size:.82rem;color:var(--verde-claro);list-style:none;display:inline-flex;min-height:36px;align-items:center}
details.cl-ed summary::-webkit-details-marker{display:none}
.cl-form{display:grid;grid-template-columns:1fr 1fr;gap:.5rem .7rem;margin-top:.5rem}
.cl-form label{display:flex;flex-direction:column;font-size:.76rem;color:var(--txt-mut);gap:.2rem}
.cl-form .inteira{grid-column:1/-1}
.cl-form input[type=checkbox],.cl-form input[type=radio]{width:auto;min-height:0;margin:0}
.cl-form .cx{flex-direction:row;align-items:center;gap:.45rem;color:var(--txt);font-size:.84rem}
.cl-form .grupo{display:flex;flex-wrap:wrap;gap:.3rem .8rem}
.cl-form button{width:auto;margin:0;min-height:42px;padding:.45rem 1rem;font-size:.9rem}
.cl-acoes{display:flex;gap:.4rem;flex-wrap:wrap;grid-column:1/-1;align-items:center}
.cl-acoes form{margin:0}
.cl-acoes button.sec,.cl-rm button{background:transparent;border:1px solid var(--borda);color:var(--txt)}
.cl-rm{margin:0;display:inline}
.cl-rm button{width:auto;margin:0;min-height:32px;padding:.2rem .6rem;font-size:.78rem}
.cl-novo{background:var(--card);border:1px dashed var(--borda);border-radius:11px;padding:.6rem .8rem;margin-top:.8rem}
.cl-sec{margin:1.3rem 0 .5rem;font-size:1.02rem}
.cl-cores{display:flex;gap:.35rem;flex-wrap:wrap}
.cl-cores label{flex-direction:row;align-items:center;gap:.2rem}
.cl-cor{display:inline-block;width:18px;height:18px;border-radius:50%;border:1px solid var(--borda)}
@media (max-width:620px){.cl-form{grid-template-columns:1fr}}
</style>
<div class="cl-pag">
  <div class="cl-topo"><h2>Configurar clínica</h2>
    <div class="sub">Quem atende, onde, o quê e quando. É daqui que a agenda e o agente tiram os horários e os preços.</div></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  {% set r = resumo %}
  {% if r.sem_grade or r.sem_tipo or not r.profissionais or not r.locais or r.tipos_sem_preco %}
  <div class="cl-falta"><b>O que falta pra agenda funcionar sozinha</b><ul>
    {% if not r.profissionais %}<li>Cadastrar quem atende (aba Profissionais).</li>{% endif %}
    {% if not r.locais %}<li>Cadastrar o endereço da clínica (aba Locais).</li>{% endif %}
    {% for n in r.sem_grade %}<li>{{ n }} ainda não tem grade de horário.</li>{% endfor %}
    {% for n in r.sem_tipo %}<li>{{ n }} ainda não tem atendimentos marcados (aba Profissionais).</li>{% endfor %}
    {% if r.tipos_sem_preco %}<li>{{ r.tipos_sem_preco|length }} atendimento(s) sem preço: o agente responde "sob consulta" e a recepção informa.</li>{% endif %}
  </ul></div>
  {% else %}<div class="cl-ok">Tudo cadastrado: cada profissional tem grade e atendimentos.</div>{% endif %}

  <nav class="cl-abas">{% for k, rot in abas %}<a href="/painel/clinica/configurar?aba={{ k }}" class="{% if aba == k %}on{% endif %}">{{ rot }}</a>{% endfor %}</nav>

  {# ---------------------------------------------------------------- PROFISSIONAIS #}
  {% if aba == 'prof' %}
  <div class="cl-lista">
  {% for p in profs %}
    <div class="cl-card" style="border-left-color:{{ p.cor }}">
      <div class="cab"><span class="nome">{{ p.nome }}</span>
        <span class="meta">{{ p.funcao or 'função a confirmar' }}{% if p.conselho %} · {{ p.conselho }}{% endif %}</span>
        <span class="cl-chip">{{ ACESSO_D[p.acesso] }}</span></div>
      <div class="cl-chips">{% for t in tipos %}{% if t.id in p.tipos %}<span class="cl-chip on">{{ t.nome }}</span>{% endif %}{% endfor %}
        {% if not p.tipos %}<span class="meta">nenhum atendimento marcado</span>{% endif %}</div>
      {% if grade_de.get(p.id) %}<div class="meta" style="margin-top:.3rem">{% for g in grade_de[p.id] %}{{ g.dias_txt }} {{ '%02d:%02d'|format(g.inicio.hour, g.inicio.minute) }}–{{ '%02d:%02d'|format(g.fim.hour, g.fim.minute) }} · {{ g.local }}{% if not loop.last %} · {% endif %}{% endfor %}</div>{% endif %}
      <details class="cl-ed"><summary>Editar</summary>{% set f = p %}{% include "clinica_form_prof.html" %}
        <div class="cl-acoes" style="margin-top:.4rem"><form class="cl-rm" method="post" action="/painel/clinica/profissional/{{ p.id }}/remover" onsubmit="return confirm('Tirar da agenda? A grade deste profissional para de valer.')"><button>Tirar da agenda</button></form></div>
      </details>
    </div>
  {% else %}<div class="meta">Ninguém cadastrado ainda.</div>{% endfor %}
  </div>
  <div class="cl-novo"><details class="cl-ed" {% if not profs %}open{% endif %}><summary>+ Novo profissional</summary>{% set f = None %}{% include "clinica_form_prof.html" %}</details></div>
  {% endif %}

  {# ---------------------------------------------------------------- ATENDIMENTOS #}
  {% if aba == 'tipos' %}
  <div class="cl-lista">
  {% for t in tipos %}
    <div class="cl-card" style="border-left-color:{{ t.cor }}">
      <div class="cab"><span class="nome">{{ t.nome }}</span>
        <span class="meta">{{ t.duracao_min }} min · {{ CAT_D.get(t.categoria, t.categoria or 'sem categoria') }} · {{ t.preco }}{% if t.volta_dias %} · volta em {{ t.volta_dias }} dias{% endif %}</span></div>
      <div class="cl-chips">
        {% if t.agente_diz_preco %}<span class="cl-chip on">o agente diz o preço</span>{% endif %}
        {% if t.agente_marca %}<span class="cl-chip on">o agente marca sozinho</span>{% endif %}
        {% if t.volta_motivo %}<span class="cl-chip">por que volta: {{ t.volta_motivo }}</span>{% endif %}</div>
      <details class="cl-ed"><summary>Editar</summary>{% set f = t %}{% include "clinica_form_tipo.html" %}
        <div class="cl-acoes" style="margin-top:.4rem"><form class="cl-rm" method="post" action="/painel/clinica/tipo/{{ t.id }}/remover" onsubmit="return confirm('Tirar este atendimento da lista?')"><button>Tirar da lista</button></form></div>
      </details>
    </div>
  {% else %}<div class="meta">Nenhum atendimento ainda.</div>{% endfor %}
  </div>
  <div class="cl-novo"><details class="cl-ed" {% if not tipos %}open{% endif %}><summary>+ Novo atendimento</summary>{% set f = None %}{% include "clinica_form_tipo.html" %}</details></div>
  <div class="meta" style="margin-top:.6rem">Preço em branco é "sob consulta": o agente não inventa valor. O "por que volta" é só pra recepção, nunca vai na mensagem ao paciente.</div>
  {% endif %}

  {# ---------------------------------------------------------------- LOCAIS #}
  {% if aba == 'locais' %}
  <div class="cl-lista">
  {% for l in locais %}
    <div class="cl-card">
      <div class="cab"><span class="nome">{{ l.nome }}</span><span class="cl-chip {% if l.tipo == 'sede' %}on{% endif %}">{{ 'sede' if l.tipo == 'sede' else 'cidade de viagem' }}</span>
        <span class="meta">{{ l.endereco or 'endereço a confirmar' }}{% if l.cidade %} · {{ l.cidade }}{% endif %}</span></div>
      <details class="cl-ed"><summary>Editar</summary>{% set f = l %}{% include "clinica_form_local.html" %}
        <div class="cl-acoes" style="margin-top:.4rem"><form class="cl-rm" method="post" action="/painel/clinica/local/{{ l.id }}/remover" onsubmit="return confirm('Tirar este local da lista?')"><button>Tirar da lista</button></form></div>
      </details>
    </div>
  {% else %}<div class="meta">Nenhum local ainda.</div>{% endfor %}
  </div>
  <div class="cl-novo"><details class="cl-ed" {% if not locais %}open{% endif %}><summary>+ Novo local</summary>{% set f = None %}{% include "clinica_form_local.html" %}</details></div>
  <div class="meta" style="margin-top:.6rem">Local é onde a pessoa vai estar, com o endereço que sai na mensagem de confirmação.</div>
  {% endif %}

  {# ---------------------------------------------------------------- GRADE #}
  {% if aba == 'grade' %}
  <div class="cl-lista">
  {% for p in profs %}{% if p.funcao != 'Recepção, não atende' %}
    <div class="cl-card" style="border-left-color:{{ p.cor }}">
      <div class="cab"><span class="nome">{{ p.nome }}</span><span class="meta">{{ p.funcao or 'função a confirmar' }}</span></div>
      {% for g in grade_de.get(p.id, []) %}
        <div class="cl-gl"><b>{{ g.dias_txt }}</b><span>{{ '%02d:%02d'|format(g.inicio.hour, g.inicio.minute) }}–{{ '%02d:%02d'|format(g.fim.hour, g.fim.minute) }}</span>
          <span class="mut">{{ g.local }} · {{ g.repete_txt }}{% if g.encaixes %} · {{ g.encaixes }} encaixe(s) por dia{% endif %}</span>
          <form class="cl-rm" method="post" action="/painel/clinica/grade/{{ g.id }}/remover"><button>Tirar</button></form></div>
      {% else %}<div class="meta" style="margin-top:.3rem">Sem grade: ainda não aparece horário livre na agenda.</div>{% endfor %}
      {% if amostra.get(p.id) and grade_de.get(p.id) %}<div class="cl-livres">Próximos horários livres de <b>{{ amostra[p.id].tipo }}</b>: {% if amostra[p.id].livres %}{{ amostra[p.id].livres|join(' · ') }}{% else %}nenhum nos próximos 14 dias{% endif %}</div>{% endif %}
    </div>
  {% endif %}{% else %}<div class="meta">Cadastre os profissionais primeiro.</div>{% endfor %}
  </div>

  {% if profs and locais %}
  <div class="cl-novo"><details class="cl-ed" {% if not grade_de %}open{% endif %}><summary>+ Nova faixa de horário</summary>
    <form class="cl-form" method="post" action="/painel/clinica/grade">
      <label>Profissional<select name="profissional_id">{% for p in profs %}{% if p.funcao != 'Recepção, não atende' %}<option value="{{ p.id }}">{{ p.nome }}</option>{% endif %}{% endfor %}</select></label>
      <label>Local<select name="local_id">{% for l in locais %}<option value="{{ l.id }}">{{ l.nome }}</option>{% endfor %}</select></label>
      <div class="inteira"><span class="meta">Dias</span><div class="grupo">{% for d, rot in DIAS %}<label class="cx"><input type="checkbox" name="dias" value="{{ d }}" {% if d <= 5 %}checked{% endif %}> {{ rot }}</label>{% endfor %}</div></div>
      <label>Início<input type="time" name="inicio" value="08:00" required></label>
      <label>Fim<input type="time" name="fim" value="12:00" required></label>
      <label>Repete<select name="repete">{% for k, rot in REPETE %}<option value="{{ k }}">{{ rot }}</option>{% endfor %}</select></label>
      <label>Encaixes por dia<input type="number" name="encaixes" min="0" max="20" value="0" inputmode="numeric"></label>
      <label>Se for uma vez por mês: qual semana<select name="semana_do_mes"><option value="">—</option>{% for n in [1,2,3,4,5] %}<option value="{{ n }}">{{ {1:'1ª',2:'2ª',3:'3ª',4:'4ª',5:'a última'}[n] }} do mês</option>{% endfor %}</select></label>
      <label>Se for a cada 15 dias: uma data em que acontece<input type="date" name="referencia" value="{{ hoje }}"></label>
      <div class="cl-acoes"><button>Salvar faixa</button><span class="meta">Manhã e tarde são duas faixas (ex.: 08:00–12:00 e 13:30–16:30).</span></div>
    </form></details></div>
  {% endif %}

  <h3 class="cl-sec">Bloqueios e exceções</h3>
  <div class="cl-lista">
  {% for b in bloqueios %}
    <div class="cl-card"><div class="cl-gl"><b>{{ b.de.strftime('%d/%m/%Y') }}{% if b.ate != b.de %} a {{ b.ate.strftime('%d/%m/%Y') }}{% endif %}</b>
      <span>{% if b.inicio %}{{ '%02d:%02d'|format(b.inicio.hour, b.inicio.minute) }}–{{ '%02d:%02d'|format(b.fim.hour, b.fim.minute) }}{% else %}dia todo{% endif %}</span>
      <span class="mut">{{ nome_prof.get(b.profissional_id, 'profissional removido') if b.profissional_id else 'a clínica toda' }}{% if b.motivo %} · {{ b.motivo }}{% endif %}</span>
      <form class="cl-rm" method="post" action="/painel/clinica/bloqueio/{{ b.id }}/remover"><button>Tirar</button></form></div></div>
  {% else %}<div class="meta">Nenhum bloqueio daqui pra frente.</div>{% endfor %}
  </div>
  <div class="cl-novo"><details class="cl-ed"><summary>+ Novo bloqueio (congresso, férias, feriado)</summary>
    <form class="cl-form" method="post" action="/painel/clinica/bloqueio">
      <label>Quem<select name="profissional_id"><option value="">A clínica toda</option>{% for p in profs %}<option value="{{ p.id }}">{{ p.nome }}</option>{% endfor %}</select></label>
      <label>Motivo<input name="motivo" maxlength="120" placeholder="congresso, férias, feriado"></label>
      <label>De<input type="date" name="de" value="{{ hoje }}" required></label>
      <label>Até (em branco = o mesmo dia)<input type="date" name="ate"></label>
      <label>Das (em branco = o dia todo)<input type="time" name="inicio"></label>
      <label>Às<input type="time" name="fim"></label>
      <div class="cl-acoes"><button>Salvar bloqueio</button></div>
    </form></details></div>
  {% endif %}
</div>
{% endblock %}"""

_TPL_PROF = r"""<form class="cl-form" method="post" action="/painel/clinica/profissional">
  {% if f %}<input type="hidden" name="id" value="{{ f.id }}">{% endif %}
  <label>Nome na agenda<input name="nome" maxlength="80" required value="{{ f.nome if f else '' }}" placeholder="Dr. Manoel"></label>
  <label>Função<input name="funcao" maxlength="80" list="cl-funcoes" value="{{ f.funcao if f else '' }}"></label>
  <label>Especialidade<input name="especialidade" maxlength="80" value="{{ f.especialidade if f else '' }}"></label>
  <label>Conselho (opcional)<input name="conselho" maxlength="40" value="{{ f.conselho if f else '' }}" placeholder="CRM-MA 00000"></label>
  <div class="inteira"><span class="meta">Cor na agenda</span><div class="cl-cores">{% for c in CORES %}<label class="cx"><input type="radio" name="cor" value="{{ c }}" {% if (f and f.cor == c) or (not f and loop.first) %}checked{% endif %}><span class="cl-cor" style="background:{{ c }}"></span></label>{% endfor %}</div></div>
  <label>Acesso ao Zaq<select name="acesso">{% for k, rot in ACESSOS %}<option value="{{ k }}" {% if f and f.acesso == k %}selected{% endif %}>{{ rot }}</option>{% endfor %}</select></label>
  <label>Quem da equipe é (se entra no Zaq)<select name="membro_id"><option value="">—</option>{% for m in membros %}<option value="{{ m.id }}" {% if f and f.membro_id == m.id %}selected{% endif %}>{{ m.nome }}</option>{% endfor %}</select></label>
  <div class="inteira"><span class="meta">Faz estes atendimentos (é o que deixa o agente oferecer o horário certo)</span>
    <div class="grupo">{% for t in tipos %}<label class="cx"><input type="checkbox" name="tipos" value="{{ t.id }}" {% if f and t.id in f.tipos %}checked{% endif %}> {{ t.nome }}</label>{% else %}<span class="meta">cadastre os atendimentos primeiro</span>{% endfor %}</div></div>
  <label class="cx inteira"><input type="checkbox" name="aviso_agenda" value="1" {% if f and f.aviso_agenda %}checked{% endif %}> Recebe a agenda do dia no WhatsApp às 7h (quando a agenda estiver no ar)</label>
  <div class="cl-acoes"><button>Salvar</button></div>
  <datalist id="cl-funcoes">{% for fn in FUNCOES %}<option value="{{ fn }}">{% endfor %}</datalist>
</form>"""

_TPL_TIPO = r"""<form class="cl-form" method="post" action="/painel/clinica/tipo">
  {% if f %}<input type="hidden" name="id" value="{{ f.id }}">{% endif %}
  <label>Nome<input name="nome" maxlength="80" required value="{{ f.nome if f else '' }}" placeholder="Consulta"></label>
  <label>Categoria<select name="categoria">{% if f and f.categoria not in CAT_D %}<option value="{{ f.categoria }}" selected>{{ f.categoria or '— sem categoria —' }}</option>{% endif %}{% for k, rot in CAT %}<option value="{{ k }}" {% if f and f.categoria == k %}selected{% endif %}>{{ rot }}</option>{% endfor %}</select></label>
  <label>Duração (min)<input type="number" name="duracao_min" min="5" max="480" step="5" inputmode="numeric" value="{{ f.duracao_min if f else 30 }}"></label>
  <label>Preço particular (em branco = sob consulta)<input name="preco" inputmode="decimal" value="{{ ('%.2f'|format(f.preco_centavos/100)).replace('.', ',') if f and f.preco_centavos else '' }}" placeholder="500,00"></label>
  <label>Volta em (dias, opcional)<input type="number" name="volta_dias" min="1" max="730" inputmode="numeric" value="{{ f.volta_dias if f and f.volta_dias else '' }}"></label>
  <label>Por que volta (só pra recepção)<input name="volta_motivo" maxlength="200" value="{{ f.volta_motivo if f else '' }}" placeholder="reaplicação, resultado da biópsia"></label>
  <div class="inteira"><span class="meta">Cor na agenda</span><div class="cl-cores">{% for c in CORES %}<label class="cx"><input type="radio" name="cor" value="{{ c }}" {% if (f and f.cor == c) or (not f and loop.first) %}checked{% endif %}><span class="cl-cor" style="background:{{ c }}"></span></label>{% endfor %}</div></div>
  <label class="cx inteira"><input type="checkbox" name="agente_diz_preco" value="1" {% if f and f.agente_diz_preco %}checked{% endif %}> O agente pode dizer este preço no WhatsApp (desmarcado, ele responde "sob consulta" e a recepção informa)</label>
  <label class="cx inteira"><input type="checkbox" name="agente_marca" value="1" {% if f and f.agente_marca %}checked{% endif %}> O agente pode marcar este atendimento sozinho (quando o agente de agenda estiver no ar)</label>
  <div class="cl-acoes"><button>Salvar</button></div>
</form>"""

_TPL_LOCAL = r"""<form class="cl-form" method="post" action="/painel/clinica/local">
  {% if f %}<input type="hidden" name="id" value="{{ f.id }}">{% endif %}
  <label>Nome<input name="nome" maxlength="80" required value="{{ f.nome if f else '' }}" placeholder="Espaço Pelle"></label>
  <label>Tipo<select name="tipo"><option value="sede" {% if f and f.tipo == 'sede' %}selected{% endif %}>Sede</option><option value="viagem" {% if f and f.tipo == 'viagem' %}selected{% endif %}>Cidade de viagem</option></select></label>
  <label>Endereço (sai na confirmação)<input name="endereco" maxlength="160" value="{{ f.endereco if f else '' }}" placeholder="Rua Óscar Galvão, 38"></label>
  <label>Cidade<input name="cidade" maxlength="80" value="{{ f.cidade if f else '' }}"></label>
  <div class="cl-acoes"><button>Salvar</button></div>
</form>"""

# ".html" NÃO É ENFEITE: o `_env` liga o autoescape pela extensão.
_env.loader.mapping["clinica_configurar.html"] = _TPL
_env.loader.mapping["clinica_form_prof.html"] = _TPL_PROF
_env.loader.mapping["clinica_form_tipo.html"] = _TPL_TIPO
_env.loader.mapping["clinica_form_local.html"] = _TPL_LOCAL
