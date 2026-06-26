#!/usr/bin/env python3
import re

# Ler o arquivo
with open('web/portal.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Novo template (versão refinada)
novo_loja = r'''{% extends "base" %}{% block conteudo %}
<style>
  .lj-wrap{max-width:940px;margin:0 auto}
  .lj-header{height:96px;background:linear-gradient(125deg,#14342a,#0f2820);position:relative;display:flex;align-items:flex-end;padding:0 1.3rem;border-radius:14px 14px 0 0;background-size:cover;background-position:center}
  .lj-aberto{position:absolute;top:13px;right:16px;background:#15301f;color:#5dcaa5;font-size:10.5px;padding:4px 11px;border-radius:20px;border:1px solid #1d9e7544}
  .lj-id{display:flex;align-items:center;gap:13px;transform:translateY(20px)}
  .lj-logo{width:60px;height:60px;border-radius:15px;background:#1d9e75;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:25px;border:3px solid #0e0e0f;flex-shrink:0}
  .lj-nome{font-size:18px;color:#f4f4f4;font-weight:600}
  .lj-info{font-size:11.5px;color:#9fbfb0;margin-top:2px}
  .lj-abas{display:flex;gap:2px;padding:1.5rem 1.3rem 0;border-bottom:1px solid #1c1c1f;overflow-x:auto}
  .lj-aba{font-size:12.5px;padding:10px 16px;color:#888780;border-bottom:2px solid transparent;cursor:pointer;white-space:nowrap;text-decoration:none;background:none;border-top:0;border-left:0;border-right:0;font-family:inherit}
  .lj-aba.on{color:#f4f4f4;font-weight:500;border-bottom-color:#1d9e75}
  .lj-body{display:grid;grid-template-columns:1fr 310px}
  .lj-vitrine{padding:1.1rem 1.3rem;border-right:1px solid #1c1c1f;min-width:0}
  .lj-chips{display:flex;gap:7px;margin-bottom:1.1rem;overflow-x:auto;padding-bottom:2px}
  .lj-chip{font-size:12px;padding:6px 15px;border-radius:20px;background:#161617;color:#b4b2a9;border:1px solid #2a2a2b;white-space:nowrap;cursor:pointer;flex-shrink:0}
  .lj-chip.on{background:#1d9e75;color:#fff;border-color:#1d9e75}
  .lj-sec-tit{font-size:12.5px;color:#5dcaa5;font-weight:600;margin:.6rem 0 .7rem}
  .lj-grade{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:1.3rem}
  .lj-card{background:#161617;border:1px solid #232325;border-radius:13px;overflow:hidden;display:flex;flex-direction:column}
  .lj-foto{height:88px;background:#1a2a1f;display:flex;align-items:center;justify-content:center;background-size:cover;background-position:center}
  .lj-card-body{padding:.7rem;display:flex;flex-direction:column;flex:1}
  .lj-pnome{font-size:13px;color:#f4f4f4;font-weight:500}
  .lj-pdesc{font-size:10.5px;color:#888780;margin:1px 0 0}
  .lj-prow{display:flex;align-items:center;justify-content:space-between;margin-top:auto;padding-top:8px}
  .lj-preco{font-size:13.5px;color:#5dcaa5;font-weight:600}
  .lj-preco small{color:#888780;font-weight:400;font-size:10px}
  .lj-add{display:flex;align-items:center;gap:4px;background:#1d9e75;color:#fff;border:0;border-radius:8px;padding:7px 13px;font-size:12px;font-weight:600;cursor:pointer}
  .lj-qtd{display:flex;align-items:center;gap:6px;background:#0e0e0f;border-radius:8px;padding:3px}
  .lj-qbtn{width:27px;height:27px;padding:0;border-radius:6px;background:#1d9e75;color:#fff;border:0;font-size:16px;cursor:pointer;line-height:1}
  .lj-qnum{font-size:12px;color:#f4f4f4;font-weight:600;min-width:36px;text-align:center}
  .lj-cart{padding:1.1rem 1.2rem;background:#131314}
  .lj-cart-tit{font-size:14px;color:#f4f4f4;font-weight:600;margin-bottom:.8rem;display:flex;align-items:center;gap:7px}
  .lj-badge{background:#1d9e75;color:#fff;font-size:10px;padding:1px 7px;border-radius:10px;margin-left:auto}
  .lj-cart-item{display:flex;justify-content:space-between;font-size:11.5px;padding:6px 0;border-bottom:1px solid #1c1c1f}
  .lj-resumo{font-size:11.5px;color:#888780;line-height:2;margin-top:.7rem}
  .lj-resumo .ln{display:flex;justify-content:space-between}
  .lj-tot{border-top:1px solid #2a2a2b;margin-top:5px;padding-top:7px}
  .lj-falta{background:#2a1f10;border:1px solid #BA751744;border-radius:8px;padding:7px 9px;margin:.7rem 0;font-size:10.5px;color:#e0a23c}
  .lj-nota{background:#15301f;border:1px solid #1d9e75;border-radius:8px;padding:.55rem .65rem;margin:.7rem 0;font-size:11px;color:#9fe8c9;line-height:1.4}
  .lj-cta{display:block;text-align:center;width:100%;box-sizing:border-box;background:#1d9e75;color:#fff;border:0;border-radius:10px;padding:.75rem;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none}
  .lj-cta:disabled,.lj-cta.off{background:#2a2a2b;color:#888780;cursor:not-allowed}
  .lj-assinar{padding:1.2rem 1.3rem;max-width:560px}
  .lj-intro{font-size:12px;color:#9fbfb0;line-height:1.5;background:#131f1a;border:1px solid #1d9e7533;border-radius:10px;padding:.7rem .8rem;margin-bottom:1.2rem}
  .lj-tam{background:#161617;border:1px solid #232325;border-radius:13px;padding:.8rem;margin-bottom:8px;cursor:pointer;display:block}
  .lj-tam.sel{border:2px solid #1d9e75}
  .lj-tam-row{display:flex;justify-content:space-between;align-items:center}
  .lj-tam-ico{width:42px;height:42px;border-radius:10px;background:#1a2a1f;display:flex;align-items:center;justify-content:center;flex-shrink:0}
  .lj-freq{display:flex;gap:7px;margin-bottom:1.2rem}
  .lj-freq label{flex:1;text-align:center;background:#161617;color:#b4b2a9;border:1px solid #232325;border-radius:9px;padding:9px;font-size:11.5px;cursor:pointer}
  .lj-freq input{display:none}
  .lj-freq input:checked + span{color:#fff}
  .lj-freq label:has(input:checked){background:#1d9e75;border-color:#1d9e75}
  @media (max-width:680px){
    .lj-body{grid-template-columns:1fr}
    .lj-grade{grid-template-columns:1fr 1fr}
    .lj-vitrine{border-right:0;padding-bottom:.5rem}
    .lj-cart{position:sticky;bottom:0;border-top:1px solid #1d9e75;box-shadow:0 -8px 20px #0e0e0fcc}
  }
  @media (max-width:420px){ .lj-grade{grid-template-columns:1fr} }
</style>

<div class="lj-wrap">
  <div class="lj-header"{% if fornecedor.banner_url %} style="background-image:url('{{ fornecedor.banner_url }}')"{% elif fornecedor.banner_cor %} style="background:{{ fornecedor.banner_cor }}"{% endif %}>
    <div class="lj-aberto">● Aberto agora</div>
    <div class="lj-id">
      <div class="lj-logo">{{ fornecedor.nome[0]|upper }}</div>
      <div style="padding-bottom:22px">
        <div class="lj-nome">{{ fornecedor.nome }}</div>
        <div class="lj-info">🛵 40-60 min · Entrega R$ {{ "%.0f"|format((fornecedor.taxa_entrega_centavos or 0)/100) }} · Mín. R$ {{ "%.0f"|format((fornecedor.pedido_minimo_centavos or 0)/100) }}</div>
      </div>
    </div>
  </div>

  <div class="lj-abas">
    <button class="lj-aba on" id="t-comprar" onclick="ljAba('comprar')">Comprar</button>
    <button class="lj-aba" id="t-assinar" onclick="ljAba('assinar')">Assinar cesta</button>
    <a class="lj-aba" href="/f/{{ fornecedor.slug }}/promocoes">🏷️ Promoções</a>
    <a class="lj-aba" href="/painel/meus-pedidos">🧾 Meus pedidos</a>
  </div>

  <div id="ab-comprar" class="lj-body">
    <div class="lj-vitrine">
      <div class="lj-chips">
        <span class="lj-chip on" onclick="ljCat('tudo',this)">Tudo</span>
        {% for cat, itens in secoes %}<span class="lj-chip" onclick="ljCat('{{ cat }}',this)">{{ cat|capitalize }}s</span>{% endfor %}
      </div>
      {% for cat, itens in secoes %}
      <div class="lj-sec" data-cat="{{ cat }}">
        <div class="lj-sec-tit">{{ cat|capitalize }}s</div>
        <div class="lj-grade">
          {% for p in itens %}
          <div class="lj-card" data-pid="{{ p.id }}" data-unidade="{{ p.unidade }}">
            <div class="lj-foto"{% if p.foto_url %} style="background-image:url('{{ p.foto_url }}')"{% endif %}></div>
            <div class="lj-card-body">
              <div class="lj-pnome">{{ p.nome }}</div>
              {% if p.descricao_curta %}<div class="lj-pdesc">{{ p.descricao_curta }}</div>{% endif %}
              <div class="lj-prow">
                <div class="lj-preco">R$ {{ "%.2f"|format(p.preco_venda_centavos/100) }}<small>/{{ p.unidade }}</small></div>
                <div class="lj-acao" id="acao-{{ p.id }}"></div>
              </div>
            </div>
          </div>
          {% endfor %}
        </div>
      </div>
      {% endfor %}
    </div>
    <div class="lj-cart" id="lj-cart"></div>
  </div>

  <div id="ab-assinar" class="lj-assinar" style="display:none">
    <div class="lj-intro">🧺 Receba uma cesta fresca toda semana. Sem fidelidade – cancele quando quiser.</div>
    <form method="post" action="/f/{{ fornecedor.slug }}/assinar">
      <div class="lj-sec-tit">Escolha o tamanho</div>
      {% for t in tamanhos %}
      <label class="lj-tam{% if escolha.tamanho_id == t.id %} sel{% endif %}" onclick="ljTam(this)">
        <input type="radio" name="tamanho_id" value="{{ t.id }}" required style="display:none" {% if escolha.tamanho_id == t.id %}checked{% endif %}>
        <div class="lj-tam-row">
          <div style="display:flex;align-items:center;gap:10px">
            <div class="lj-tam-ico">🧺</div>
            <div>
              <div style="font-size:13.5px;color:#f4f4f4;font-weight:600">{{ t.nome }}</div>
              <div style="font-size:10.5px;color:#888780">{{ t.qtd_frutas }} frutas · {{ t.qtd_legumes }} legumes · {{ t.qtd_verduras }} verduras · {{ t.qtd_temperos }} temperos</div>
            </div>
          </div>
          <div style="font-size:14px;color:#5dcaa5;font-weight:600">R$ {{ "%.0f"|format(t.preco_centavos/100) }}</div>
        </div>
      </label>
      {% endfor %}
      <div class="lj-sec-tit" style="margin-top:1.2rem">Com que frequência?</div>
      <div class="lj-freq">
        <label><input type="radio" name="frequencia" value="semanal" {% if (escolha.frequencia or 'semanal')=='semanal' %}checked{% endif %}><span>Semanal</span></label>
        <label><input type="radio" name="frequencia" value="quinzenal" {% if escolha.frequencia=='quinzenal' %}checked{% endif %}><span>Quinzenal</span></label>
        <label><input type="radio" name="frequencia" value="mensal" {% if escolha.frequencia=='mensal' %}checked{% endif %}><span>Mensal</span></label>
      </div>
      <details style="margin-bottom:1.2rem">
        <summary style="font-size:11.5px;color:#888780;cursor:pointer">Algum item que você não quer receber? (opcional)</summary>
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:.7rem">
          {% for p in produtos %}<label style="font-size:11px;color:#b4b2a9;background:#161617;border:1px solid #232325;border-radius:16px;padding:4px 11px;cursor:pointer"><input type="checkbox" name="restricoes" value="{{ p.id }}" style="margin-right:4px" {% if p.id in (escolha.restricoes or []) %}checked{% endif %}>{{ p.nome }}</label>{% endfor %}
        </div>
      </details>
      <button class="lj-cta" type="submit">Assinar cesta</button>
      <div style="text-align:center;font-size:10.5px;color:#888780;margin-top:.6rem">Você só paga 4 dias antes de cada entrega</div>
    </form>
  </div>
</div>

<script>
let CART = {{ carrinho_json|safe }};
const SLUG = "{{ fornecedor.slug }}";
const inc = u => (['unidade','duzia','maco','bandeja','pacote'].includes(u) ? 1 : 0.5);
const brl = c => 'R$ ' + (c/100).toFixed(2).replace('.',',');

function ljAba(q){
  document.getElementById('ab-comprar').style.display = q==='comprar'?'grid':'none';
  document.getElementById('ab-assinar').style.display = q==='assinar'?'block':'none';
  document.getElementById('t-comprar').classList.toggle('on', q==='comprar');
  document.getElementById('t-assinar').classList.toggle('on', q==='assinar');
}
function ljCat(cat, el){
  document.querySelectorAll('.lj-sec').forEach(s=>{ s.style.display=(cat==='tudo'||s.dataset.cat===cat)?'block':'none'; });
  document.querySelectorAll('.lj-chip').forEach(c=>c.classList.remove('on'));
  el.classList.add('on');
}
function ljTam(el){
  document.querySelectorAll('.lj-tam').forEach(t=>t.classList.remove('sel'));
  el.classList.add('sel');
}
function renderCart(){
  const box=document.getElementById('lj-cart');
  let h='<div class="lj-cart-tit">🛒 Carrinho';
  if(CART.itens.length) h+='<span class="lj-badge">'+CART.itens.length+'</span>';
  h+='</div>';
  if(!CART.itens.length){ h+='<div style="font-size:12px;color:#888780">Carrinho vazio. Adicione produtos!</div>'; box.innerHTML=h; syncBtns(); return; }
  CART.itens.forEach(it=>{ h+='<div class="lj-cart-item"><span style="color:#b4b2a9">'+it.qtd+it.unidade+' '+it.nome+'</span><span style="color:#f4f4f4">'+brl(it.total)+'</span></div>'; });
  h+='<div class="lj-resumo"><div class="ln"><span>Subtotal</span><span style="color:#f4f4f4">'+brl(CART.subtotal)+'</span></div><div class="ln"><span>Entrega</span><span style="color:#f4f4f4">'+brl(CART.taxa)+'</span></div><div class="ln lj-tot"><span style="color:#f4f4f4;font-weight:600">Total</span><span style="color:#5dcaa5;font-weight:600">'+brl(CART.total)+'</span></div></div>';
  if(CART.minimo && CART.subtotal<CART.minimo){
    h+='<div class="lj-falta">Faltam '+brl(CART.minimo-CART.subtotal)+' pro mínimo</div><button class="lj-cta off" disabled>Enviar pedido</button>';
  }else{
    h+='<div class="lj-nota">ℹ️ Confirma e paga depois. Sem cobrança antes.</div><a class="lj-cta" href="/f/'+SLUG+'/carrinho/revisar">Ver carrinho</a>';
  }
  box.innerHTML=h; syncBtns();
}
function syncBtns(){
  document.querySelectorAll('.lj-card').forEach(card=>{
    const pid=parseInt(card.dataset.pid), u=card.dataset.unidade;
    const it=CART.itens.find(i=>i.produto_id===pid);
    const acao=card.querySelector('.lj-acao');
    if(it){ acao.innerHTML='<div class="lj-qtd"><button class="lj-qbtn" onclick="ljQtd('+pid+',-1)">−</button><span class="lj-qnum">'+it.qtd+it.unidade+'</span><button class="lj-qbtn" onclick="ljQtd('+pid+',1)">+</button></div>'; }
    else{ acao.innerHTML='<button class="lj-add" onclick="ljAdd('+pid+",'"+u+"')"+'">+Add</button>'; }
  });
}
async function ljAdd(pid,u){
  try{ const r=await fetch('/f/'+SLUG+'/carrinho/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({produto_id:pid,quantidade:inc(u)})}); CART=await r.json(); renderCart(); }catch(e){ console.error(e); }
}
async function ljQtd(pid,dir){
  const it=CART.itens.find(i=>i.produto_id===pid); if(!it)return;
  const nova=it.qtd+dir*inc(it.unidade);
  try{ const r=await fetch('/f/'+SLUG+'/carrinho/qtd',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({produto_id:pid,quantidade:nova<=0?0:nova})}); CART=await r.json(); renderCart(); }catch(e){ console.error(e); }
}
renderCart();
</script>
{% endblock %}'''

# Substituir _LOJA
pattern = r'_LOJA = """.*?{% endblock %}"""'
content_novo = re.sub(pattern, f'_LOJA = """{novo_loja}"""', content, flags=re.DOTALL)

# Salvar
with open('web/portal.py', 'w', encoding='utf-8') as f:
    f.write(content_novo)

print("OK: Template _LOJA substituido!")
