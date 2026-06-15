# Função que falta no contas.py do Render: resgatar_convite

## Diagnóstico confirmado
O contas/contas.py do Render tem 225 linhas e SÓ tem `gerar_convite_para` (linha 181).
Falta a função `resgatar_convite`, que o telegram_bot.py CHAMA (linha 88) — por isso o
AttributeError. A função nunca foi commitada no repositório real.

**ATENÇÃO:** os arquivos divergiram (no Render `gerar_convite_para` está na linha 181;
em outras cópias está mais embaixo). Por isso NÃO substituir o arquivo inteiro — só
ADICIONAR a função que falta. A outra ferramenta, que conhece a organização real do
arquivo, cola no lugar certo.

---

## Função pra ADICIONAR no contas/contas.py (território da outra ferramenta)

Colar esta função em qualquer ponto do contas/contas.py (entre duas funções de
nível de módulo, ex: logo antes ou depois de `gerar_convite_para`):

```python
def resgatar_convite(pool, codigo: str, telegram_id: int) -> tuple[bool, str]:
    """A pessoa digitou o codigo no Telegram. Vincula o telegram_id dela ao
    membro pendente. Retorna (ok, mensagem). Idempotente e seguro:
    - codigo invalido -> (False, ...)
    - codigo ja' usado -> (False, ...)
    - telegram ja' vinculado a outra conta -> (False, ...)
    """
    codigo = (codigo or "").strip().upper()
    if not codigo:
        return False, "Codigo vazio."
    with pool.connection() as conn:
        ja = conn.execute("select 1 from membros where telegram_id=%s", (telegram_id,)).fetchone()
        if ja:
            return False, "Esse Telegram ja' esta vinculado a uma conta."
        m = conn.execute(
            "select id, conta_id, nome, papel from membros where codigo_convite=%s and telegram_id is null",
            (codigo,),
        ).fetchone()
        if not m:
            return False, "Codigo invalido ou ja' utilizado."
        conn.execute(
            "update membros set telegram_id=%s, codigo_convite=null, ativo=true where id=%s",
            (telegram_id, m[0]),
        )
        conn.commit()
    registrar_evento(pool, m[1], "convite_resgatado", f"{m[2]} vinculou Telegram", membro_id=m[0])
    return True, f"Pronto, {m[2] or 'tudo certo'}! Voce foi vinculado a' conta."
```

---

## Dependências (confirmar que existem no contas.py do Render)

### 1) A função usa registrar_evento(pool, conta_id, tipo, descricao, membro_id=...)
Confirmar que existe:

```bash
grep -n "def registrar_evento" contas/contas.py
```

Se NÃO existir, avisar pra entregar ela também.

### 2) A tabela membros precisa ter as colunas: telegram_id, codigo_convite, ativo
Confirmar:

```bash
psql $DATABASE_URL -c "\d membros" | grep -E "telegram_id|codigo_convite|ativo"
```

Se faltar alguma coluna, rodar a migração correspondente.

---

## Depois de adicionar

1. **Commit + push** do arquivo atualizado.
2. **Render faz deploy** (ou forçar "Manual Deploy" / "Clear build cache & deploy" no painel).
3. **RESTART do serviço openclaw-bot** (pra carregar o código novo na memória do processo).
4. **Teste:** Mary manda `THO-4632` no bot → deve responder 
   `"Pronto, Mary! Voce foi vinculado a' conta."`
