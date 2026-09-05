# Regras desta base

## 0. Nada do cliente pode se perder

Regra do dono, dada em 22/08/2026:

> "não pode perder conexão ou perder nada, os clientes, tipo zaq-waqr, nem canal,
> nem informação, nem nada pode ser [perdido]"

Vale pra **tudo que é do cliente**, não só pra sessão de WhatsApp:

* **Conexão** — o socket do `zaq-waqr`, o pareamento, o cofre `wa_qr_auth`.
* **Canal** — a linha em `canais_config`, o chip, o número.
* **Informação** — `conversas`, `mensagens`, `prospeccao`, `contas`, `membros`,
  `clientes`, `eventos_agenda`, `orcamentos`, `titulos`, `lancamentos`.

Não existe "só pra testar", "depois recria" ou "é rápido". Se uma ação pode
apagar, sobrescrever, truncar ou derrubar qualquer uma dessas coisas, ela **não é
feita** — nem pra diagnosticar, nem pra validar hipótese. As duas seções abaixo
são os dois jeitos concretos pelos quais isso já quase aconteceu.

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

## 2. O banco de produção não é banco de teste

Em 22/08/2026, das 17:07 às 19:41 UTC, a suíte de testes e os replays de migração
rodaram **contra o banco de produção**. Efeito: cada replay toma `ACCESS
EXCLUSIVE` em `contas`, `membros`, `clientes`, `conversas`, `orcamentos` — e o
painel inteiro ficou lento pra todos os clientes ao mesmo tempo. O teardown
chegou a tentar **apagar `membros` de produção**; só não apagou porque a foreign
key `prospeccao_vendedor_id_fkey` barrou.

O buraco era a trava do `tests/conftest.py`: a checagem de produção era
`if prod_url and _host(test) == _host(prod)` — só valia **quando `DATABASE_URL`
existia**. Em máquina sem `DATABASE_URL` (container de dev, sessão de agente),
`TEST_DATABASE_URL` podia apontar pro banco vivo e nada disparava.

A trava agora falha fechada, em quatro portões (ver o docstring do conftest):

1. Sem `TEST_DATABASE_URL` → aborta.
2. Mesmo host de `DATABASE_URL` → aborta.
3. Qualquer referência de projeto de produção na URL de teste (`REFS_PRODUCAO`,
   no host **ou** no usuário do pooler) → aborta **sempre**, com ou sem
   `DATABASE_URL`. **Este portão não tem escape.**
4. Banco remoto sem marca de teste no nome → aborta; escape explícito em
   `PERMITIR_BANCO_NAO_MARCADO=SIM`, que **não** libera o portão 3.

`tests/test_trava_banco_producao.py` fixa esse comportamento — inclusive o caso
exato do incidente (produção sem `DATABASE_URL` setada).

Na prática, pra quem for rodar qualquer coisa contra um banco:

* Teste roda em Postgres descartável. O CI sobe `postgres:16` em `localhost` com
  o banco `openclaw_test` — copie isso.
* `python -m db.aplicar_migracoes` em produção é do `preDeployCommand` do Render.
  Não se roda na mão "pra conferir": as migrações são DDL e travam as tabelas
  vivas.
* Ao adicionar um projeto Supabase novo, a referência dele entra em
  `REFS_PRODUCAO` no conftest **antes** de qualquer teste rodar.

## 3. Este repositório

`render.yaml` é **documentação**, não Blueprint — os serviços do Render são
gerenciados na mão. Mudar o arquivo não muda o Render.

Os testes exigem `TEST_DATABASE_URL` (trava do `tests/conftest.py`, nascida de um
incidente em que o pytest apagou produção). Os testes do serviço Node são arquivos
`teste-*.js` soltos em `services/wa-qr`, rodados na mão com `node`.

## 4. Depois de mesclar, a branch recomeça da main

Regra do dono, dada em 27/08/2026, depois de tropeçar duas vezes seguidas na mesma
pedra:

> "sim, faz assim por padrão"

Os PRs deste repositório são mesclados com **squash**. O squash cria na `main` um
commit NOVO com o mesmo conteúdo — e a branch continua com os commits originais.
Pro git isso é história divergente.

