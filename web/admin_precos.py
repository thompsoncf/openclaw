"""Tela ADM do banco de precos (o ouro), pensada pra ESCALA (10k-100k+ precos).

- Tabela AGREGADA POR PRODUTO (1 linha/produto: menor preco, loja mais barata,
  nÂº de lojas), nao lista crua - escala muito melhor.
- Filtros SEM REFRESH (AJAX): seletor de departamento (ramo) + busca por produto.
- Paginacao no servidor (so' devolve a pagina pedida).

Arquivo INDEPENDENTE (router proprio). Plugar no web/app.py:
    from web.admin_precos import router as precos_router
    app.include_router(precos_router)

Conteudo desta tela e' dominio do banco de precos - subir VERBATIM, nao reescrever.
"""
from types import SimpleNamespace

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, DictLoader, select_autoescape

from db.conexao import get_pool
from web.portal import brl
from web.admin import _admin, _NEGADO, _ADMIN_BASE

router = APIRouter()

_POR_PAGINA = 50


def _usar_vigentes(c) -> bool:
    """True quando NAO ha observacoes cruas (ex: so' restaram os vigentes agregados).
    Nesse caso, todas as leituras do painel passam a usar precos_vigentes."""
    return c.execute("select count(*) from precos_observados").fetchone()[0] == 0


def _resumo(c):
    n_lojas = c.execute("select count(*) from lojas").fetchone()[0]
    if _usar_vigentes(c):
        total = c.execute("select count(*) from precos_vigentes").fetchone()[0]
        produtos = c.execute(
            "select count(distinct descricao_norm) from precos_vigentes").fetchone()[0]
        # 'via cupom' nao existe em vigentes (agregado); usamos n_observacoes total
        cupom = c.execute(
            "select coalesce(sum(n_observacoes),0) from precos_vigentes").fetchone()[0]
        return {"total": total, "produtos": produtos, "lojas": n_lojas, "cupom": cupom}
    total = c.execute("select count(*) from precos_observados").fetchone()[0]
    produtos = c.execute(
        "select count(distinct descricao_norm) from precos_observados").fetchone()[0]
    cupom = c.execute(
        "select count(*) from precos_observados where fonte='cupom'").fetchone()[0]
    return {"total": total, "produtos": produtos, "lojas": n_lojas, "cupom": cupom}


def _ramos(c):
    """Lista os ramos (departamentos) que existem, pra montar o seletor."""
    if _usar_vigentes(c):
        rows = c.execute(
            """select l.ramo, count(distinct v.descricao_norm)
               from precos_vigentes v join lojas l on l.id = v.loja_id
               where l.ramo is not null
               group by l.ramo order by l.ramo""").fetchall()
        return [(r[0], r[1]) for r in rows]
    rows = c.execute(
        """select l.ramo, count(distinct po.descricao_norm)
           from precos_observados po join lojas l on l.id = po.loja_id
           where l.ramo is not null
           group by l.ramo order by l.ramo""").fetchall()
    return [(r[0], r[1]) for r in rows]


