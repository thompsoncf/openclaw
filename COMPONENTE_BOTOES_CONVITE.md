# Componente: botões de convite (Telegram + WhatsApp) no painel do cliente

Pronto pra plugar no `web/portal.py` (território da outra ferramenta), no card de
cada pessoa que tem `codigo_convite`. Mostra:
- Botão **Telegram** funcional: abre o chat do bot com o código já no campo.
- Botão **Copiar link** (pro cliente mandar por onde quiser).
- Botão **WhatsApp** desligado ("em breve") até ter número oficial.

## 1) Helper pra montar o link (pode ir em web/portal.py ou num util)

```python
import urllib.parse

BOT_TELEGRAM = "clawaladdin_bot"  # @CLAWALADDIN_BOT (username real do bot)

def link_convite_telegram(codigo: str) -> str:
    """Abre o chat do bot com o código já escrito no campo (a pessoa só envia)."""
    return f"https://t.me/{BOT_TELEGRAM}?text={urllib.parse.quote(codigo or '')}"

# Para quando o WhatsApp oficial estiver aprovado:
# def link_convite_whatsapp(numero_oficial: str, codigo: str) -> str:
#     digs = "".join(c for c in numero_oficial if c.isdigit())
#     return f"https://wa.me/{digs}?text={urllib.parse.quote(codigo or '')}"
```

## 2) Snippet Jinja pro card da pessoa (dentro do {% for membro %})

Onde hoje aparece o código (ex: `🔑 THO-4632`), trocar/complementar por:

```html
{% if m.codigo_convite %}
<div class="convite-box">
  <div class="convite-cod">🔑 <code>{{ m.codigo_convite }}</code></div>
  <div class="convite-acoes">
    <a class="btn-conv btn-tg"
       href="https://t.me/clawaladdin_bot?text={{ m.codigo_convite|urlencode }}"
       target="_blank" rel="noopener">📨 Convidar no Telegram</a>
    <button class="btn-conv btn-copy"
       onclick="copiarConvite(this, 'https://t.me/clawaladdin_bot?text={{ m.codigo_convite|urlencode }}')">
       🔗 Copiar link</button>
    <span class="btn-conv btn-wa-soon" title="Disponível quando o WhatsApp oficial for aprovado">
       🟢 WhatsApp (em breve)</span>
  </div>
  <div class="convite-dica">A pessoa abre o link, o código já vem preenchido — é só enviar. ✅</div>
</div>
{% endif %}
```

## 3) CSS (somar ao <style> do painel)

```css
.convite-box{margin-top:.6rem;padding:.6rem;border:1px solid #243049;border-radius:10px;background:#0e1525}
.convite-cod{font-size:.9rem;margin-bottom:.5rem}
.convite-cod code{background:#1a2233;padding:.1rem .4rem;border-radius:6px;letter-spacing:.05em}
.convite-acoes{display:flex;gap:.5rem;flex-wrap:wrap}
.btn-conv{padding:.45rem .8rem;border-radius:8px;font-size:.85rem;border:1px solid #243049;cursor:pointer;text-decoration:none;display:inline-block}
.btn-tg{background:#2AABEE;color:#fff;border-color:#2AABEE}
.btn-copy{background:#1a2233;color:#e7ecf3}
.btn-wa-soon{background:#11201a;color:#5a6b62;border-color:#1e3a2e;cursor:not-allowed}
.convite-dica{font-size:.78rem;color:#8b97a8;margin-top:.5rem}
```

## 4) JS pra copiar (somar ao <script> do painel)

```javascript
function copiarConvite(btn, url){
  navigator.clipboard.writeText(url).then(function(){
    var txt = btn.textContent;
    btn.textContent = '✅ Copiado!';
    setTimeout(function(){ btn.textContent = txt; }, 1500);
  }).catch(function(){
    // fallback: seleciona um prompt
    window.prompt('Copie o link:', url);
  });
}
```

## Quando o WhatsApp oficial sair
1. Trocar o `<span class="btn-wa-soon">` por um `<a>` com
   `href="https://wa.me/NUMEROOFICIAL?text={{ m.codigo_convite|urlencode }}"`.
2. NÃO usar o número do sandbox Twilio (+1 555...) — link wa.me não funciona em
   sandbox (exige "join" antes). Só plugar quando tiver número Business aprovado.

## Notas
- O username REAL do bot é `clawaladdin_bot` (o "ClawIAOpen" era só o nome de
  exibição do chat). O link t.me usa o username.
- `?text=` funciona hoje sem mexer no bot. Se quiser envio AUTOMÁTICO (sem a
  pessoa apertar enviar), seria `?start=CODIGO` + ajuste no telegram_bot.py pra
  ler o parâmetro do /start (território da outra ferramenta).
