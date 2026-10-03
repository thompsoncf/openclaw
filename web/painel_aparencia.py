"""A tela APARÊNCIA: /painel/aparencia (docs/mockups/zaq_temas.html, aprovado em 03/10/2026).

Escolhe o tema do painel: escuro, claro, misto (menu escuro, trabalho claro) ou
automático (segue o aparelho). Duas escolhas na mesma tela:

- o tema da EMPRESA, que vale pra todo mundo que não escolheu o seu. Só dono e
  gestor mudam; os outros veem qual é.
- o tema SÓ PRA MIM, de quem é da equipe (tem `membro_id`). O dono não tem essa
  parte: pra ele, o tema da empresa já é o dele.

Fase 1: só existe pra conta piloto (`contas.temas_piloto`). Pra qualquer outra a
rota manda de volta pro painel, e o item nem aparece no menu.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from contas import aparencia as ap
from db.conexao import get_pool
from web.portal import _env, _render, conta_logada

router = APIRouter()

# A miniatura de cada tema: (menu, página, linhas). São as cores dos próprios
# temas (web/tema.py), desenhadas pequenas; não seguem o tema de quem olha.
_MINIS = {
    "escuro": ("#0E1512", "#0A0F0C", "#1E2A23"),
    "claro": ("#E9EFEB", "#F3F6F4", "#D7E0DA"),
    "misto": ("#0E1512", "#F3F6F4", "#D7E0DA"),
    "auto": None,
}


def _entrar(request: Request):
    """(conta, None) ou (None, redirect)."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if not ap.eh_piloto(get_pool(), conta[0]):
        return None, RedirectResponse("/painel", status_code=303)
    return conta, None


def _volta(ok: str = "", erro: str = "") -> RedirectResponse:
    q = f"?ok={quote(ok)}" if ok else (f"?erro={quote(erro)}" if erro else "")
    return RedirectResponse("/painel/aparencia" + q, status_code=303)


@router.get("/painel/aparencia", response_class=HTMLResponse)
def painel_aparencia(request: Request, ok: str = "", erro: str = ""):
    conta, fora = _entrar(request)
    if fora is not None:
        return fora
    papel = request.session.get("papel") or "dono"
    membro_id = request.session.get("membro_id")
    atual = ap.ler(get_pool(), conta[0], membro_id)
    opcoes = [{"v": t, "nome": ap.ROTULOS[t][0], "dica": ap.ROTULOS[t][1], "mini": _MINIS[t]}
              for t in ap.ROTULOS]
    return _render("aparencia", request, titulo="Aparência", tem_pj=True,
                   secao_ativa="aparencia", atual=atual, opcoes=opcoes,
                   muda_empresa=papel in ap.PAPEIS_DA_EMPRESA,
                   tem_meu=bool(membro_id), rotulos=ap.ROTULOS, ok=ok, erro=erro)


@router.post("/painel/aparencia/empresa")
def painel_aparencia_empresa(request: Request, tema: str = Form("")):
    conta, fora = _entrar(request)
    if fora is not None:
        return fora
    if (request.session.get("papel") or "dono") not in ap.PAPEIS_DA_EMPRESA:
        return _volta(erro="Só o dono e quem gerencia mudam o tema da empresa.")
    if not ap.salvar_empresa(get_pool(), conta[0], tema):
        return _volta(erro="Escolha um dos quatro temas.")
    return _volta(ok=f"Tema da empresa: {ap.ROTULOS[ap.normalizar(tema)][0]}.")


@router.post("/painel/aparencia/meu")
def painel_aparencia_meu(request: Request, tema: str = Form("")):
    conta, fora = _entrar(request)
    if fora is not None:
        return fora
    membro_id = request.session.get("membro_id")
    if not membro_id:
        return _volta(erro="Pra você, o tema da empresa já é o seu.")
    escolha = "" if tema == "seguir" else tema
    if not ap.salvar_meu(get_pool(), conta[0], membro_id, escolha):
        return _volta(erro="Escolha um dos quatro temas.")
    if not escolha:
        return _volta(ok="Pronto: você segue o tema da empresa.")
    return _volta(ok=f"Seu tema: {ap.ROTULOS[ap.normalizar(escolha)][0]}.")


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.ap{max-width:760px;display:grid;gap:1.4rem}
.ap-topo p{color:var(--txt-mut);font-size:.92rem;line-height:1.55;margin:.3rem 0 0}
.ap-teste{background:var(--ambar-fundo);border:1px solid var(--ambar-borda);border-radius:10px;
  padding:.65rem .85rem;font-size:.86rem;line-height:1.5;color:var(--txt)}
.ap-teste b{color:var(--ambar)}
.ap-msg{border-radius:10px;padding:.55rem .8rem;font-size:.86rem}
.ap-msg.ap-ok{background:var(--neon-fundo);border:1px solid var(--neon-borda);color:var(--txt)}
.ap-msg.ap-err{background:var(--coral-fundo);border:1px solid var(--coral-borda);color:var(--coral)}
.ap-bloco{background:var(--card);border:1px solid var(--borda);border-radius:14px;padding:1rem 1.1rem}
.ap-bloco h3{margin:0;font-size:1.02rem}
.ap-bloco>p{margin:.25rem 0 0;color:var(--txt-mut);font-size:.86rem;line-height:1.5}
.ap-opcs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.7rem;margin:.9rem 0 0;border:0;padding:0}
@media (max-width:640px){.ap-opcs{grid-template-columns:repeat(2,minmax(0,1fr))}}
.ap-opc{position:relative;display:flex;flex-direction:column;gap:.45rem;border:1.5px solid var(--borda);
  border-radius:12px;padding:.5rem;cursor:pointer;margin:0}
