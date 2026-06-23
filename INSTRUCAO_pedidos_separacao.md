# Tarefa: sub-aba SEPARAÇÃO do módulo Pedidos (passo 2 de 4)

A camada de dados já existe (`finance/pedidos.py` foi atualizado pelo dono;
substituir verbatim). Esta tarefa é só ROTA + TEMPLATE + navegação de sub-abas.

A Separação é o **pick list** do fornecedor: "pra entregar essa semana, preciso
separar 4kg de banana, 8 alfaces, 5 maços de couve...". Só conta cestas em
status `confirmada` (rigoroso) — mostra cestas em ajuste como ALERTA separado.

Arquivo único a tocar: `web/portal.py`.

---

## EDIT 1 — Adicionar NAVEGAÇÃO de sub-abas no template `_PEDIDOS_FORN`

Achar no template (depois do `<h2 style="margin:.2rem 0 1.2rem">📋 Pedidos</h2>`)
e ANTES do `<!-- BARRA DE FILTROS -->`:

Inserir:

```html
  <!-- SUB-ABAS -->
  <div class="ped-abas">
    <a href="/painel/fornecedor/pedidos" class="ped-aba ativa">Lista</a>
    <a href="/painel/fornecedor/separacao" class="ped-aba">Separação</a>
    <span class="ped-aba off" title="em breve">Embalagem</span>
    <span class="ped-aba off" title="em breve">Rotas</span>
  </div>
```

E adicionar no `<style>` desse template (junto com os outros .ped-* existentes):

```css
.ped-abas{display:flex;gap:0;border-bottom:1px solid #2a2a2b;margin-bottom:1rem;overflow-x:auto}
.ped-aba{padding:.55rem 1rem;color:#a8a8a3;border-bottom:2px solid transparent;text-decoration:none;font-size:.92rem;white-space:nowrap}
.ped-aba:hover{color:#ececec}
.ped-aba.ativa{color:#5dcaa5;border-color:#5dcaa5;font-weight:600}
.ped-aba.off{color:#5a5a5a;font-size:.88rem;cursor:default}
```

## EDIT 2 — Adicionar template `_SEPARACAO_FORN`

Adicionar JUNTO dos outros templates (antes do dicionário `_templates`,
linha ~2091):

