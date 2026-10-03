"""A tela "Funil da clínica" (finance/clinica_funil.py).

  /painel/clinica/funil            as colunas, o que anda sozinho e o ensaio
  POST /painel/clinica/funil       aplica as colunas marcadas e as regras
  POST /painel/clinica/funil/regras  muda só as regras (depois de aplicado)

Só o dono e o gestor (é a mesma régua do funil de todo o produto). Rascunho aprovado em
03/10/2026: "Ligar o funil da Pelle", decisões A a D.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import clinica_funil as cfu
from web.painel_clinica_agenda import _acesso
from web.portal import _env, _render

router = APIRouter()
URL = "/painel/clinica/funil"

_AVISOS = {"aplicado": "Funil da clínica aplicado. Nenhum cartão mudou de coluna.",
           "regras": "Regras salvas."}


def _ir(request: Request, aviso: str = "", erro: str = "") -> RedirectResponse:
    if erro:
        request.session["funil_erro"] = erro[:300]
    return RedirectResponse(URL + (f"?aviso={aviso}" if aviso in _AVISOS else ""), status_code=303)


def _gerencia(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return None, redir
    if not gerencia:
        return None, RedirectResponse("/painel/clinica/agenda", status_code=303)
    return conta, None


@router.get(URL, response_class=HTMLResponse)
def ver(request: Request):
    conta, redir = _gerencia(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        est = cfu.estado(c, conta[0])
    return _render("clinica_funil.html", request, titulo="Funil da clínica", secao_ativa="agenda",
                   aviso=_AVISOS.get(request.query_params.get("aviso") or "", ""),
                   erro=request.session.pop("funil_erro", ""), e=est, MODO_D=cfu.MODO_D,
                   prazo_dias=cfu.PRAZO_DIAS, prazo_renov=cfu.PRAZO_RENOVACOES, ensaio_dias=cfu.ENSAIO_DIAS)


def _escolhas(form) -> dict:
    return {k: (form.get(k) or "off") for k in cfu.REGRAS}


@router.post(URL)
async def aplicar(request: Request):
    conta, redir = _gerencia(request)
    if redir is not None:
        return redir
    form = await request.form()
    with get_pool().connection() as c:
        _feito, erro = cfu.aplicar(c, conta[0], form.getlist("item"), _escolhas(form))
        (c.rollback if erro else c.commit)()
    return _ir(request, "" if erro else "aplicado", erro or "")


@router.post(URL + "/regras")
async def regras(request: Request):
    conta, redir = _gerencia(request)
    if redir is not None:
        return redir
    form = await request.form()
    with get_pool().connection() as c:
        erro = cfu.salvar_regras(c, conta[0], _escolhas(form))
        (c.rollback if erro else c.commit)()
    return _ir(request, "" if erro else "regras", erro or "")


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.fu-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.fu-sub{color:var(--texto-2,#6b7280);font-size:.92rem;max-width:72ch}
.fu-passos{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.8rem;margin-top:1rem}
@media (max-width:900px){.fu-passos{grid-template-columns:1fr}}
.fu-cx{border:1px solid var(--borda);border-radius:12px;padding:.9rem 1rem;display:grid;gap:.6rem;align-content:start;min-width:0}
.fu-cx h3{margin:0;font-size:1rem}
.fu-cx h3 small{display:block;font-weight:400;font-size:.8rem;opacity:.7}
.fu-lin{display:grid;grid-template-columns:auto minmax(0,1fr);gap:.5rem;font-size:.9rem;align-items:start}
.fu-lin input{margin-top:.25rem;width:auto}
.fu-de{text-decoration:line-through;opacity:.6}
.fu-nota{font-size:.78rem;opacity:.7}
.fu-reg{display:grid;gap:.3rem;font-size:.9rem;border-top:1px solid var(--borda);padding-top:.5rem}
.fu-reg:first-of-type{border-top:0;padding-top:0}
.fu-tri{display:flex;flex-wrap:wrap;gap:.3rem}
.fu-tri label{display:inline-flex;gap:.3rem;align-items:center;border:1px solid var(--borda);border-radius:8px;padding:.15rem .55rem;font-size:.82rem;cursor:pointer}
.fu-tri input{width:auto;margin:0}
.fu-res{display:grid;gap:.3rem;font-size:.9rem}
.fu-res div{display:flex;justify-content:space-between;gap:.6rem;border-bottom:1px dashed var(--borda);padding-bottom:.2rem}
.fu-res b{font-variant-numeric:tabular-nums}
.fu-chip{display:inline-block;border-radius:999px;padding:0 .5rem;font-size:.74rem;font-weight:700;border:1px solid var(--borda)}
</style>
<div class="fu-pag">
  <h2 style="margin:0">Funil da clínica</h2>
  <div class="fu-sub">O funil aprovado em 01/10: Consulta, Em tratamento e Retorno, e o cartão andando sozinho pela agenda, pelo plano e pelo pacote. Aplicar não muda nenhum cartão de coluna.</div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}

  {% macro regras(nome_form) %}
    <div class="fu-reg"><b>A agenda move o cartão</b><span class="fu-nota">Presente, Faltou, Finalizar, plano, pagamento, sessões e retorno. Vem junto com as colunas.</span><span class="fu-chip">ligado</span></div>
    <div class="fu-reg"><b>Primeira resposta nossa leva para Em conversa</b>
      <div class="fu-tri">{% for m in ('off','observando','ligado') %}<label><input type="radio" name="resposta" value="{{ m }}" {% if e.modos.resposta == m or (not e.aplicado and m == 'observando') %}checked{% endif %}> {{ MODO_D[m] }}</label>{% endfor %}</div></div>
    <div class="fu-reg"><b>Prazo de Em conversa: {{ prazo_dias }} dias, {{ prazo_renov }} renovações</b><span class="fu-nota">renovar pede justificativa; vencido, ninguém perde o cartão sozinho: a recepção decide</span>
      <div class="fu-tri">{% for m in ('off','observando','ligado') %}<label><input type="radio" name="prazo" value="{{ m }}" {% if e.modos.prazo == m or (not e.aplicado and m == 'observando') %}checked{% endif %}> {{ MODO_D[m] }}</label>{% endfor %}</div></div>
    <div class="fu-reg"><b>Perdido volta a Em conversa quando a pessoa escreve</b>
      <div class="fu-tri">{% for m in ('off','ligado') %}<label><input type="radio" name="reabre" value="{{ m }}" {% if e.modos.reabre == m or (not e.aplicado and m == 'ligado') %}checked{% endif %}> {{ MODO_D[m] }}</label>{% endfor %}</div></div>
    <div class="fu-reg"><b>Mensagens automáticas (chamar de novo, resgate)</b><span class="fu-nota">ficam para as próximas entregas: a recepção primeiro</span><span class="fu-chip">desligado</span></div>
  {% endmacro %}

  {% if not e.aplicado %}
  <form method="post" action="/painel/clinica/funil" onsubmit="this.querySelector('button').disabled=true">
  <div class="fu-passos">
    <div class="fu-cx"><h3>1. As colunas<small>o que o desenho aprovado pede já vem marcado</small></h3>
      {% for it in e.itens %}<label class="fu-lin"><input type="checkbox" name="item" value="{{ it.id }}" {% if it.marcado %}checked{% endif %}>
        <span>{% if it.acao == 'criar' %}{{ it.para }} <span class="fu-chip">nova</span>{% elif it.acao == 'rotulo' %}<span class="fu-de">{{ it.de }}</span> → {{ it.para }}{% else %}{{ it.texto }}{% endif %}{% if it.nota %}<br><span class="fu-nota">{{ it.nota }}</span>{% endif %}</span></label>
      {% else %}<div class="fu-nota">As colunas já estão iguais ao modelo.</div>{% endfor %}
    </div>
    <div class="fu-cx"><h3>2. O que anda sozinho<small>ensaio: a regra não mexe em nada e conta o que teria feito</small></h3>{{ regras('aplicar') }}</div>
    <div class="fu-cx"><h3>3. Conferir e aplicar<small>o que muda no dia</small></h3>
      <div class="fu-res">
        <div><span>Colunas novas</span><b>{{ e.itens|selectattr('acao','equalto','criar')|list|length }}</b></div>
        <div><span>Nomes trocados</span><b>{{ e.itens|selectattr('acao','equalto','rotulo')|list|length }}</b></div>
        <div><span>Cartões que mudam de coluna</span><b>0</b></div>
        <div><span>Cartões hoje no funil</span><b>{{ e.cards.values()|sum }}</b></div>
      </div>
      <button>Aplicar o funil da clínica</button>
    </div>
  </div>
  </form>
  {% else %}
  <form method="post" action="/painel/clinica/funil/regras" onsubmit="this.querySelector('button').disabled=true">
  <div class="fu-passos">
    <div class="fu-cx"><h3>As colunas<small>aplicadas</small></h3>
      <div class="fu-nota">O funil da clínica está no quadro. Nome, prazo e gatilho de cada coluna também ficam em <a href="/painel/prospeccao/regua#etapas">Funil › Régua</a>.</div>
      {% for it in e.itens %}<div class="fu-nota">Ainda diferente do modelo: {{ it.texto or it.para }}</div>{% endfor %}
    </div>
    <div class="fu-cx"><h3>O que anda sozinho</h3>{{ regras('regras') }}<button>Salvar as regras</button></div>
    <div class="fu-cx"><h3>O ensaio<small>últimos {{ ensaio_dias }} dias</small></h3>
      <div class="fu-res">
        <div><span>Primeira resposta: cartões que teria levado para Em conversa</span><b>{{ e.ensaio.resposta }}</b></div>
        <div><span>Prazo de Em conversa: avisos que teria dado</span><b>{{ e.ensaio.prazo }}</b></div>
      </div>
      <div class="fu-nota">Quando os números fizerem sentido, passe a regra de ensaio para ligado.</div>
    </div>
  </div>
  </form>
  {% endif %}
</div>
{% endblock %}"""

_env.loader.mapping["clinica_funil.html"] = _TPL
