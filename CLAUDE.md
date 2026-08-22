# Regras desta base

## 1. Não encoste em sessão, canal ou conexão que está saudável

Regra do dono, dada em 22/08/2026 e válida pra sempre:

> "não pode mexer em nada de sessão ou canal ou conexão que já existe saudável.
> sempre pense antes de fazer algo e prejudicar isso"

O que isso proíbe, na prática:

* **Nunca** apagar, reescrever ou "limpar" `wa_qr_auth` (o cofre das credenciais),
  `wa_qr_sessao_lock` (a trava) ou `wa_qr_sessao_estado` de uma conta que está
  conectada. Apagar o cofre é irreversível: obriga a parear de novo, e o dono tem
  que estar com o celular na mão.
* **Nunca** derrubar, religar ou "reiniciar pra testar" um socket que está
  entregando mensagem. Um chip de pé é receita entrando.
* **Nunca** mexer em `canais_config.ativo` / `desconectado_em` pra "corrigir" um
  estado que parece errado na tela. A tela pode estar errada; a conexão não é a
  tela.
* Mudança em `services/wa-qr` que toque conexão, reconexão, trava, disjuntor ou
  vigia tem que ser justificada por dado de produção — e, na dúvida, não é feita.

O que é permitido sem cerimônia: **leitura**. `wa_qr_log`, `mensagens`,
`conversas`, `render_evento` — olhar não quebra nada, e é por onde todo
diagnóstico deve começar.

### Por que a regra existe

Em 22/08/2026 a conta 23 ficou 19h sem mensagem de entrada na tabela `mensagens`.
Diagnostiquei "sessão quebrada, religar não resolve, precisa parear de novo". Duas
coisas estavam erradas no diagnóstico:

1. **"0 entradas em `mensagens`" não é "não está recebendo".** O serviço descarta
   de propósito grupo, canal e status (`entrada ignorada (sem texto, grupo, canal
   ou status)`). Naquelas 19h estava chegando tráfego o tempo todo — era tudo
   grupo. O sinal certo é `entrada repassada ao webhook ✓` no `wa_qr_log`, não a
   contagem em `mensagens`.
2. **`failed to decrypt message` com `fromMe: true` é ruído**, não perda: é o eco
   das mensagens que o próprio dono mandou pelo celular. Convive com a conta
   funcionando.

A conta voltou sozinha 10 minutos depois do meu diagnóstico. O pareamento que eu
recomendei apagou 9.714 linhas do cofre e custou uma manhã de trabalho — sem
necessidade nenhuma.

### O checklist antes de mandar alguém desconectar

1. Tem `entrada repassada ao webhook ✓` no `wa_qr_log` nas últimas horas?
   Se tem, a conta **está recebendo** — o problema é outro.
2. As falhas de decifragem são `fromMe: true` ou `false`? Só `false` é mensagem de
   cliente. E dessas, quantas são ids DISTINTOS? (o retry repete o mesmo id).
3. As mensagens que falharam chegaram depois? Cruze `dados->'key'->>'id'` com
   `mensagens.provider_sid` — em 21/08 as 38 da Prime chegaram todas.
4. Só depois de 1, 2 e 3 apontarem perda real é que se fala em parear de novo.

## 2. Este repositório

`render.yaml` é **documentação**, não Blueprint — os serviços do Render são
gerenciados na mão. Mudar o arquivo não muda o Render.

Os testes exigem `TEST_DATABASE_URL` (trava do `tests/conftest.py`, nascida de um
incidente em que o pytest apagou produção). Os testes do serviço Node são arquivos
`teste-*.js` soltos em `services/wa-qr`, rodados na mão com `node`.
