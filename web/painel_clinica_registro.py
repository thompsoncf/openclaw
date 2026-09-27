"""O registro de acesso ao prontuário (mockup docs/mockups/clinica_prontuario.html, 11.8).

/painel/clinica/registro   quem abriu o conteúdo clínico de qual paciente, quando e o
                           quê — nunca o conteúdo. Dono e gestor veem tudo; o
                           profissional de saúde, o dos pacientes que ele atendeu.
                           Filtros: paciente (?paciente=id) e pessoa (?quem=id do
                           profissional) — ids, nunca nome na URL.

finance/clinica_acesso_clinico.py (migração 419).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import clinica_acesso_clinico as acc
from finance import clinica_config as cc
from web.painel_clinica_agenda import _acesso, _int
from web.portal import _env, _render

router = APIRouter()
URL = "/painel/clinica/registro"


@router.get(URL, response_class=HTMLResponse)
def registro(request: Request):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    q = request.query_params
    agora = datetime.now(timezone.utc)
    paciente, quem = _int(q.get("paciente")), _int(q.get("quem"))
    dias = 30 if q.get("dias") == "30" else 7
    with get_pool().connection() as c:
        tudo, leitor = acc.pode_ver_registro(c, conta[0], request.session)
        if not tudo and not leitor:
            return RedirectResponse("/painel/clinica/agenda", status_code=303)
        linhas = acc.registro(c, conta[0], agora, dias=dias, cliente_id=paciente, profissional_id=quem,
                              so_pacientes_de=None if tudo else leitor["profissional_id"])
        profs = [p for p in cc.listar_profissionais(c, conta[0], so_ativos=False)
                 if p["funcao"] != "Recepção, não atende"]
    nome_paciente = next((x["paciente"] for x in linhas if x["cliente_id"] == paciente), "") if paciente else ""
    return _render("clinica_registro.html", request, titulo="Registro de acesso", secao_ativa="agenda",
                   linhas=linhas, tudo=tudo, profs=profs, paciente=paciente, nome_paciente=nome_paciente,
                   quem=quem, dias=dias)


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.rg-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.rg-m{font-size:.84rem;color:var(--txt-mut)}
.rg-t{width:100%;border-collapse:collapse;font-size:.88rem;margin-top:.7rem}.rg-t th,.rg-t td{padding:.45rem .35rem;border-bottom:1px solid var(--borda);text-align:left}
.rg-f{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center;margin-top:.7rem}.rg-f select{width:auto;margin:0}
</style>
<div class="rg-pag">
  <h2 style="margin:0">Registro de acesso ao prontuário</h2>
  <div class="rg-m" style="margin-top:.2rem">Quem abriu o conteúdo clínico, de qual paciente, quando e o quê. O conteúdo nunca aparece aqui. Nada neste registro se altera ou se apaga.{% if not tudo %} Você vê o registro dos pacientes que atendeu.{% endif %}</div>
  <form class="rg-f" method="get" action="/painel/clinica/registro">
    {% if paciente %}<input type="hidden" name="paciente" value="{{ paciente }}"><span class="rg-m">Paciente: <b>{{ nome_paciente or ('ficha ' ~ paciente) }}</b> · <a href="/painel/clinica/registro">todos</a></span>{% endif %}
    <select name="quem" onchange="this.form.submit()"><option value="">Todas as pessoas</option>{% for p in profs %}<option value="{{ p.id }}" {% if quem == p.id %}selected{% endif %}>{{ p.nome }}</option>{% endfor %}</select>
    <select name="dias" onchange="this.form.submit()"><option value="7" {% if dias == 7 %}selected{% endif %}>Últimos 7 dias</option><option value="30" {% if dias == 30 %}selected{% endif %}>Últimos 30 dias</option></select>
  </form>
  {% if linhas %}
  <table class="rg-t"><tr><th>Quando</th><th>Quem</th><th>O quê</th><th>Paciente</th></tr>
  {% for x in linhas %}<tr><td>{{ x.quando.strftime('%d/%m %H:%M') }}</td><td>{{ x.quem }}</td><td>{{ x.o_que }}</td>
    <td>{% if x.cliente_id %}<a href="/painel/clinica/pacientes/{{ x.cliente_id }}">{{ x.paciente or 'ficha' }}</a>{% else %}—{% endif %}</td></tr>{% endfor %}
  </table>
  {% else %}<div class="rg-m" style="margin-top:.8rem">Nenhum acesso no período.</div>{% endif %}
</div>
{% endblock %}"""

_env.loader.mapping["clinica_registro.html"] = _TPL