def _agregado_vigentes(c, ramo=None, busca=None, pagina=1):
    """Versao que le' de precos_vigentes (quando observados esta vazia).
    Cada linha de vigentes ja' e' 1 produto/loja/regiao com mediana e n_observacoes.
    Agregamos por descricao_norm: menor mediana, nÂº de lojas, visto mais recente."""
    where = ["v.valor_mediana_centavos > 0"]
    args = []
    if ramo:
        where.append("l.ramo = %s")
        args.append(ramo)
    if busca:
        where.append("v.descricao_norm like %s")
        args.append(f"%{busca.lower().strip()}%")
    w = " and ".join(where)
    total = c.execute(
        f"""select count(*) from (
              select v.descricao_norm from precos_vigentes v
              left join lojas l on l.id = v.loja_id
              where {w} group by v.descricao_norm) t""", args).fetchone()[0]
    offset = max(0, (pagina - 1) * _POR_PAGINA)
    sql = f"""
    with agg as (
      select v.descricao_norm,
             max(v.descricao_exemplo) as nome,
             min(v.valor_mediana_centavos) as menor,
             max(v.valor_mediana_centavos) as maior,
             count(distinct v.loja_id) as n_lojas,
             coalesce(sum(v.n_observacoes),0) as n_precos,
             max(v.data_mais_recente) as visto
      from precos_vigentes v
      left join lojas l on l.id = v.loja_id
      where {w}
      group by v.descricao_norm
    )
    select a.descricao_norm, a.nome, a.menor, a.maior, a.n_lojas, a.n_precos, a.visto,
      (select coalesce(l2.nome, v2.mercado) from precos_vigentes v2
       left join lojas l2 on l2.id = v2.loja_id
       where v2.descricao_norm = a.descricao_norm
         and v2.valor_mediana_centavos = a.menor
       order by v2.data_mais_recente desc limit 1) as loja_barata
    from agg a
    order by a.nome
    limit {_POR_PAGINA} offset {offset}
    """
    rows = c.execute(sql, args).fetchall()
    itens = [{
        "nome": r[1], "menor": r[2], "maior": r[3], "n_lojas": r[4],
        "n_precos": r[5], "visto": r[6].strftime("%d/%m") if r[6] else "",
        "loja": r[7] or "-",
        "menor_brl": brl(r[2]), "maior_brl": brl(r[3]),
        "varia": r[2] != r[3],
    } for r in rows]
    paginas = max(1, (total + _POR_PAGINA - 1) // _POR_PAGINA)
    return {"itens": itens, "total": total, "pagina": pagina, "paginas": paginas}


def _agregado(c, ramo=None, busca=None, pagina=1):
    """Agrega por produto: menor preco, loja mais barata, nÂº lojas. Paginado.

    Se NAO ha observacoes cruas, le' de precos_vigentes (o agregado consolidado).
    """
    if _usar_vigentes(c):
        return _agregado_vigentes(c, ramo, busca, pagina)
    where = ["po.valor_unitario_centavos > 0"]
    args = []
    if ramo:
        where.append("l.ramo = %s")
        args.append(ramo)
    if busca:
        where.append("po.descricao_norm like %s")
        args.append(f"%{busca.lower().strip()}%")
    w = " and ".join(where)
    total = c.execute(
        f"""select count(*) from (
              select po.descricao_norm from precos_observados po
              left join lojas l on l.id = po.loja_id
              where {w} group by po.descricao_norm) t""", args).fetchone()[0]
    offset = max(0, (pagina - 1) * _POR_PAGINA)
    sql = f"""
    with agg as (
      select po.descricao_norm,
             max(po.descricao_original) as nome,
             min(po.valor_unitario_centavos) as menor,
             max(po.valor_unitario_centavos) as maior,
             count(distinct po.loja_id) as n_lojas,
             count(*) as n_precos,
             max(po.data_compra) as visto
      from precos_observados po
      left join lojas l on l.id = po.loja_id
      where {w}
      group by po.descricao_norm
    )
    select a.descricao_norm, a.nome, a.menor, a.maior, a.n_lojas, a.n_precos, a.visto,
      (select coalesce(l2.nome, p2.mercado) from precos_observados p2
       left join lojas l2 on l2.id = p2.loja_id
       where p2.descricao_norm = a.descricao_norm
         and p2.valor_unitario_centavos = a.menor
       order by p2.data_compra desc limit 1) as loja_barata
    from agg a
    order by a.nome
    limit {_POR_PAGINA} offset {offset}
    """
    rows = c.execute(sql, args).fetchall()
    itens = [{
        "nome": r[1], "menor": r[2], "maior": r[3], "n_lojas": r[4],
        "n_precos": r[5], "visto": r[6].strftime("%d/%m") if r[6] else "",
        "loja": r[7] or "-",
        "menor_brl": brl(r[2]), "maior_brl": brl(r[3]),
        "varia": r[2] != r[3],
    } for r in rows]
    paginas = max(1, (total + _POR_PAGINA - 1) // _POR_PAGINA)
    return {"itens": itens, "total": total, "pagina": pagina, "paginas": paginas}


@router.get("/admin/precos", response_class=HTMLResponse)
def admin_precos(request: Request):
    if _admin(request) is None:
        return _NEGADO
    with get_pool().connection() as c:
        resumo = _resumo(c)
        ramos = _ramos(c)
        lcols = ["nome", "cnpj", "endereco", "cidade", "uf", "ramo", "n_precos"]
        lojas = [SimpleNamespace(**dict(zip(lcols, r))) for r in c.execute(
            """select l.nome, l.cnpj, l.endereco, l.cidade, l.uf, l.ramo, count(po.id)
               from lojas l left join precos_observados po on po.loja_id = l.id
               group by l.id order by count(po.id) desc limit 100""").fetchall()]
    return HTMLResponse(_env.get_template("aprecos").render(
        resumo=resumo, ramos=ramos, lojas=lojas))


@router.get("/admin/precos/dados")
def admin_precos_dados(request: Request, ramo: str = "", busca: str = "", pagina: int = 1):
    """Endpoint JSON pros filtros AJAX (departamento + busca + paginacao)."""
    if _admin(request) is None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    with get_pool().connection() as c:
        dados = _agregado(c, ramo=ramo or None, busca=busca or None, pagina=max(1, pagina))
    return JSONResponse(dados)


_PRECOS_TPL = """{% extends "abase" %}{% block conteudo %}
<h1>Banco de preços (o ouro)</h1>
<div class="cards">
<div class="metric"><span>Preços coletados</span><b>{{ resumo.total }}</b></div>
<div class="metric"><span>Produtos únicos</span><b style="color:var(--verde-claro)">{{ resumo.produtos }}</b></div>
<div class="metric"><span>Lojas</span><b>{{ resumo.lojas }}</b></div>
<div class="metric"><span>Via cupom</span><b>{{ resumo.cupom }}</b></div>
</div>

<div class="card">
<div style="display:flex; gap:.6rem; flex-wrap:wrap; align-items:center; margin-bottom:.8rem">
  <select id="framo" style="padding:.5rem; border-radius:8px; background:#0e1525; color:#e7ecf3; border:1px solid #243049">
    <option value="">Todos os departamentos</option>
    {% for nome, n in ramos %}<option value="{{ nome }}">{{ nome }} ({{ n }})</option>{% endfor %}
  </select>
  <input id="fbusca" type="text" placeholder="Buscar produto..." autocomplete="off"
    style="flex:1; min-width:180px; padding:.5rem; border-radius:8px; background:#0e1525; color:#e7ecf3; border:1px solid #243049">
  <span id="fcount" class="mut" style="font-size:.85rem"></span>
</div>
<table>
<thead><tr><th>Produto</th><th>Menor preço</th><th>Onde</th><th>Faixa</th><th>Lojas</th><th>Visto</th></tr></thead>
<tbody id="tbody"><tr><td colspan="6" class="mut">Carregando...</td></tr></tbody>
</table>
<div id="paginacao" style="display:flex; gap:.5rem; align-items:center; margin-top:.8rem"></div>
</div>

{% if lojas %}<div class="card"><h2>Lojas cadastradas</h2>
<table><tr><th>Loja</th><th>Ramo</th><th>CNPJ</th><th>Endereço</th><th>Cidade/UF</th><th>Preços</th></tr>
{% for l in lojas %}<tr>
<td>{{ l.nome or '-' }}</td>
<td>{% if l.ramo %}<span class="tag ativa">{{ l.ramo }}</span>{% else %}<span class="mut">-</span>{% endif %}</td>
<td class="mut" style="font-size:.78rem">{{ l.cnpj }}</td>
<td class="mut" style="font-size:.78rem">{{ l.endereco or '-' }}</td>
<td class="mut">{{ l.cidade or '-' }}{% if l.uf %}/{{ l.uf }}{% endif %}</td>
<td>{{ l.n_precos }}</td>
</tr>{% endfor %}
</table></div>{% endif %}

<script>
(function(){
  var ramo = document.getElementById('framo');
  var busca = document.getElementById('fbusca');
  var tbody = document.getElementById('tbody');
  var pag = document.getElementById('paginacao');
  var count = document.getElementById('fcount');
  var pagina = 1, timer = null;

  function carregar(){
    var url = '/admin/precos/dados?pagina=' + pagina
      + '&ramo=' + encodeURIComponent(ramo.value)
      + '&busca=' + encodeURIComponent(busca.value);
    tbody.innerHTML = '<tr><td colspan="6" class="mut">Carregando...</td></tr>';
    fetch(url).then(function(r){ return r.json(); }).then(function(d){
      if(!d.itens || d.itens.length === 0){
        tbody.innerHTML = '<tr><td colspan="6" class="mut">Nenhum produto encontrado.</td></tr>';
        pag.innerHTML = ''; count.textContent = '';
        return;
      }
      var html = '';
      d.itens.forEach(function(it){
        var faixa = it.varia ? (it.menor_brl + ' &ndash; ' + it.maior_brl) : '&mdash;';
        html += '<tr>'
          + '<td>' + esc(it.nome) + '</td>'
          + '<td><b>' + it.menor_brl + '</b></td>'
          + '<td>' + esc(it.loja) + '</td>'
          + '<td class="mut" style="font-size:.82rem">' + faixa + '</td>'
          + '<td>' + it.n_lojas + '</td>'
          + '<td class="mut">' + it.visto + '</td>'
          + '</tr>';
      });
      tbody.innerHTML = html;
      count.textContent = d.total + ' produto' + (d.total === 1 ? '' : 's');
      var p = '';
      if(d.paginas > 1){
        p += botao('&laquo; Anterior', pagina > 1, pagina - 1);
        p += '<span class="mut" style="font-size:.85rem">Página ' + d.pagina + ' de ' + d.paginas + '</span>';
        p += botao('Próxima &raquo;', pagina < d.paginas, pagina + 1);
      }
      pag.innerHTML = p;
    }).catch(function(){
      tbody.innerHTML = '<tr><td colspan="6" class="mut">Erro ao carregar.</td></tr>';
    });
  }
  function botao(txt, ativo, destino){
    if(!ativo) return '<button disabled style="opacity:.4; padding:.35rem .7rem; border-radius:8px; background:#1a2233; color:#8b97a8; border:1px solid #243049">' + txt + '</button>';
    return '<button onclick="__irPagina(' + destino + ')" style="padding:.35rem .7rem; border-radius:8px; background:#1a2233; color:#e7ecf3; border:1px solid #243049; cursor:pointer">' + txt + '</button>';
  }
  function esc(s){ var d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
  window.__irPagina = function(p){ pagina = p; carregar(); };

  ramo.addEventListener('change', function(){ pagina = 1; carregar(); });
  busca.addEventListener('input', function(){
    clearTimeout(timer);
    timer = setTimeout(function(){ pagina = 1; carregar(); }, 250);
  });
  carregar();
})();
</script>
{% endblock %}"""

_env = Environment(
    loader=DictLoader({"abase": _ADMIN_BASE, "aprecos": _PRECOS_TPL}),
    autoescape=select_autoescape())
_env.globals["brl"] = brl
