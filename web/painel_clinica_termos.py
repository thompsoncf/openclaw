"""Os termos da clínica: uso de dados, uso de imagem e um termo por procedimento.

/painel/clinica/termos           a lista e o formulário (dono e gestor)
/painel/clinica/termos/salvar    cria ou edita (editar sobe a versão)
/painel/clinica/termos/{id}/tirar tira o modelo (dados e imagem voltam pro padrão do Zaq)

finance/clinica_termos.py. O paciente aceita no passo 3 do link da ficha; cada aceite
guarda o texto exato que valia e tem a cópia em PDF na ficha.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import clinica_config as cc
from finance import clinica_termos as ct
from web.painel_clinica_agenda import _acesso, _int
from web.portal import _env, _render

router = APIRouter()
URL = "/painel/clinica/termos"


def _ir(request: Request, aviso: str = "", erro: str = "") -> RedirectResponse:
    if erro:
        request.session["termos_erro"] = erro
    return RedirectResponse(URL + ("?aviso=" + aviso if aviso else ""), status_code=303)


def _gerencia(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return None, redir
    if not gerencia:
        return None, RedirectResponse("/painel/clinica/agenda", status_code=303)
    return conta, None


@router.get(URL, response_class=HTMLResponse)
def termos(request: Request):
    conta, redir = _gerencia(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        mods = ct.modelos(c, conta[0])
        # o termo de um atendimento desativado continua editável (senão "Editar" caía em outro)
        tipos = cc.listar_tipos(c, conta[0], so_ativos=False)
    # o ponto de partida tem {clinica} e {paciente}: vale pro adulto e pro menor
    padrao = {k: {"titulo": t, "texto": x} for k, (t, x) in ct.MODELO_INICIAL.items()}
    da_clinica = {m["chave"]: m for m in mods if m["chave"] != "procedimento"}
    editar = _int(request.query_params.get("editar"))
    return _render("clinica_termos.html", request, titulo="Termos", secao_ativa="agenda",
                   aviso={"salvo": "Termo salvo.", "tirado": "Termo tirado."}.get(request.query_params.get("aviso") or "", ""),
                   erro=request.session.pop("termos_erro", ""), padrao=padrao, da_clinica=da_clinica,
                   procedimentos=[m for m in mods if m["chave"] == "procedimento"], tipos=tipos,
                   editar=next((m for m in mods if m["id"] == editar and m["chave"] == "procedimento"), None))


@router.post(URL + "/salvar")
def salvar(request: Request, chave: str = Form(""), titulo: str = Form(""), texto: str = Form(""),
           servico_id: str = Form("")):
    conta, redir = _gerencia(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        erro = ct.salvar_modelo(c, conta[0], chave, titulo, texto, servico_id=_int(servico_id),
                                membro_id=request.session.get("membro_id"))
        (c.rollback if erro else c.commit)()
    return _ir(request, "" if erro else "salvo", erro or "")


@router.post(URL + "/{modelo_id}/tirar")
def tirar(request: Request, modelo_id: int):
    conta, redir = _gerencia(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        ct.desativar(c, conta[0], modelo_id)
        c.commit()
    return _ir(request, "tirado")


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.tm-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.tm-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin:.8rem 0}
.tm-cx h3{margin:0 0 .3rem;font-size:1.02rem}.tm-m{font-size:.84rem;color:var(--txt-mut)}
.tm-txt{white-space:pre-wrap;font-size:.86rem;background:var(--card-2,rgba(255,255,255,.03));border:1px solid var(--borda);border-radius:8px;padding:.5rem .6rem;margin:.4rem 0;max-height:12rem;overflow:auto}
.tm-cx textarea{min-height:10rem}
.tm-acoes{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.5rem}.tm-acoes form{margin:0}
.tm-acoes button,.tm-acoes a{width:auto;margin:0;min-height:38px;padding:.3rem .8rem;font-size:.86rem;border-radius:8px}
.tm-acoes .sec,.tm-acoes a{background:transparent;border:1px solid var(--borda);color:var(--txt);text-decoration:none;display:inline-flex;align-items:center}
</style>
<div class="tm-pag">
  <h2 style="margin:0">Termos da clínica</h2>
  <div class="tm-m" style="margin-top:.2rem">O que o paciente aceita no link da ficha (passo 3). Use {clinica} e {paciente} no texto. Editar um termo cria uma versão nova: quem já aceitou fica com o texto que leu, e a cópia em PDF sai na ficha dele. Confira os textos com o jurídico da clínica.</div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}

  {% for chave in ('lgpd', 'imagem') %}{% set m = da_clinica.get(chave) %}
  <div class="tm-cx"><form method="post" action="/painel/clinica/termos/salvar" style="margin:0">
    <input type="hidden" name="chave" value="{{ chave }}">
    <h3>{{ 'Uso de dados (LGPD)' if chave == 'lgpd' else 'Uso de imagem' }} <span class="tm-m">· {% if m %}texto da clínica, versão {{ m.versao }}{% else %}texto padrão do Zaq{% endif %}</span></h3>
    <label>Título<input name="titulo" maxlength="120" value="{{ m.titulo if m else padrao[chave].titulo }}"></label>
    <label>Texto<textarea name="texto" maxlength="8000">{{ m.texto if m else padrao[chave].texto }}</textarea></label>
    {% if chave == 'imagem' %}<div class="tm-m">Depois do texto, o paciente escolhe: só para o tratamento, também para divulgação, ou não autorizo.</div>{% endif %}
    <div class="tm-acoes"><button>Salvar</button></div></form>
    {% if m %}<div class="tm-acoes"><form method="post" action="/painel/clinica/termos/{{ m.id }}/tirar" onsubmit="return confirm('Voltar ao texto padrão do Zaq?')"><button class="sec">Voltar ao padrão do Zaq</button></form></div>{% endif %}</div>
  {% endfor %}

  <div class="tm-cx"><h3>Termos por procedimento</h3>
    <div class="tm-m">Um termo para cada atendimento que pede consentimento (peeling, laser, botox…). Quando o próximo agendamento do paciente é desse atendimento, o termo aparece no link da ficha e conta para a ficha completa.</div>
    {% for m in procedimentos %}<div style="margin-top:.7rem"><b>{{ m.servico }}</b> · {{ m.titulo }} <span class="tm-m">· versão {{ m.versao }}, {{ m.quando.strftime('%d/%m/%Y') }}</span>
      <div class="tm-acoes"><a href="/painel/clinica/termos?editar={{ m.id }}#novo">Editar</a>
        <form method="post" action="/painel/clinica/termos/{{ m.id }}/tirar" onsubmit="return confirm('Tirar este termo?')"><button class="sec">Tirar</button></form></div></div>
    {% else %}<div class="tm-m" style="margin-top:.5rem">Nenhum ainda.</div>{% endfor %}
  </div>
  <form class="tm-cx" id="novo" method="post" action="/painel/clinica/termos/salvar">
    <input type="hidden" name="chave" value="procedimento">
    <h3>{% if editar %}Editar o termo de {{ editar.servico }}{% else %}Novo termo de procedimento{% endif %}</h3>
    <label>Atendimento<select name="servico_id">{% for t in tipos %}<option value="{{ t.id }}" {% if editar and editar.servico_id == t.id %}selected{% endif %}>{{ t.nome }}{% if not t.ativo %} (desativado){% endif %}</option>{% endfor %}</select></label>
    <label>Título<input name="titulo" maxlength="120" value="{{ editar.titulo if editar else '' }}" placeholder="Termo de consentimento: peeling químico"></label>
    <label>Texto<textarea name="texto" maxlength="8000" placeholder="Declaro que fui informado(a) sobre o procedimento, seus cuidados, riscos e alternativas…">{{ editar.texto if editar else '' }}</textarea></label>
    <div class="tm-acoes"><button>Salvar</button>{% if editar %}<a href="/painel/clinica/termos">Cancelar</a>{% endif %}</div>
  </form>
</div>
{% endblock %}"""

_env.loader.mapping["clinica_termos.html"] = _TPL
