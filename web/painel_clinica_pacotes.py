"""Pacotes e retornos da clínica (finance/clinica_pacotes.py).

  /painel/clinica/pacotes          quem precisa marcar a próxima sessão, os retornos,
                                   os pacotes (saldo, validade) e a configuração
  /painel/clinica/pacotes/{id}     o pacote: as sessões usadas, com data; encerrar

A mesma porta da agenda da clínica. Encerrar pacote e mudar a configuração: dono e gestor.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import clinica_agenda as ca
from finance import clinica_pacotes as ckp
from web.painel_clinica_agenda import _acesso
from web.portal import _env, _render

router = APIRouter()
URL = "/painel/clinica/pacotes"
_AVISOS = {"config": "Configuração salva.", "encerrado": "Pacote encerrado. O saldo fica registrado.",
           "dispensado": "Retorno tirado da fila."}


def _ir(request: Request, url: str, aviso: str = "", erro: str = "") -> RedirectResponse:
    if erro:
        request.session["pacotes_erro"] = erro[:300]
    if aviso in _AVISOS:
        url += ("&" if "?" in url else "?") + f"aviso={aviso}"
    return RedirectResponse(url, status_code=303)


def _ctx(request: Request) -> dict:
    return {"aviso": _AVISOS.get(request.query_params.get("aviso") or "", ""),
            "erro": request.session.pop("pacotes_erro", ""), "secao_ativa": "agenda"}


@router.get(URL, response_class=HTMLResponse)
def lista(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    agora = datetime.now(timezone.utc)
    hoje = ca.hoje_br(agora)
    with get_pool().connection() as c:
        pacotes = ckp.listar(c, conta[0], hoje)
        marcar = {k["id"] for k in ckp.precisam_marcar(c, conta[0], agora)}
        atrasados = {k["id"] for k in pacotes if k["estado"] == "ativo" and ckp.atrasado(c, conta[0], k, hoje)}
        retornos = ckp.retornos(c, conta[0], agora, dias=14)
        cfg = ckp.config(c, conta[0])
    ativos = [k for k in pacotes if k["estado"] == "ativo"]
    return _render("clinica_pacotes.html", request, titulo="Pacotes e retornos", **_ctx(request),
                   ativos=ativos, marcar=[k for k in ativos if k["id"] in marcar], marcar_ids=marcar,
                   atrasados=atrasados, outros=[k for k in pacotes if k["estado"] != "ativo"][:30],
                   retornos=retornos, cfg=cfg, gerencia=gerencia, hoje=hoje,
                   vendidas=sum(k["total"] for k in pacotes if k["estado"] != "encerrado"),
                   usadas=sum(k["usadas"] for k in pacotes if k["estado"] != "encerrado"),
                   devidas=sum(k["saldo"] for k in ativos))


@router.post(URL + "/config")
def salvar_config(request: Request, validade: str = Form(""), lembretes: str = Form("ligado"),
                  aviso: str = Form(""), bloqueia: str = Form("")):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return _ir(request, URL, erro="Só o dono ou o gestor muda a configuração dos pacotes.")
    with get_pool().connection() as c:
        erro = ckp.salvar_config(c, conta[0], validade=validade, lembretes=lembretes, aviso=aviso,
                                 bloqueia=bool(bloqueia))
        (c.rollback if erro else c.commit)()
    return _ir(request, URL, "" if erro else "config", erro or "")


@router.post(URL + "/retorno/{retorno_id}/dispensar")
def dispensar(request: Request, retorno_id: int):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        ckp.dispensar_retorno(c, conta[0], retorno_id)
        c.commit()
    return _ir(request, URL, "dispensado")


@router.get(URL + "/{pacote_id}", response_class=HTMLResponse)
def ver(request: Request, pacote_id: int):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        k = ckp.pacote(c, conta[0], pacote_id)
        if not k:
            return RedirectResponse(URL, status_code=303)
        usos = ckp.consumos(c, conta[0], pacote_id)
        atraso = ckp.atrasado(c, conta[0], k)
    return _render("clinica_pacote.html", request, titulo="Pacote", **_ctx(request), k=k, usos=usos,
                   atraso=atraso, gerencia=gerencia)


@router.post(URL + "/{pacote_id}/encerrar")
def encerrar(request: Request, pacote_id: int, motivo: str = Form("")):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return _ir(request, f"{URL}/{pacote_id}", erro="Só o dono ou o gestor encerra um pacote com saldo.")
    with get_pool().connection() as c:
        ok = ckp.encerrar(c, conta[0], pacote_id, motivo, request.session.get("membro_id"))
        c.commit()
    return _ir(request, f"{URL}/{pacote_id}", "encerrado" if ok else "")


_CSS = r"""<style>
.pk-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.pk-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.pk-topo h2{margin:0;font-size:1.5rem}.pk-topo .sub{color:var(--txt-mut);font-size:.86rem;margin-top:.2rem;max-width:64ch}
.pk-kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem;margin:1rem 0}
.pk-kpi div{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.55rem .75rem}
.pk-kpi b{display:block;font-size:1.3rem}.pk-kpi span{font-size:.72rem;color:var(--txt-mut)}
.pk-sec{margin:1.2rem 0 .4rem;font-size:1.02rem}
.pk-l{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.2rem .6rem;padding:.5rem .1rem;border-top:1px solid var(--borda)}
.pk-l a.q{font-weight:600;color:var(--txt);text-decoration:none}.pk-m{font-size:.8rem;color:var(--txt-mut)}
.pk-chip{font-size:.7rem;padding:.08rem .45rem;border-radius:999px;border:1px solid var(--borda);color:var(--txt-mut);margin-left:.3rem}
.pk-chip.al{border-color:var(--ambar-borda);background:var(--ambar-fundo);color:#F0DCA6}
.pk-bar{display:flex;gap:3px;margin-top:.3rem}.pk-bar i{width:14px;height:8px;border-radius:3px;background:var(--borda)}
.pk-bar i.u{background:var(--verde)}
.pk-acoes{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center}.pk-acoes form{margin:0}
.pk-acoes a,.pk-acoes button{width:auto;margin:0;min-height:36px;padding:.3rem .7rem;font-size:.82rem;border-radius:8px}
.pk-acoes .sec,.pk-acoes a{background:transparent;border:1px solid var(--borda);color:var(--txt);text-decoration:none;display:inline-flex;align-items:center}
.pk-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin-top:1.4rem}
.pk-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.6rem}
.pk-grid label{display:block;font-size:.78rem;color:var(--txt-mut);margin-bottom:.15rem}
</style>"""

_TPL = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
{% macro barra(k) %}<div class="pk-bar">{% for i in range(k.total) %}<i class="{% if i < k.usadas %}u{% endif %}"></i>{% endfor %}</div>{% endmacro %}
<div class="pk-pag">
  <div class="pk-topo"><div><h2>Pacotes e retornos</h2>
    <div class="sub">As sessões vendidas no plano de tratamento viram saldo; cada atendimento finalizado baixa uma. Retorno pedido pelo médico vira prazo, e o Zaq chama o paciente antes.</div></div>
    <div class="pk-acoes"><a href="/painel/clinica/agenda">‹ Agenda</a><a href="/painel/clinica/planos">Planos</a></div></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  <div class="pk-kpi">
    <div><span>Sessões vendidas</span><b>{{ vendidas }}</b></div>
    <div><span>Usadas</span><b>{{ usadas }}</b></div>
    <div><span>Devidas (saldo ativo)</span><b>{{ devidas }}</b><span>é atendimento que a clínica ainda deve</span></div>
    <div><span>Precisam marcar</span><b>{{ marcar|length }}</b></div>
  </div>

  <h3 class="pk-sec">Precisam marcar a próxima sessão</h3>
  {% for k in marcar %}<div class="pk-l"><div><a class="q" href="/painel/clinica/pacotes/{{ k.id }}">{{ k.paciente }}</a>
      <span class="pk-chip">{{ k.nome }}</span>{% if k.id in atrasados %}<span class="pk-chip al">parcela atrasada</span>{% endif %}
      <div class="pk-m">{{ k.usadas }} de {{ k.total }} usadas · a {{ k.proxima_n }}ª fica boa desde {{ k.proxima.strftime('%d/%m') }}</div></div>
    <div class="pk-acoes"><a href="/painel/clinica/agenda/novo?{% if k.profissional_id %}prof={{ k.profissional_id }}&{% endif %}tipo={{ k.servico_id }}&data={{ (k.proxima if k.proxima > hoje else hoje).isoformat() }}&lead={{ k.lead or '' }}">Marcar</a></div></div>
  {% else %}<div class="pk-m">Ninguém com sessão liberada sem marcar. 🎉</div>{% endfor %}

  <h3 class="pk-sec">Retornos (próximos 14 dias e vencidos)</h3>
  {% for r in retornos %}<div class="pk-l"><div><b>{{ r.paciente }}</b>{% if r.prof %} · {{ r.prof }}{% endif %}
      {% if r.vencido %}<span class="pk-chip al">venceu {{ r.vence_em.strftime('%d/%m') }}</span>{% else %}<span class="pk-chip">até {{ r.vence_em.strftime('%d/%m') }}</span>{% endif %}</div>
    <div class="pk-acoes"><a href="/painel/clinica/agenda/novo?{% if r.profissional_id %}prof={{ r.profissional_id }}&{% endif %}lead={{ r.lead or '' }}">Marcar</a>
      <form method="post" action="/painel/clinica/pacotes/retorno/{{ r.id }}/dispensar"><button class="sec">Tirar da fila</button></form></div></div>
  {% else %}<div class="pk-m">Nenhum retorno chegando.</div>{% endfor %}

  <h3 class="pk-sec">Pacotes ativos</h3>
  {% for k in ativos %}<div class="pk-l"><div><a class="q" href="/painel/clinica/pacotes/{{ k.id }}">{{ k.paciente }}</a>
      <span class="pk-chip">{{ k.nome }}</span>{% if k.vence_logo %}<span class="pk-chip al">vence {{ k.validade_ate.strftime('%d/%m/%Y') }}</span>{% endif %}{% if k.id in atrasados %}<span class="pk-chip al">parcela atrasada</span>{% endif %}
      {{ barra(k) }}</div><div class="pk-m">{{ k.saldo }} a usar</div></div>
  {% else %}<div class="pk-m">Nenhum pacote ativo. Eles nascem do plano de tratamento aceito.</div>{% endfor %}

  {% if outros %}<h3 class="pk-sec">Concluídos, vencidos e encerrados</h3>
  {% for k in outros %}<div class="pk-l"><div><a class="q" href="/painel/clinica/pacotes/{{ k.id }}">{{ k.paciente }}</a><span class="pk-chip">{{ k.nome }}</span>
    <span class="pk-chip">{{ {'concluido':'concluído','vencido':'venceu com saldo','encerrado':'encerrado'}[k.estado] }}</span></div>
    <div class="pk-m">{{ k.usadas }} de {{ k.total }}</div></div>{% endfor %}{% endif %}

  {% if gerencia %}
  <form class="pk-cx" method="post" action="/painel/clinica/pacotes/config">
    <b>Configuração</b>
    <div class="pk-m" style="margin:.3rem 0 .6rem">Os lembretes (próxima sessão liberada, retorno chegando, pacote perto de vencer) saem só no horário de atendimento, no máximo 1 mensagem automática por paciente por dia, e nunca dizem o procedimento.</div>
    <div class="pk-grid">
      <div><label>Validade do pacote (meses)</label><input name="validade" inputmode="numeric" value="{{ cfg.validade_meses }}"></div>
      <div><label>Chamar pro retorno quantos dias antes</label><input name="aviso" inputmode="numeric" value="{{ cfg.retorno_aviso_dias }}"></div>
      <div><label>Lembretes automáticos</label><select name="lembretes"><option value="ligado" {% if cfg.lembretes == 'ligado' %}selected{% endif %}>Ligados</option><option value="off" {% if cfg.lembretes == 'off' %}selected{% endif %}>Desligados</option></select></div>
      <div><label style="display:flex;gap:.4rem;align-items:center;margin-top:1.2rem"><input type="checkbox" name="bloqueia" value="1" style="width:auto" {% if cfg.bloqueia_atrasado %}checked{% endif %}> Sessão só com parcela em dia</label></div>
    </div>
    <div class="pk-acoes" style="margin-top:.7rem"><button>Salvar</button></div>
  </form>
  {% endif %}
</div>
{% endblock %}"""

