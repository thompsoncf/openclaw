# Diagnóstico: por que o bot não aceitou o código no Telegram

## Causa raiz (DOIS problemas somados)

### Problema 1 – `_parece_convite` não existe (CRÍTICO)
No `telegram_bot.py` linha 82, `_parece_convite(possivel)` é chamada mas a função
NUNCA foi definida nem importada. Resultado: ao receber QUALQUER código de convite,
o bot quebra com NameError e não responde nada.

**Correção** (telegram_bot.py – território da outra ferramenta): adicionar no topo,
após os imports (com `import re`):

```python
import re

def _parece_convite(texto: str) -> bool:
    """True se o texto tem cara de codigo de convite: 2-4 alfanum, hifen, 3-6 alfanum.
    Ex: THO-4632, LAR-7K2M. Evita acionar o resgate em mensagens normais."""
    t = (texto or "").strip().upper()
    return bool(re.fullmatch(r"[A-Z0-9]{2,4}-[A-Z0-9]{3,6}", t))
```
Testado: 9/9 (THO-4632 e LAR-7K2M passam; "oi","/start","quanto gastei" não).

### Problema 2 – confusão O (letra) vs 0 (zero) – design
O prefixo do código vem do NOME da conta: "Thompson" -> "THO" (com LETRA O).
No portal, a fonte renderiza o "O" parecendo zero ("TH0"). O cliente não sabe
se digita letra O ou zero. Isso vai gerar MUITO suporte.

Obs: no caso do Thompson, o código real é THO-4632 (letra O), e foi o que ele
digitou – então após o Problema 1 corrigido, o resgate dele JÁ funciona. Mas a
ambiguidade vai pegar outros clientes.

**Correção de design recomendada** (contas/contas.py, função que gera o código –
território da outra ferramenta): gerar SEM caracteres ambíguos (sem O,0,I,1,L).

```python
import secrets
_ALFABETO_SEGURO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sem O 0 I 1 L

def _sufixo_seguro(n=4):
    return "".join(secrets.choice(_ALFABETO_SEGURO) for _ in range(n))
```
E o prefixo: em vez de usar 3 letras do nome (que pode ter O/I/L), usar também
o alfabeto seguro, OU filtrar ambíguos do prefixo do nome. Assim nenhum código
gerado terá caracteres confundíveis.

Para tolerância retroativa, o `resgatar_convite` pode normalizar a entrada do
usuário: trocar O->0 ou 0->O não resolve sozinho; melhor é validar comparando de
forma tolerante. Mas o caminho limpo é gerar códigos novos sem ambíguos daqui pra
frente (o botão "convite" regenera).

## Para o Thompson AGORA
1. Outra ferramenta aplica o Problema 1 (a função `_parece_convite`).
2. Thompson manda no bot exatamente: THO-4632 (a fonte do portal engana, mas é
   letra O). Após o patch, o resgate funciona.
3. Se não funcionar, no Render Shell confirmar o valor EXATO:
   ```bash
   psql $DATABASE_URL -c "select nome, codigo_convite from membros where codigo_convite ilike 'TH_-4632'"
   ```
   (o ilike com _ casa tanto O quanto 0 na 3ª posição.)