```python
_SEPARACAO_FORN = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <a href="/painel/fornecedor" class="ped-back">← voltar</a>
  <h2 style="margin:.2rem 0 1.2rem">📋 Pedidos</h2>

  <!-- SUB-ABAS -->
  <div class="ped-abas">
    <a href="/painel/fornecedor/pedidos" class="ped-aba">Lista</a>
    <a href="/painel/fornecedor/separacao" class="ped-aba ativa">Separação</a>
    <span class="ped-aba off" title="em breve">Embalagem</span>
    <span class="ped-aba off" title="em breve">Rotas</span>
  </div>

  <!-- FILTRO DE PERÍODO -->
  <form method="get" action="/painel/fornecedor/separacao" class="sep-filtro">
    <select name="periodo" onchange="this.form.submit()">
      <option value="esta_semana"    {% if periodo=='esta_semana' %}selected{% endif %}>Esta semana</option>
      <option value="proxima_semana" {% if periodo=='proxima_semana' %}selected{% endif %}>Próxima semana</option>
      <option value="proxima"        {% if periodo=='proxima' %}selected{% endif %}>Próximas entregas</option>
      <option value="mes"            {% if periodo=='mes' %}selected{% endif %}>Próximos 30 dias</option>
    </select>
    {% if dados.data_de and dados.data_ate %}
    <span class="sep-range">{{ dados.data_de }} — {{ dados.data_ate }}</span>
    {% elif dados.data_de %}
    <span class="sep-range">a partir de {{ dados.data_de }}</span>
    {% endif %}
  </form>

  <!-- RESUMO -->
  <div class="sep-resumo">
    <div class="sep-card">
      <div class="sep-card-num">{{ dados.qtd_cestas_confirmadas }}</div>
      <div class="sep-card-rot">cesta{% if dados.qtd_cestas_confirmadas != 1 %}s{% endif %} confirmada{% if dados.qtd_cestas_confirmadas != 1 %}s{% endif %}</div>
    </div>
    <div class="sep-card">
      <div class="sep-card-num">{{ dados.total_itens_distintos }}</div>
      <div class="sep-card-rot">produto{% if dados.total_itens_distintos != 1 %}s{% endif %} distinto{% if dados.total_itens_distintos != 1 %}s{% endif %}</div>
    </div>
    <div class="sep-card">
      <div class="sep-card-num">R$ {{ "%.2f"|format(dados.valor_total_reais) }}</div>
      <div class="sep-card-rot">valor total</div>
    </div>
  </div>

  {% if dados.qtd_cestas_em_ajuste %}
  <div class="sep-alerta">
    ⚠️ <strong>{{ dados.qtd_cestas_em_ajuste }}</strong> cesta{% if dados.qtd_cestas_em_ajuste != 1 %}s{% endif %} ainda em ajuste pelos clientes — a lista pode mudar.
    <a href="/painel/fornecedor/pedidos?status=em_aberto" class="sep-alerta-link">ver →</a>
  </div>
  {% endif %}

  <!-- LISTA POR GRUPO -->
  {% if dados.grupos %}
  <div class="sep-acoes">
    <button onclick="window.print()" class="sep-btn">🖨️ Imprimir / Salvar PDF</button>
  </div>

  {% for g in dados.grupos %}
  <div class="sep-grupo">
    <h3 class="sep-grupo-tit">{{ g.grupo|capitalize }}</h3>
    <div class="sep-itens">
      {% for it in g.itens %}
      <div class="sep-item {% if not it.suficiente %}sep-item-falta{% endif %}">
        <div class="sep-item-info">
          <div class="sep-item-nome">{{ it.produto_nome }}</div>
          <div class="sep-item-saldo">
            {% if it.suficiente %}
              <span style="color:#1d9e75">✓ tem em estoque</span>
              <span class="mut">({{ it.saldo_atual }} {{ it.unidade }})</span>
            {% else %}
              <span style="color:#c66">⚠ faltam {{ it.falta }} {{ it.unidade }}</span>
              <span class="mut">(estoque: {{ it.saldo_atual }})</span>
            {% endif %}
          </div>
        </div>
        <div class="sep-item-qtd">{{ it.quantidade }} {{ it.unidade }}</div>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endfor %}

  {% else %}
  <div class="ped-vazio">
    <div style="font-size:2.5rem;opacity:.4;margin-bottom:.5rem">🏭</div>
    {% if dados.qtd_cestas_em_ajuste %}
      <p>Nenhuma cesta confirmada nesse período ainda.</p>
      <p class="mut" style="margin-top:.5rem;font-size:.9rem">
        Há {{ dados.qtd_cestas_em_ajuste }} em ajuste pelos clientes — assim que confirmarem, aparecem aqui.
      </p>
    {% else %}
      <p>Sem cestas confirmadas pra esse período.</p>
      <p class="mut" style="margin-top:.5rem;font-size:.9rem">
        Cestas confirmadas viram a sua lista de compras do CEASA.
      </p>
    {% endif %}
  </div>
  {% endif %}
</div>

<style>
.ped-back{color:#5dcaa5;display:inline-block;margin-bottom:.8rem;font-size:.9rem;text-decoration:none}
.ped-back:hover{text-decoration:underline}
.ped-abas{display:flex;gap:0;border-bottom:1px solid #2a2a2b;margin-bottom:1rem;overflow-x:auto}
.ped-aba{padding:.55rem 1rem;color:#a8a8a3;border-bottom:2px solid transparent;text-decoration:none;font-size:.92rem;white-space:nowrap}
.ped-aba:hover{color:#ececec}
.ped-aba.ativa{color:#5dcaa5;border-color:#5dcaa5;font-weight:600}
.ped-aba.off{color:#5a5a5a;font-size:.88rem;cursor:default}
.sep-filtro{display:flex;align-items:center;gap:.8rem;margin-bottom:1rem;padding:.7rem .9rem;background:#1a1a1c;border:1px solid #2a2a2b;border-radius:8px;flex-wrap:wrap}
.sep-filtro select{background:#0a0a0a;color:#ececec;border:1px solid #2a2a2b;border-radius:6px;padding:.5rem .7rem;font-size:.9rem;cursor:pointer}
.sep-range{color:#a8a8a3;font-size:.85rem}
.sep-resumo{display:grid;grid-template-columns:repeat(3,1fr);gap:.6rem;margin-bottom:1rem}
.sep-card{background:#1a1a1c;border:1px solid #2a2a2b;border-radius:8px;padding:1rem;text-align:center}
.sep-card-num{color:#5dcaa5;font-size:1.6rem;font-weight:700;line-height:1.1}
.sep-card-rot{color:#a8a8a3;font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;margin-top:.3rem}
.sep-alerta{background:#3a2d12;border:1px solid #6b5320;border-radius:8px;padding:.8rem 1rem;margin-bottom:1rem;color:#e0a83d;font-size:.9rem}
.sep-alerta-link{color:#e0a83d;text-decoration:underline;margin-left:.4rem;white-space:nowrap}
.sep-acoes{margin-bottom:1rem}
.sep-btn{background:#15241d;color:#5dcaa5;border:1px solid #5dcaa5;border-radius:6px;padding:.5rem .9rem;cursor:pointer;font-size:.9rem}
.sep-btn:hover{background:#1d2e25}
.sep-grupo{margin-bottom:1.2rem}
.sep-grupo-tit{color:#5dcaa5;font-size:.92rem;text-transform:uppercase;letter-spacing:.06em;margin:0 0 .5rem;padding-bottom:.3rem;border-bottom:1px solid #2a2a2b}
.sep-itens{display:flex;flex-direction:column;gap:.4rem}
.sep-item{display:flex;justify-content:space-between;align-items:center;padding:.8rem 1rem;background:#1a1a1c;border:1px solid #2a2a2b;border-radius:6px;gap:1rem}
.sep-item-falta{border-left:3px solid #c66}
.sep-item-info{flex:1;min-width:0}
.sep-item-nome{color:#ececec;font-weight:500}
.sep-item-saldo{color:#a8a8a3;font-size:.8rem;margin-top:.2rem}
.sep-item-qtd{color:#5dcaa5;font-weight:700;font-size:1.1rem;white-space:nowrap}
.ped-vazio{text-align:center;padding:3rem 1rem;color:#a8a8a3}
@media (max-width:520px){
  .sep-resumo{grid-template-columns:1fr;gap:.4rem}
  .sep-card{padding:.8rem}
  .sep-card-num{font-size:1.3rem}
}
@media print{
  .topo,.ped-back,.ped-abas,.sep-filtro,.sep-acoes,.sep-alerta{display:none}
  .card.larga{border:none;background:white;color:black}
  .sep-card{background:white;border:1px solid #ccc;color:black}
  .sep-card-num,.sep-grupo-tit,.sep-item-qtd{color:black}
  .sep-item{background:white;border:1px solid #ccc;color:black}
  .sep-item-nome,.sep-item-saldo{color:black}
}
</style>
{% endblock %}"""
```

