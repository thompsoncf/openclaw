# Trecho EXATO pra plugar os botões de convite no painel (web/portal.py)

Território da outra ferramenta. São 3 mudanças cirúrgicas no `web/portal.py`.
Testado: renderiza certo com código, fica vazio sem código, não quebra.

---

## MUDANÇA 1 — substituir a linha do código de convite (~linha 235)

PROCURAR esta linha exata:
```html
{% if m[5] %}<div class="conv">🔑 <code>{{ m[5] }}</code> <span class="mut">(Telegram)</span></div>{% endif %}
```

SUBSTITUIR por:
```html
{% if m[5] %}<div class="conv">🔑 <code>{{ m[5] }}</code> <span class="mut">(Telegram)</span>
<div class="conv-links">
<a class="lk-tg" href="https://t.me/clawaladdin_bot?text={{ m[5]|urlencode }}" target="_blank" rel="noopener">📨 Convidar no Telegram</a>
<button type="button" class="lk-copy" onclick="copiarConvite(this, 'https://t.me/clawaladdin_bot?text={{ m[5]|urlencode }}')">🔗 Copiar link</button>
<span class="lk-wa" title="Disponível quando o WhatsApp oficial for aprovado">🟢 WhatsApp (em breve)</span>
</div>
</div>{% endif %}
```

---

## MUDANÇA 2 — somar o CSS (junto dos outros .membro-* / .conv, ~linha 154)

ADICIONAR estas regras no bloco <style> do painel:
```css
.conv-links{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.lk-tg,.lk-copy,.lk-wa{padding:.4rem .7rem;border-radius:8px;font-size:.8rem;border:1px solid #243049;text-decoration:none;display:inline-block;line-height:1}
.lk-tg{background:#2AABEE;color:#fff;border-color:#2AABEE}
.lk-copy{background:#1a2233;color:#e7ecf3;cursor:pointer}
.lk-wa{background:#11201a;color:#5a6b62;border-color:#1e3a2e;cursor:not-allowed}
```

---

## MUDANÇA 3 — somar o JS de copiar (no <script> do painel, ou antes do </body>)

ADICIONAR:
```javascript
function copiarConvite(btn, url){
  navigator.clipboard.writeText(url).then(function(){
    var txt = btn.textContent;
    btn.textContent = '✅ Copiado!';
    setTimeout(function(){ btn.textContent = txt; }, 1500);
  }).catch(function(){ window.prompt('Copie o link:', url); });
}
```

---

## Resultado
No card de cada pessoa COM código de convite (ex: Mary, THO-4632), aparecem 3
botões abaixo do código:
- 📨 Convidar no Telegram → abre t.me/clawaladdin_bot com o código no campo
- 🔗 Copiar link → copia a URL pro cliente mandar por onde quiser
- 🟢 WhatsApp (em breve) → desligado até ter número oficial

O dono (titular) não tem código, então não mostra botões pra ele — correto.

## Detalhes importantes
- username do bot = `clawaladdin_bot` (confirmado pelo QR; "ClawIAOpen" era só
  nome de exibição).
- `|urlencode` é filtro nativo do Jinja — encoda o código com segurança.
- NÃO usar o número sandbox Twilio (+1 555...) no WhatsApp — link wa.me não
  funciona em sandbox. Só plugar quando tiver número Business oficial: trocar o
  `<span class="lk-wa">` por `<a href="https://wa.me/NUMERO?text={{ m[5]|urlencode }}">`.
- Opcional futuro: a mensagem fixa do painel ("O código serve pro Telegram...")
  pode ser atualizada pra mencionar que agora é só clicar no botão.
