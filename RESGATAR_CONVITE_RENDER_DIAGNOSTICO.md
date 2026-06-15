# Erro: AttributeError resgatar_convite no Render

## Progresso
O erro MUDOU (bom sinal): antes era `_parece_convite` faltando (NameError na
linha 82). Agora passou da linha 82 e chega na 88 — ou seja, `_parece_convite`
JÁ foi corrigido. O novo erro é:

```
File "telegram_bot.py", line 88, in _processar
    ok, msg = ct.resgatar_convite(_pool, possivel, ...)
AttributeError: module 'contas.contas' has no attribute 'resgatar_convite'
```

## Causa
A função `resgatar_convite` EXISTE no `contas/contas.py` (linha 205, confirmado).
Mas o Render diz que não existe. Isso = o código rodando no Render está
DESATUALIZADO (deploy antigo ou arquivo diferente do repositório).

## Diagnóstico (rodar no Render Shell)

1) Ver se a função está no arquivo que o Render tem:
```bash
grep -n "def resgatar_convite" /opt/render/project/src/contas/contas.py
```
- Se NÃO aparecer → o arquivo no Render é antigo. Precisa redeploy/git pull.
- Se aparecer → o processo do bot está rodando código velho em memória (precisa
  restart do serviço, não só do shell).

2) Ver o que o Python REALMENTE carregou (pega o processo real):
```bash
python -c "from contas import contas as ct; import inspect; print(hasattr(ct,'resgatar_convite')); print(inspect.getfile(ct))"
```
- `False` → a versão importada não tem a função. Confirma desatualização.
- Mostra o caminho do arquivo que está sendo usado.

3) Ver o commit que o Render está rodando:
```bash
cd /opt/render/project/src && git log --oneline -1
```
Comparar com o último commit que você fez (que tem o resgatar_convite).

## Correção (território da outra ferramenta)
- Se o arquivo está antigo: garantir que o último commit (com resgatar_convite)
  foi pro main e o Render fez deploy. Forçar "Manual Deploy" / "Clear build cache
  & deploy" no painel do Render.
- Se o arquivo está certo mas o processo é velho: RESTART do serviço
  openclaw-bot (o worker do Telegram) no Render — o bot fica com o módulo
  carregado em memória; mudança em arquivo só vale após restart do processo.

## Teste depois
Mary manda THO-4632 no bot → deve responder algo como
"Pronto, Mary! Você foi vinculado à conta."