O sintoma é traiçoeiro porque **não é um erro**: o PR seguinte nasce em conflito
(`mergeable_state: dirty`), e **o GitHub não roda CI em PR conflitado**. Nenhuma
mensagem, nenhum check vermelho — o `pytest` simplesmente nunca aparece, e quem
está olhando acha que é lentidão. Aconteceu no #576 e de novo no #577, nos dois
casos custando um ciclo até alguém desconfiar.

**Então, ao começar trabalho novo depois de um merge:**

```
git fetch origin main && git checkout -B <branch> origin/main
```

O nome da branch continua o mesmo (é o que as instruções da sessão exigem); o que
muda é a base. Se a branch já tiver commit NÃO mesclado, ele é rebaseado por cima
da main nova — nunca descartado:

```
git rebase --onto origin/main <ultimo-commit-ja-mesclado>
```

**E falta um passo, que só apareceu ao fazer isso pela primeira vez:** recomeçar
a branch mexe só no local. A branch REMOTA continua apontando pro estado
pré-merge — e é ela que a próxima sessão vai buscar. Sem fechar o ciclo, o
problema volta na rodada seguinte.

```
git push --force-with-lease -u origin <branch>
```

O `--force-with-lease` aqui é seguro porque a remota carrega **só história já
mesclada** — o squash levou tudo pra main. Confira antes, e o comando é este; diff
vazio quer dizer que o conteúdo está inteiro na main e nada se perde:

```
git diff origin/<branch> origin/main
```

Se esse diff NÃO vier vazio, pare: há trabalho na remota que não entrou na main.
Aí é `rebase --onto` (acima) pra salvar o que sobrou, nunca force.

E o sinal de que se caiu nisso, pra reconhecer rápido: o PR mostra **mais arquivos
e mais commits do que a mudança tem**, porque está remontando o que já entrou.

Um segundo sinal, mais tardio: o CI fica “rodando” e nunca termina, ou o check do
pytest simplesmente não aparece na lista. Antes de esperar mais, confira o
`mergeable_state` do PR — `dirty` é isto aqui, não lentidão.

## 5. PR que muda tela leva o aviso

Regra do dono, dada em 05/09/2026, ao aprovar o mockup das Novidades em três
lugares (`docs/mockups/novidades_tres_lugares.html`):

O painel tem a tela Novidades desde 16/08 (`finance/novidades.py`, migração 174),
com mira por público. Entre 19/08 e 05/09 ela ficou **17 dias sem aviso** —
inclusive nas quatro entregas do funil de 05/09 (#622, #625, #626, #627), que
mudaram o quadro de todo mundo e a Fila do vendedor sem ninguém ser avisado. Foi
o autor do sistema de avisos que esqueceu de usá-lo.

**Então, todo PR que muda o que uma pessoa vê ou faz na tela leva o aviso no
mesmo PR**, como migração `NNN_novidade_<chave>.sql`, seguindo a receita da 175:

1. Nomeie o portão que decide quem recebeu a mudança (`publico`). Se nenhum dos
   portões de `finance.novidades.PUBLICOS` descreve o alcance, crie o portão no
   código primeiro — aviso é o teste da própria gatilhagem.
2. Diga **pra quem** é, por papel (`pra_quem`: dono, gestor, vendedor). O vendedor
   só recebe o que muda a rotina dele; aviso de tela que ele não tem, nunca.
3. Escreva o **resumo** (uma linha, tom de fora: é o que vai pro site em
   `zaq-ia.com/atualizacoes`, via `GET /novidades.json`) separado do **corpo**
   (fala com quem já usa). Aviso sem resumo não sai no site — é assim que um
   aviso interno não vira público por esquecimento.
4. `link` aponta pra tela que mudou ("Ver como ficou").
5. A lista de quem recebe, por nome, vai no corpo do PR (`contas_alcancadas`).

O que **não** precisa de aviso: correção que não muda tela, teste, mockup,
documentação, mudança de serviço interno. Na dúvida, é 'novidade' com público
'todos'.