## EDIT 3 — Registrar `_SEPARACAO_FORN` no dicionário `_templates`

Achar a linha do dicionário e adicionar a entrada:

```python
    ..., "pedido_detalhe_forn": _PEDIDO_DETALHE_FORN, "separacao_forn": _SEPARACAO_FORN,
```

## EDIT 4 — Adicionar a rota `/painel/fornecedor/separacao`

JUNTO das outras rotas de fornecedor (depois da rota de detalhe de pedido, linha
~2810):

```python
@router.get("/painel/fornecedor/separacao", response_class=HTMLResponse)
def painel_fornecedor_separacao(request: Request, periodo: str = "proxima_semana"):
    from finance import pedidos as pedidos_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:  # eh_fornecedor
        return RedirectResponse("/painel", status_code=303)
    periodo_f = (periodo or "proxima_semana").strip() or "proxima_semana"
    dados = pedidos_mod.consolidar_separacao(get_pool(), conta[0], periodo=periodo_f)
    return _render("separacao_forn", request, conta=conta,
                   dados=dados, periodo=periodo_f)
```

---

## NÃO MEXER

- `finance/pedidos.py` — versão atualizada do dono; aplicar verbatim.
- Outras rotas/templates (Lista, detalhe, Catálogo, Compras etc.).

---

## COMO VALIDAR

1. Logar como Thompson → `/painel/fornecedor/pedidos` (Lista). Deve ver no topo
   as 4 sub-abas: **Lista** (ativa) · Separação · Embalagem (cinza) · Rotas (cinza).
2. Clicar **Separação** → vai pra `/painel/fornecedor/separacao`. Como hoje só
   tem 1 cesta `sugerida` (não confirmada), a tela deve mostrar:
   - 0 cestas confirmadas, 0 produtos distintos, R$ 0,00
   - Alerta amarelo: "⚠️ 1 cesta ainda em ajuste pelos clientes" (com link "ver →")
   - Estado vazio educativo
3. Pra testar o caminho feliz, no Render Shell:
   ```bash
   python -c "
   from web.app import _setup
   p = _setup()
   with p.connection() as c:
       c.execute(\"update cesta_semana set status='confirmada' where id=2\")
       c.commit()
   print('cesta 2 confirmada')
   "
   ```
   Aí recarrega `/painel/fornecedor/separacao` (selecionando "Próximas entregas"
   ou "Próximos 30 dias", já que 07/07 não cai em próxima semana):
   - 1 cesta confirmada, 8 produtos, R$ 120,00
   - 4 grupos (fruta · legume · verdura · tempero)
   - Cada produto com quantidade e marca "✓ tem em estoque" (verde)
   - Botão "🖨️ Imprimir / Salvar PDF" funciona (Ctrl+P esconde menu e cabeçalho)

Esse passo entrega o pick list operacional — o fornecedor sabe exatamente o que
precisa comprar pra entregar as cestas confirmadas da semana.
