"""Tela ADM do banco de precos (o ouro): precos coletados + lojas por CNPJ.

Arquivo INDEPENDENTE: tem seu proprio router. Pra plugar, basta no web/app.py:
    from web.admin_precos import router as precos_router
    app.include_router(precos_router)

Reusa _admin (autorizacao) e brl (formatacao) do modulo admin/portal ja' existentes.
Conteudo desta tela e' dominio do banco de precos - nao deve ser reescrito ad-hoc.
"""
from types import SimpleNamespace

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, DictLoader, select_autoescape

from db.conexao import get_pool
from web.portal import brl
from web.admin import _admin, _NEGADO, _ADMIN_BASE

router = APIRouter()

_PRECOS_TPL = """{% extends "abase" %}{% block conteudo %}
<h1>Banco de preços (o ouro)</h1>
<div class="cards">
<div class="metric"><span>Preços coletados</span><b>{{ resumo.total }}</b></div>
<div class="metric"><span>Produtos únicos</span><b style="color:#5dcaa5">{{ resumo.produtos }}</b></div>
<div class="metric"><span>Lojas</span><b>{{ resumo.lojas }}</b></div>
<div class="metric"><span>Via cupom</span><b>{{ resumo.cupom }}</b></div>
</div>

<div class="card"><h2>Últimos preços coletados</h2>
<table><tr><th>Quando</th><th>Produto</th><th>Preço unit.</th><th>Loja</th><th>Endereço</th><th>Cidade/UF</th><th>Fonte</th></tr>
{% for r in precos %}<tr>
<td class="mut">{{ r.criado_em.strftime('%d/%m %H:%M') }}</td>
<td>{{ r.descricao_original }}</td>
<td><b>{{ brl(r.valor_unitario_centavos) }}</b></td>
<td>{{ r.loja_nome or r.mercado or '-' }}</td>
<td class="mut" style="font-size:.78rem">{{ r.endereco or '-' }}</td>
<td class="mut">{{ r.cidade or '-' }}{% if r.uf %}/{{ r.uf }}{% endif %}</td>
<td class="mut">{{ r.fonte }}</td>
</tr>{% endfor %}
</table>
{% if not precos %}<p class="mut">Nenhum preço coletado ainda. Mande um cupom de mercado pra começar a alimentar o banco.</p>{% endif %}
</div>

{% if lojas %}<div class="card"><h2>Lojas cadastradas</h2>
<table><tr><th>Loja</th><th>CNPJ</th><th>Endereço</th><th>Cidade/UF</th><th>Preços</th></tr>
{% for l in lojas %}<tr>
<td>{{ l.nome or '-' }}</td>
<td class="mut" style="font-size:.78rem">{{ l.cnpj }}</td>
<td class="mut" style="font-size:.78rem">{{ l.endereco or '-' }}</td>
<td class="mut">{{ l.cidade or '-' }}{% if l.uf %}/{{ l.uf }}{% endif %}</td>
<td>{{ l.n_precos }}</td>
</tr>{% endfor %}
</table></div>{% endif %}
{% endblock %}"""

_env = Environment(
    loader=DictLoader({"abase": _ADMIN_BASE, "aprecos": _PRECOS_TPL}),
    autoescape=select_autoescape())
_env.globals["brl"] = brl


@router.get("/admin/precos", response_class=HTMLResponse)
def admin_precos(request: Request):
    if _admin(request) is None:
        return _NEGADO
    pool = get_pool()
    with pool.connection() as c:
        total = c.execute("select count(*) from precos_observados").fetchone()[0]
        produtos = c.execute(
            "select count(distinct descricao_norm) from precos_observados").fetchone()[0]
        n_lojas = c.execute("select count(*) from lojas").fetchone()[0]
        cupom = c.execute(
            "select count(*) from precos_observados where fonte='cupom'").fetchone()[0]
        pcols = ["criado_em", "descricao_original", "valor_unitario_centavos",
                 "mercado", "fonte", "loja_nome", "endereco", "cidade", "uf"]
        precos = [dict(zip(pcols, r)) for r in c.execute(
            """select po.criado_em, po.descricao_original, po.valor_unitario_centavos,
                      po.mercado, po.fonte, l.nome, l.endereco, l.cidade, l.uf
               from precos_observados po left join lojas l on l.id = po.loja_id
               order by po.id desc limit 200""").fetchall()]
        lcols = ["nome", "cnpj", "endereco", "cidade", "uf", "n_precos"]
        lojas = [dict(zip(lcols, r)) for r in c.execute(
            """select l.nome, l.cnpj, l.endereco, l.cidade, l.uf, count(po.id)
               from lojas l left join precos_observados po on po.loja_id = l.id
               group by l.id order by count(po.id) desc limit 100""").fetchall()]
    resumo = {"total": total, "produtos": produtos, "lojas": n_lojas, "cupom": cupom}
    precos = [SimpleNamespace(**x) for x in precos]
    lojas = [SimpleNamespace(**x) for x in lojas]
    return HTMLResponse(_env.get_template("aprecos").render(
        resumo=resumo, precos=precos, lojas=lojas))