.ap-opc input{position:absolute;opacity:0;width:1px;height:1px;min-height:0;margin:0}
.ap-opc:has(input:checked){border-color:var(--verde);box-shadow:0 0 0 1px var(--verde)}
.ap-opc:has(input:focus-visible){outline:2px solid var(--verde);outline-offset:2px}
.ap-opc b{font-size:.9rem;color:var(--txt)}
.ap-opc small{font-size:.76rem;color:var(--txt-mut);line-height:1.35}
.ap-mini{height:58px;border-radius:7px;overflow:hidden;display:grid;grid-template-columns:28% 1fr;
  border:1px solid var(--borda)}
.ap-mini .mc{padding:7px;display:grid;gap:5px;align-content:start}
.ap-mini .mc i{display:block;height:7px;border-radius:3px}
.ap-mini .mc i:first-child{width:55%;background:var(--verde)}
.ap-mini.auto{background:linear-gradient(135deg,#0E1512 50%,#F3F6F4 50%)}
.ap-bt{margin-top:.9rem;width:auto;min-height:44px;padding:.55rem 1.2rem;background:var(--verde);
  color:var(--sobre-verde);border:0;border-radius:9px;font-size:.9rem;font-weight:700;cursor:pointer}
.ap-ver{display:inline-block;margin-top:.7rem;font-size:.84rem;color:var(--txt-mut)}
.ap-ver b{color:var(--txt)}
.ap-seguir{display:flex;gap:.6rem;align-items:flex-start;margin:.9rem 0 0;font-size:.9rem;color:var(--txt);cursor:pointer}
.ap-seguir input{width:auto;min-height:0;margin-top:.2rem;accent-color:var(--verde)}
.ap-seguir small{display:block;color:var(--txt-mut);font-size:.8rem}
</style>

{% macro grade(nome, marcado, com_seguir=false) -%}
<fieldset class="ap-opcs">
  <legend class="sr" style="position:absolute;left:-9999px">{{ nome }}</legend>
  {% for o in opcoes %}
  <label class="ap-opc">
    <input type="radio" name="tema" value="{{ o.v }}"{% if marcado == o.v %} checked{% endif %}>
    {% if o.mini %}<span class="ap-mini" style="background:{{ o.mini[1] }}"><span style="background:{{ o.mini[0] }}"></span><span class="mc"><i></i><i style="background:{{ o.mini[2] }}"></i><i style="background:{{ o.mini[2] }}"></i></span></span>
    {% else %}<span class="ap-mini auto"><span></span><span class="mc"><i></i></span></span>{% endif %}
    <b>{{ o.nome }}</b><small>{{ o.dica }}</small>
  </label>
  {% endfor %}
</fieldset>
{%- endmacro %}

<div class="ap">
  <div class="ap-topo">
    <h2>Aparência</h2>
    <p>Escolha as cores do painel. A empresa define o padrão, e cada pessoa da equipe pode usar o dela.</p>
  </div>

  <div class="ap-teste"><b>Em teste nesta conta.</b> O tema escuro continua igual. No claro e no misto, algumas telas ainda aparecem com partes escuras ou cores trocadas; elas vão sendo acertadas nas próximas atualizações. Se algo ficar difícil de ler, volte pro Escuro.</div>

  {% if ok %}<div class="ap-msg ap-ok">{{ ok }}</div>{% endif %}
  {% if erro %}<div class="ap-msg ap-err">{{ erro }}</div>{% endif %}

  <form class="ap-bloco" method="post" action="/painel/aparencia/empresa">
    <h3>Tema da empresa</h3>
    <p>Vale pra todos da equipe que seguem a empresa.{% if not muda_empresa %} Só o dono e quem gerencia mudam.{% endif %}</p>
    {% if muda_empresa %}
      {{ grade("Tema da empresa", atual.empresa) }}
      <button class="ap-bt" type="submit">Usar este tema na empresa</button>
    {% else %}
      <span class="ap-ver">A empresa usa: <b>{{ rotulos[atual.empresa][0] }}</b></span>
    {% endif %}
  </form>

  {% if tem_meu %}
  <form class="ap-bloco" method="post" action="/painel/aparencia/meu">
    <h3>Só pra mim</h3>
    <p>Vale em qualquer aparelho em que você entrar.</p>
    <label class="ap-seguir"><input type="radio" name="tema" value="seguir"{% if not atual.meu %} checked{% endif %}>
      <span>Seguir a empresa<small>Hoje: {{ rotulos[atual.empresa][0] }}. Se a empresa trocar, troca pra você também.</small></span></label>
    {{ grade("Meu tema", atual.meu or "") }}
    <button class="ap-bt" type="submit">Salvar o meu</button>
  </form>
  {% endif %}
</div>
{% endblock %}"""

_env.loader.mapping["aparencia"] = _TPL