_TPL_UM = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="pk-pag">
  <div class="pk-topo"><div><h2>{{ k.paciente }} · {{ k.nome }}</h2>
    <div class="sub">{{ k.usadas }} de {{ k.total }} sessões usadas{% if k.validade_ate %} · válido até {{ k.validade_ate.strftime('%d/%m/%Y') }}{% endif %} · intervalo de {{ k.intervalo }} dias{% if atraso %} · <b>parcela atrasada</b>{% endif %}</div></div>
    <div class="pk-acoes"><a href="/painel/clinica/pacotes">‹ Pacotes</a>{% if k.plano_id %}<a href="/painel/clinica/planos/{{ k.plano_id }}">Plano</a>{% endif %}</div></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  <div class="pk-cx">
    <b>Sessões usadas</b>
    {% for u in usos %}<div class="pk-l"><div>{{ loop.index }}ª · {{ u.quando }}{% if u.prof %} · {{ u.prof }}{% endif %}</div><div class="pk-acoes"><a href="/painel/clinica/agenda/evento/{{ u.evento_id }}">ver</a></div></div>
    {% else %}<div class="pk-m">Nenhuma ainda.</div>{% endfor %}
    {% if k.estado == 'encerrado' %}<div class="pk-m" style="margin-top:.5rem">Encerrado: {{ k.motivo }} · saldo de {{ k.saldo }} registrado.</div>{% endif %}
  </div>
  {% if k.estado == 'ativo' and k.saldo %}
  <div class="pk-acoes" style="margin-top:.8rem"><a href="/painel/clinica/agenda/novo?{% if k.profissional_id %}prof={{ k.profissional_id }}&{% endif %}tipo={{ k.servico_id }}&data={{ k.proxima.isoformat() }}&lead={{ k.lead or '' }}">Marcar a {{ k.proxima_n }}ª sessão</a></div>
  {% endif %}
  {% if gerencia and k.estado in ('ativo','vencido') and k.saldo %}
  <form class="pk-cx" method="post" action="/painel/clinica/pacotes/{{ k.id }}/encerrar">
    <b>Encerrar com saldo</b><div class="pk-m" style="margin:.3rem 0 .5rem">Desistência ou reembolso: as {{ k.saldo }} sessões que sobram ficam registradas com o motivo.</div>
    <input name="motivo" placeholder="Motivo (ex.: reembolso de 2 sessões)" maxlength="200">
    <div class="pk-acoes" style="margin-top:.6rem"><button class="sec">Encerrar pacote</button></div>
  </form>
  {% endif %}
</div>
{% endblock %}"""

_env.loader.mapping["clinica_pacotes.html"] = _TPL
_env.loader.mapping["clinica_pacote.html"] = _TPL_UM
