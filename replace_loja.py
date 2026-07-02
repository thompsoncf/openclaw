import re, sys

# Loja v3 (design claro do Claude Design). Pagina standalone (nao estende _BASE).
# Gerado – subir verbatim. Roda: python replace_loja.py
novo_loja = """<!doctype html>
<html lang="pt-br"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ fornecedor.nome }} · Zaq</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  html,body{margin:0;padding:0}
  body{background:#eef0ec;font-family:'Poppins',system-ui,-apple-system,sans-serif;color:#23301f;-webkit-font-smoothing:antialiased}
  *{box-sizing:border-box}
  .noscroll{scrollbar-width:none;-ms-overflow-style:none}
  .noscroll::-webkit-scrollbar{display:none}
  .sz-body{display:grid;grid-template-columns:186px 1fr 312px}
  .sz-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:13px}
  .sz-shelf{display:flex;gap:12px;overflow-x:auto;padding-bottom:6px}
  .sz-shelfcard{flex:0 0 152px;background:#fff;border:1px solid #e8ece5;border-radius:14px;overflow:hidden;box-shadow:0 1px 3px rgba(20,40,15,.05)}
  .sz-foto{background:#eef3ea;background-size:cover;background-position:center;display:flex;align-items:center;justify-content:center}
  .sz-chips{display:none}
  .sz-tab{display:none}
  .sz-tab.on{display:block}
  .qadd{position:absolute;bottom:6px;right:6px;width:32px;height:32px;border-radius:50%;background:#2f7d32;color:#fff;border:0;cursor:pointer;font-size:19px;line-height:1;box-shadow:0 2px 6px rgba(0,0,0,.22)}
  @media (max-width:900px){
    .sz-body{grid-template-columns:1fr}
    .sz-nav{display:none !important}
    .sz-cart{position:sticky;bottom:0;border-top:2px solid #31772f !important;border-radius:14px 14px 0 0 !important;box-shadow:0 -10px 26px rgba(0,0,0,.10)}
    .sz-chips{display:flex !important}
    .sz-vitrine{border-right:0 !important;padding:16px 6px !important}
  }
  @media (max-width:520px){ .sz-grid{grid-template-columns:1fr 1fr} }
  .sz-cover{height:170px;background-position:center;background-size:cover}
  .sz-logo{width:92px;height:92px;border-radius:22px;font-size:34px}
  .sz-idrow{margin-top:-40px}
  @media (max-width:640px){
    .sz-cover{height:118px}
    .sz-logo{width:70px;height:70px;border-radius:16px;font-size:24px}
    .sz-idrow{margin-top:-28px;gap:12px}
    .sz-idrow .sz-nome{font-size:19px !important}
  }
</style>
</head><body>

{% macro card_full(p) %}
{% set eff = p.preco_promo_centavos if (p.em_promo and p.preco_promo_centavos) else p.preco_venda_centavos %}
<div class="prodcard" data-nome="{{ p.nome }}" style="background:#fff;border:1px solid #e8ece5;border-radius:15px;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 1px 3px rgba(20,40,15,.05)">
  <div style="position:relative">
    <div class="sz-foto" style="height:118px;{% if p.foto_url %}background-image:url('{{ p.foto_url }}'){% endif %}">{% if not p.foto_url %}<span style="font-size:34px;opacity:.5">🥬</span>{% endif %}</div>
    {% if p.em_promo and p.preco_promo_centavos %}<span style="position:absolute;top:8px;left:8px;background:#f48b22;color:#fff;font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px">OFERTA</span>{% endif %}
  </div>
  <div style="padding:12px;display:flex;flex-direction:column;flex:1">
    <div style="font-size:14px;color:#2f7d32;font-weight:600;line-height:1.25">{{ p.nome }}</div>
    {% if p.descricao_curta %}<div style="font-size:11px;color:#8a938a;margin:2px 0 0">{{ p.descricao_curta }}</div>{% endif %}
    <div style="margin-top:auto;padding-top:12px">
      <div style="white-space:nowrap">
        {% if p.em_promo and p.preco_promo_centavos %}<span style="font-size:11px;color:#a3aca0;text-decoration:line-through;margin-right:4px">R$ {{ "%.2f"|format(p.preco_venda_centavos/100) }}</span>{% endif %}
        <span style="font-size:15px;color:#2f7d32;font-weight:700">R$ {{ "%.2f"|format(eff/100) }}</span><span style="font-size:11px;color:#8a938a;font-weight:400">/{{ p.unidade }}</span>
      </div>
      {% if fornecedor.desconto_pix_pct %}<div style="font-size:11px;color:#31772f;margin-top:2px">no Pix <strong>R$ {{ "%.2f"|format(eff*(1-(fornecedor.desconto_pix_pct or 0)/100)/100) }}</strong></div>{% endif %}
      <div style="display:flex;align-items:center;gap:8px;margin-top:10px">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:4px;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:9px;padding:3px;flex:1;min-width:0">
          <button onclick="szDec({{ p.id }},'{{ p.unidade }}')" style="width:28px;height:28px;border-radius:7px;background:#e6e9e2;color:#3a463a;border:0;font-size:17px;cursor:pointer;line-height:1;flex-shrink:0">−</button>
          <span id="sel-{{ p.id }}" style="font-size:13px;color:#23301f;font-weight:600;text-align:center">{% if p.unidade in ['unidade','duzia','maco','bandeja','pacote'] %}1{% else %}0,5{% endif %}</span>
          <button onclick="szInc({{ p.id }},'{{ p.unidade }}')" style="width:28px;height:28px;border-radius:7px;background:#e6e9e2;color:#3a463a;border:0;font-size:17px;cursor:pointer;line-height:1;flex-shrink:0">+</button>
        </div>
        <button onclick="szAdd({{ p.id }},'{{ p.unidade }}')" title="Adicionar" style="width:40px;height:36px;border-radius:9px;background:#2f7d32;color:#fff;border:0;cursor:pointer;flex-shrink:0;font-size:18px">🛒</button>
      </div>
      <div data-incart="{{ p.id }}" style="display:none;font-size:10.5px;color:#31772f;font-weight:600;margin-top:6px">✓ <span></span> no carrinho</div>
    </div>
  </div>
</div>
{% endmacro %}

{% macro card_shelf(p) %}
{% set eff = p.preco_promo_centavos if (p.em_promo and p.preco_promo_centavos) else p.preco_venda_centavos %}
<div class="prodcard sz-shelfcard" data-nome="{{ p.nome }}">
  <div style="position:relative">
    <div class="sz-foto" style="height:96px;{% if p.foto_url %}background-image:url('{{ p.foto_url }}'){% endif %}">{% if not p.foto_url %}<span style="font-size:28px;opacity:.5">🥬</span>{% endif %}</div>
    {% if p.em_promo and p.preco_promo_centavos %}<span style="position:absolute;top:6px;left:6px;background:#f48b22;color:#fff;font-size:9px;font-weight:700;padding:2px 7px;border-radius:20px">OFERTA</span>{% endif %}
    <button class="qadd" onclick="szQuick({{ p.id }},'{{ p.unidade }}')" title="Adicionar">+</button>
  </div>
  <div style="padding:9px 10px">
    <div style="font-size:12.5px;color:#2f7d32;font-weight:600;line-height:1.2">{{ p.nome }}</div>
    <div style="font-size:13px;color:#2f7d32;font-weight:700;margin-top:4px">R$ {{ "%.2f"|format(eff/100) }}<span style="font-size:10px;color:#8a938a;font-weight:400">/{{ p.unidade }}</span></div>
    {% if fornecedor.desconto_pix_pct %}<div style="font-size:10px;color:#31772f">no Pix R$ {{ "%.2f"|format(eff*(1-(fornecedor.desconto_pix_pct or 0)/100)/100) }}</div>{% endif %}
    <div data-incart="{{ p.id }}" style="display:none;font-size:9.5px;color:#31772f;font-weight:600;margin-top:2px">✓ <span></span></div>
  </div>
</div>
{% endmacro %}

<div style="max-width:1180px;margin:0 auto;background:#fff;min-height:100vh;box-shadow:0 0 40px rgba(0,0,0,.06)">

  <div style="padding:12px 22px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #e7eae5">
    <span style="display:flex;align-items:center;gap:8px">
      <svg width="22" height="22" viewBox="0 0 64 64" fill="none"><path d="M16 18 H44 L18 46 H46" stroke="#0f7d5c" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"></path><path d="M47 10 L49 16 L55 18 L49 20 L47 26 L45 20 L39 18 L45 16 Z" fill="#f48b22"></path></svg>
      <span style="color:#1a2417;font-weight:700;font-size:17px;letter-spacing:-.6px">zaq</span>
    </span>
    {% if logado %}<a href="/painel" style="color:#31772f;font-size:13px;font-weight:500;text-decoration:none">Meu painel</a>
    {% else %}<a href="/login" style="color:#31772f;font-size:13px;font-weight:500;text-decoration:none">Entrar · Criar conta</a>{% endif %}
  </div>

  <div style="position:relative">
    <div class="sz-cover" style="background:{% if fornecedor.banner_url %}url('{{ fornecedor.banner_url }}') center/cover{% elif fornecedor.banner_cor %}{{ fornecedor.banner_cor }}{% else %}linear-gradient(120deg,#3a7d3a,#245c24){% endif %}"></div>
    <span style="position:absolute;top:14px;right:16px;background:rgba(0,0,0,.45);color:#eafae6;font-size:12px;font-weight:600;padding:6px 13px;border-radius:20px;display:flex;align-items:center;gap:7px;backdrop-filter:blur(4px)">
      <span style="width:8px;height:8px;border-radius:50%;background:#8ff0a0"></span>Aberto agora
    </span>
  </div>

  <div style="padding:0 22px">
    <div class="sz-idrow" style="display:flex;align-items:flex-end;gap:16px;position:relative">
      <div class="sz-logo" style="box-shadow:0 6px 18px rgba(0,0,0,.18);border:4px solid #fff;flex-shrink:0;background:{% if fornecedor.logo_url %}url('{{ fornecedor.logo_url }}') center/cover{% else %}#eef6ea{% endif %};display:flex;align-items:center;justify-content:center;font-weight:700;color:#31772f">{% if not fornecedor.logo_url %}{{ fornecedor.nome[0]|upper }}{% endif %}</div>
      <div style="padding-bottom:6px">
        <div style="display:flex;align-items:center;gap:9px;flex-wrap:wrap">
          <span class="sz-nome" style="font-size:24px;font-weight:700;color:#1a2417;letter-spacing:-.3px">{{ fornecedor.nome }}</span>
          {% if fornecedor.verificado %}<span style="display:inline-flex;align-items:center;gap:4px;background:#eef6ea;color:#31772f;font-size:11px;font-weight:700;padding:3px 9px;border-radius:20px">✓ Produtor verificado</span>{% endif %}
        </div>
        {% if fornecedor.bio %}<div style="font-size:13px;color:#6b7669;margin-top:5px">{{ fornecedor.bio }}</div>{% endif %}
      </div>
    </div>

    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:16px">
      <span style="display:inline-flex;align-items:center;gap:6px;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:20px;padding:7px 13px;font-size:12px;color:#3a463a;font-weight:500">🕐 40–60 min</span>
      <span style="display:inline-flex;align-items:center;gap:6px;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:20px;padding:7px 13px;font-size:12px;color:#3a463a;font-weight:500">Entrega R$ {{ "%.0f"|format((fornecedor.taxa_entrega_centavos or 0)/100) }}</span>
      <span style="display:inline-flex;align-items:center;gap:6px;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:20px;padding:7px 13px;font-size:12px;color:#3a463a;font-weight:500">Mín. R$ {{ "%.0f"|format((fornecedor.pedido_minimo_centavos or 0)/100) }}</span>
      {% if fornecedor.endereco %}<span style="display:inline-flex;align-items:center;gap:6px;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:20px;padding:7px 13px;font-size:12px;color:#3a463a;font-weight:500">📍 {{ fornecedor.endereco }}</span>{% endif %}
      <span style="display:inline-flex;align-items:center;gap:6px;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:20px;padding:7px 13px;font-size:12px;color:#3a463a;font-weight:500">💳 Pix · Cartão · Dinheiro</span>
    </div>
  </div>

  <div style="display:flex;gap:4px;padding:22px 22px 0;border-bottom:1px solid #e7eae5;overflow-x:auto" class="noscroll">
    <button class="tabbtn on" data-tab="comprar" onclick="szTab('comprar')" style="font-size:14px;font-weight:600;padding:12px 20px;border:none;border-bottom:2px solid #2f7d32;background:none;color:#2f7d32;cursor:pointer;white-space:nowrap;font-family:inherit">Comprar</button>
    <button class="tabbtn" data-tab="assinar" onclick="szTab('assinar')" style="font-size:14px;font-weight:600;padding:12px 20px;border:none;border-bottom:2px solid transparent;background:none;color:#8a938a;cursor:pointer;white-space:nowrap;font-family:inherit">Assinar cesta</button>
    <button class="tabbtn" data-tab="promocoes" onclick="szTab('promocoes')" style="font-size:14px;font-weight:600;padding:12px 20px;border:none;border-bottom:2px solid transparent;background:none;color:#8a938a;cursor:pointer;white-space:nowrap;font-family:inherit">🏷️ Promoções</button>
    <button class="tabbtn" data-tab="pedidos" onclick="szTab('pedidos')" style="font-size:14px;font-weight:600;padding:12px 20px;border:none;border-bottom:2px solid transparent;background:none;color:#8a938a;cursor:pointer;white-space:nowrap;font-family:inherit">📦 Meus pedidos</button>
  </div>

  <!-- ============ COMPRAR ============ -->
  <div class="sz-tab on" data-tab="comprar">
    <div class="sz-body">
      <nav class="sz-nav" style="position:sticky;top:12px;align-self:start;border-right:1px solid #e7eae5;padding:22px 16px 22px 22px">
        <div style="font-size:11px;color:#31772f;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px">Categorias</div>
        <button onclick="szCat('tudo',this)" class="catbtn on" style="display:flex;align-items:center;gap:10px;width:100%;text-align:left;font-size:13.5px;color:#3a463a;background:#eef6ea;border:0;border-radius:9px;padding:9px 11px;cursor:pointer;font-family:inherit;margin-bottom:3px">🧺 Tudo</button>
        {% for cat, itens in secoes %}<button onclick="szCat('{{ cat }}',this)" class="catbtn" style="display:flex;align-items:center;gap:10px;width:100%;text-align:left;font-size:13.5px;color:#3a463a;background:none;border:0;border-radius:9px;padding:9px 11px;cursor:pointer;font-family:inherit;margin-bottom:3px">🥕 {{ cat|capitalize }}</button>{% endfor %}
      </nav>

      <div class="sz-vitrine" style="padding:22px 24px;border-right:1px solid #e7eae5;min-width:0">
        <div style="position:relative;display:flex;align-items:center;margin-bottom:18px">
          <svg style="position:absolute;left:14px;pointer-events:none" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#9aa398" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.3-4.3"></path></svg>
          <input type="text" oninput="szBusca(this.value)" placeholder="Buscar produto" style="width:100%;padding:11px 14px 11px 40px;background:#f4f6f2;border:1px solid #e2e7dd;color:#23301f;border-radius:11px;font-size:13.5px;font-family:inherit;outline:none">
        </div>

        <div class="sz-chips noscroll" style="gap:7px;margin-bottom:16px;overflow-x:auto">
          <button onclick="szCat('tudo',this)" style="font-size:12px;padding:7px 15px;border-radius:20px;background:#eef6ea;color:#31772f;border:1px solid #cfe6c6;white-space:nowrap;cursor:pointer;font-family:inherit;flex-shrink:0">Tudo</button>
          {% for cat, itens in secoes %}<button onclick="szCat('{{ cat }}',this)" style="font-size:12px;padding:7px 15px;border-radius:20px;background:#f4f6f2;color:#3a463a;border:1px solid #e2e7dd;white-space:nowrap;cursor:pointer;font-family:inherit;flex-shrink:0">{{ cat|capitalize }}</button>{% endfor %}
        </div>

        {% if sazonais %}
        <div class="szsec" data-cat="_sazonal" style="margin-bottom:26px">
          <div style="font-size:14px;font-weight:700;color:#1a2417;margin-bottom:12px">🌱 Tá na época</div>
          <div class="sz-shelf noscroll">
            {% for p in sazonais %}{{ card_shelf(p) }}{% endfor %}
          </div>
        </div>
        {% endif %}

        {% if mais_vendidos %}
        <div class="szsec" data-cat="_hot" style="margin-bottom:26px">
          <div style="font-size:14px;font-weight:700;color:#1a2417;margin-bottom:12px">🔥 Mais vendidos</div>
          <div class="sz-shelf noscroll">
            {% for p in mais_vendidos %}{{ card_shelf(p) }}{% endfor %}
          </div>
        </div>
        {% endif %}

        {% for cat, itens in secoes %}
        <div class="szsec" data-cat="{{ cat }}" style="margin-bottom:30px">
          <div style="font-size:13px;color:#31772f;font-weight:600;margin:4px 0 14px;display:flex;align-items:center;gap:8px">🥬 {{ cat|capitalize }}</div>
          <div class="sz-grid">
            {% for p in itens %}{{ card_full(p) }}{% endfor %}
          </div>
        </div>
        {% endfor %}

        {% if fornecedor.sobre %}
        <div style="background:#f7f9f5;border:1px solid #e7eae5;border-radius:14px;padding:16px 18px;margin-top:8px">
          <div style="font-size:13px;font-weight:700;color:#1a2417">Sobre a banca</div>
          <p style="font-size:13px;color:#6b7669;line-height:1.55;margin:8px 0 14px">{{ fornecedor.sobre }}</p>
          {% if fornecedor.whatsapp_loja %}<a href="https://wa.me/55{{ fornecedor.whatsapp_loja|replace('(','')|replace(')','')|replace(' ','')|replace('-','')|replace('+','') }}" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:8px;background:#25d366;color:#fff;text-decoration:none;border-radius:10px;padding:10px 18px;font-size:13px;font-weight:600">💬 Falar no WhatsApp</a>{% endif %}
        </div>
        {% endif %}
      </div>

      <div class="sz-cart" id="sz-cart" style="padding:22px 20px;background:#f7f9f5;align-self:start;position:sticky;top:12px"></div>
    </div>
  </div>

  <!-- ============ PROMOÇÕES ============ -->
  <div class="sz-tab" data-tab="promocoes">
    <div style="padding:24px 22px 44px">
      <div style="font-size:13px;color:#f48b22;font-weight:700;text-transform:uppercase;letter-spacing:.04em">🏷️ Ofertas de hoje</div>
      <p style="font-size:13px;color:#6b7669;margin:6px 0 18px">Produtos com preço promocional – por tempo limitado.</p>
      {% set ns = namespace(has=false) %}
      {% for cat, itens in secoes %}{% for p in itens %}{% if p.em_promo and p.preco_promo_centavos %}{% set ns.has = true %}{% endif %}{% endfor %}{% endfor %}
      {% if ns.has %}
      <div class="sz-grid">{% for cat, itens in secoes %}{% for p in itens %}{% if p.em_promo and p.preco_promo_centavos %}{{ card_full(p) }}{% endif %}{% endfor %}{% endfor %}</div>
      {% else %}
      <div style="text-align:center;padding:40px 20px;color:#8a938a;font-size:14px">Nenhuma oferta ativa no momento. Volte logo! 🥕</div>
      {% endif %}
    </div>
  </div>

  <!-- ============ MEUS PEDIDOS (Fase 2 preenche 'pedidos') ============ -->
  <div class="sz-tab" data-tab="pedidos">
    <div style="max-width:720px;padding:26px 22px 44px">
      {% if pedidos %}
      {% for o in pedidos %}
      <div style="background:#fff;border:1px solid #e8ece5;border-radius:18px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(20,40,15,.05)">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
          <div>
            <span style="font-size:16px;font-weight:700;color:#1a2417">Pedido {{ o.code }}</span>
            <span style="font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;background:#eef6ea;color:#31772f;margin-left:8px">{{ o.status }}</span>
            <div style="font-size:12.5px;color:#8a938a;margin-top:3px">{{ o.data }}</div>
          </div>
          <div style="text-align:right"><div style="font-size:12px;color:#8a938a">Total</div><div style="font-size:18px;font-weight:700;color:#2f7d32">R$ {{ "%.2f"|format((o.total_centavos or 0)/100) }}</div></div>
        </div>
      </div>
      {% endfor %}
      {% else %}
      <div style="text-align:center;padding:50px 20px;color:#8a938a;font-size:14px">Você ainda não fez pedidos nesta banca.<br>Adicione produtos e faça seu primeiro! 🛒</div>
      {% endif %}
    </div>
  </div>

  <!-- ============ ASSINAR CESTA ============ -->
  <div class="sz-tab" data-tab="assinar">
    <div style="padding:26px 22px 44px;max-width:640px">
      <div style="font-size:13px;color:#31772f;line-height:1.55;background:#eef6ea;border:1px solid #d8ead2;border-radius:12px;padding:14px 16px;margin-bottom:22px">🧺 Uma cesta fresca na sua porta toda semana. <strong>Sem fidelidade</strong> – cancele quando quiser.</div>
      <form method="post" action="/f/{{ fornecedor.slug }}/assinar">
        <div style="font-size:13px;color:#31772f;font-weight:600;margin-bottom:12px">1. Escolha o tamanho</div>
        {% for t in tamanhos %}
        <label onclick="szTam(this)" class="tamopt{% if escolha.tamanho_id == t.id %} sel{% endif %}" style="display:block;background:#fff;border:1px solid #e7eae5;border-radius:13px;padding:14px;margin-bottom:9px;cursor:pointer">
          <input type="radio" name="tamanho_id" value="{{ t.id }}" required style="display:none" {% if escolha.tamanho_id == t.id %}checked{% endif %}>
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div style="display:flex;align-items:center;gap:12px">
              <div style="width:44px;height:44px;border-radius:11px;background:#eef6ea;display:flex;align-items:center;justify-content:center;font-size:22px">🧺</div>
              <div>
                <div style="font-size:14px;color:#1a2417;font-weight:600">{{ t.nome }}</div>
                <div style="font-size:11px;color:#8a938a">{{ t.qtd_frutas }} frutas · {{ t.qtd_legumes }} legumes · {{ t.qtd_verduras }} verduras · {{ t.qtd_temperos }} temperos</div>
              </div>
            </div>
            <div style="font-size:15px;color:#2f7d32;font-weight:700">R$ {{ "%.0f"|format(t.preco_centavos/100) }}</div>
          </div>
        </label>
        {% endfor %}
        <div style="font-size:13px;color:#31772f;font-weight:600;margin:20px 0 12px">2. Com que frequência?</div>
        <div style="display:flex;gap:8px;margin-bottom:20px">
          <label class="freqopt" onclick="szFreq(this)" style="flex:1;text-align:center;background:#fff;color:#3a463a;border:1px solid #e7eae5;border-radius:11px;padding:11px;font-size:12.5px;cursor:pointer"><input type="radio" name="frequencia" value="semanal" style="display:none" {% if (escolha.frequencia or 'semanal')=='semanal' %}checked{% endif %}>Semanal</label>
          <label class="freqopt" onclick="szFreq(this)" style="flex:1;text-align:center;background:#fff;color:#3a463a;border:1px solid #e7eae5;border-radius:11px;padding:11px;font-size:12.5px;cursor:pointer"><input type="radio" name="frequencia" value="quinzenal" style="display:none" {% if escolha.frequencia=='quinzenal' %}checked{% endif %}>Quinzenal</label>
          <label class="freqopt" onclick="szFreq(this)" style="flex:1;text-align:center;background:#fff;color:#3a463a;border:1px solid #e7eae5;border-radius:11px;padding:11px;font-size:12.5px;cursor:pointer"><input type="radio" name="frequencia" value="mensal" style="display:none" {% if escolha.frequencia=='mensal' %}checked{% endif %}>Mensal</label>
        </div>
        <details style="margin-bottom:20px">
          <summary style="font-size:12px;color:#8a938a;cursor:pointer">Algum item que você não quer receber? (opcional)</summary>
          <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px">
            {% for p in produtos %}<label style="font-size:11px;color:#3a463a;background:#f4f6f2;border:1px solid #e2e7dd;border-radius:16px;padding:4px 11px;cursor:pointer"><input type="checkbox" name="restricoes" value="{{ p.id }}" style="margin-right:4px" {% if p.id in (escolha.restricoes or []) %}checked{% endif %}>{{ p.nome }}</label>{% endfor %}
          </div>
        </details>
        <button type="submit" style="display:block;width:100%;background:#2f7d32;color:#fff;border:0;border-radius:11px;padding:14px;font-size:14px;font-weight:700;cursor:pointer;font-family:inherit">Assinar cesta</button>
        <div style="text-align:center;font-size:11px;color:#8a938a;margin-top:8px">Você só paga 4 dias antes de cada entrega</div>
      </form>
    </div>
  </div>

</div>

<script>
var SLUG="{{ fornecedor.slug }}";
var PIXPCT={{ fornecedor.desconto_pix_pct or 0 }};
var FRETEG={{ fornecedor.frete_gratis_acima_centavos or 0 }};
var CART={{ carrinho_json|safe }};
var SEL={};
function inc(u){return ['unidade','duzia','maco','bandeja','pacote'].indexOf(u)>=0?1:0.5;}
function brl(c){return 'R$ '+(c/100).toFixed(2).replace('.',',');}
function pix(c){return c*(1-PIXPCT/100);}

function szTab(q){
  document.querySelectorAll('.sz-tab').forEach(function(t){t.classList.toggle('on',t.dataset.tab===q);});
  document.querySelectorAll('.tabbtn').forEach(function(b){var on=b.dataset.tab===q;b.style.color=on?'#2f7d32':'#8a938a';b.style.borderBottomColor=on?'#2f7d32':'transparent';});
}
function szCat(cat,el){
  document.querySelectorAll('.szsec').forEach(function(s){var c=s.dataset.cat;s.style.display=(cat==='tudo'||c===cat||c==='_sazonal'||c==='_hot')?'block':'none';});
  document.querySelectorAll('.catbtn').forEach(function(b){b.classList.remove('on');b.style.background='none';});
  if(el&&el.classList.contains('catbtn')){el.classList.add('on');el.style.background='#eef6ea';}
}
function szBusca(q){
  q=(q||'').toLowerCase().trim();
  document.querySelectorAll('.prodcard').forEach(function(c){
    var m=(c.dataset.nome||'').toLowerCase().indexOf(q)>=0;
    c.style.display=(!q||m)?'':'none';
  });
}
function szTam(el){document.querySelectorAll('.tamopt').forEach(function(t){t.style.border='1px solid #e7eae5';t.style.background='#fff';});el.style.border='2px solid #2f7d32';el.style.background='#eef6ea';var r=el.querySelector('input');if(r)r.checked=true;}
function szFreq(el){document.querySelectorAll('.freqopt').forEach(function(f){f.style.border='1px solid #e7eae5';f.style.background='#fff';f.style.color='#3a463a';});el.style.border='2px solid #2f7d32';el.style.background='#eef6ea';el.style.color='#2f7d32';var r=el.querySelector('input');if(r)r.checked=true;}
function szInitAssinar(){
  var t=document.querySelector('.tamopt input:checked');if(t)szTam(t.parentNode);
  var f=document.querySelector('.freqopt input:checked');if(f)szFreq(f.parentNode);
}

function selQ(pid,u){if(SEL[pid]==null)SEL[pid]=inc(u);return SEL[pid];}
function szInc(pid,u){SEL[pid]=selQ(pid,u)+inc(u);document.getElementById('sel-'+pid).textContent=fmtQ(SEL[pid]);}
function szDec(pid,u){var v=selQ(pid,u)-inc(u);SEL[pid]=v<inc(u)?inc(u):v;document.getElementById('sel-'+pid).textContent=fmtQ(SEL[pid]);}
function fmtQ(v){return (v%1===0)?v:v.toFixed(1).replace('.',',');}

async function szAdd(pid,u){
  var q=selQ(pid,u);
  try{var r=await fetch('/f/'+SLUG+'/carrinho/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({produto_id:pid,quantidade:q})});CART=await r.json();renderCart();syncCards();}catch(e){console.error(e);}
}
async function szQuick(pid,u){
  try{var r=await fetch('/f/'+SLUG+'/carrinho/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({produto_id:pid,quantidade:inc(u)})});CART=await r.json();renderCart();syncCards();}catch(e){console.error(e);}
}

function syncCards(){
  document.querySelectorAll('[data-incart]').forEach(function(el){el.style.display='none';});
  (CART.itens||[]).forEach(function(it){
    document.querySelectorAll('[data-incart="'+it.produto_id+'"]').forEach(function(el){
      el.style.display='block';el.querySelector('span').textContent=fmtQ(it.qtd)+it.unidade;
    });
  });
}

function renderCart(){
  var box=document.getElementById('sz-cart');
  var n=(CART.itens||[]).length;
  var h='<div style="font-size:15px;color:#1a2417;font-weight:700;margin-bottom:14px;display:flex;align-items:center;gap:8px">🛒 Carrinho';
  if(n)h+='<span style="background:#31772f;color:#fff;font-size:11px;font-weight:700;padding:1px 8px;border-radius:12px;margin-left:auto">'+n+'</span>';
  h+='</div>';
  if(!n){h+='<div style="font-size:13px;color:#8a938a;line-height:1.5">Carrinho vazio. Adicione produtos!</div>';box.innerHTML=h;return;}
  var sub=CART.subtotal||0;
  if(FRETEG>0){
    var falta=FRETEG-sub, gratis=falta<=0, pct=Math.min(100,Math.round(sub/FRETEG*100));
    h+='<div style="margin-bottom:14px">';
    h+=gratis?'<div style="font-size:12px;color:#31772f;font-weight:600;margin-bottom:6px">✓ Você ganhou frete grátis!</div>':'<div style="font-size:11.5px;color:#6b7669;margin-bottom:6px">Faltam <strong style="color:#31772f">'+brl(falta)+'</strong> pro frete grátis</div>';
    h+='<div style="height:7px;background:#e6ebe2;border-radius:5px;overflow:hidden"><div style="height:100%;width:'+pct+'%;background:'+(gratis?'#2f7d32':'#7bbf5a')+'"></div></div></div>';
  }
  (CART.itens||[]).forEach(function(it){
    h+='<div style="display:flex;justify-content:space-between;gap:10px;font-size:12px;padding:8px 0;border-bottom:1px solid #e7eae5"><span style="color:#5b6659">'+fmtQ(it.qtd)+it.unidade+' '+it.nome+'</span><span style="color:#1a2417;white-space:nowrap;font-weight:500">'+brl(it.total)+'</span></div>';
  });
  h+='<div style="font-size:12.5px;color:#8a938a;line-height:2.1;margin-top:10px">';
  h+='<div style="display:flex;justify-content:space-between"><span>Subtotal</span><span style="color:#1a2417">'+brl(sub)+'</span></div>';
  h+='<div style="display:flex;justify-content:space-between"><span>Entrega</span><span style="color:#1a2417">'+brl(CART.taxa||0)+'</span></div>';
  h+='<div style="display:flex;justify-content:space-between;border-top:1px solid #dde3d9;margin-top:6px;padding-top:8px"><span style="color:#1a2417;font-weight:600">Total</span><span style="color:#2f7d32;font-weight:700">'+brl(CART.total||0)+'</span></div>';
  if(PIXPCT>0)h+='<div style="display:flex;justify-content:space-between;font-size:11.5px;margin-top:2px"><span style="color:#31772f">no Pix (−'+PIXPCT+'%)</span><span style="color:#31772f;font-weight:700">'+brl(pix(CART.total||0))+'</span></div>';
  h+='</div>';
  var min=CART.minimo||0;
  if(min&&sub<min){
    h+='<div style="background:#fdf3e6;border:1px solid #f0c98f;border-radius:9px;padding:9px 11px;margin:12px 0;font-size:11.5px;color:#b5750f">Faltam '+brl(min-sub)+' pro mínimo</div>';
    h+='<button style="display:block;width:100%;background:#e6e9e2;color:#9aa398;border:0;border-radius:11px;padding:13px;font-size:13.5px;font-weight:600;cursor:not-allowed;font-family:inherit">Enviar pedido</button>';
  }else{
    h+='<div style="background:#eef6ea;border:1px solid #cfe6c6;border-radius:9px;padding:10px 12px;margin:12px 0;font-size:11.5px;color:#31772f;line-height:1.4">⏰ Confirma e paga depois. Sem cobrança antes.</div>';
    h+='<a href="/f/'+SLUG+'/carrinho/revisar" style="display:block;text-align:center;width:100%;background:#f48b22;color:#fff;text-decoration:none;border-radius:11px;padding:13px;font-size:13.5px;font-weight:700;font-family:inherit">Ver carrinho</a>';
  }
  box.innerHTML=h;
}
renderCart();syncCards();szInitAssinar();
</script>
</body></html>
"""

ALVO = "web/portal.py"
src = open(ALVO, encoding="utf-8").read()
assert '"""' not in novo_loja, "template tem triplo-aspas"
achados = re.findall(r'_LOJA = """.*?(?:{% endblock %}|</html>)\s*"""', src, flags=re.DOTALL)
if len(achados) != 1:
    print(f"ABORTADO: encontrei {len(achados)} blocos _LOJA (esperado 1). Nada alterado."); sys.exit(1)
novo = re.sub(r'_LOJA = """.*?(?:{% endblock %}|</html>)\s*"""',
              lambda m: '_LOJA = """' + novo_loja + '"""', src, count=1, flags=re.DOTALL)
open(ALVO, "w", encoding="utf-8").write(novo)
print("OK: _LOJA v3 (header responsivo) aplicado")
print('Pre-deploy: python -c "from web import portal"')
