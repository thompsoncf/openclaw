# ZAQ · Serviço WhatsApp por QR Code (Baileys)

Serviço Node **à parte, sempre ligado**, que mantém sessões de WhatsApp via QR
Code (tipo WhatsApp Web) por empresa (`conta_id`). O app Python (web) fala com ele
por HTTP; o estado da sessão fica no **mesmo Postgres** (tabela `wa_qr_auth`,
migração `097`), então sobrevive a restart sem disco persistente.

> ⚠️ **Risco:** conectar por QR usa a automação não-oficial do WhatsApp e **viola
> os termos** — o número do cliente pode ser **banido**. A via oficial sem esse
> risco é a **Cloud API** (aba Canais → "Número próprio"). Use o QR só quando o
> cliente recusar migrar o número e aceitar o risco.

## Como funciona

- Multi-tenant: uma sessão por `conta_id`, sob demanda.
- `POST /session/:conta/iniciar` liga a sessão e devolve o **QR** (data URL) pra
  exibir no painel; o cliente escaneia com o celular dele.
- Mensagens que chegam viram `POST ${APP_URL}/webhooks/wa-qr` (com o segredo), e o
  ZAQ trata como lead + agente, igual aos outros canais.
- Envio: `POST /session/:conta/enviar` com `{numero, texto}`.

Todas as rotas (menos `GET /saude`) exigem o header `x-wa-secret` = `WA_QR_SHARED_SECRET`.

## Variáveis de ambiente

| Var | Descrição |
|-----|-----------|
| `DATABASE_URL` | mesmo Postgres do app (guarda a sessão em `wa_qr_auth`) |
| `WA_QR_SHARED_SECRET` | segredo compartilhado com o web (string aleatória) |
| `APP_URL` | URL pública do web (ex.: `https://openclaw-web-bcu3.onrender.com`) — pra onde as mensagens recebidas são repassadas |
| `PORT` | porta HTTP (o Render injeta) |
| `WA_QR_LOG_DB` | `0` desliga o espelho do log no Postgres (padrão ligado) |
| `WA_QR_MUDO_LIMITE_MS` · `WA_QR_MUDO_TETO_MS` | vigia da sessão muda: quando pingar (10min) e quando religar mesmo com ping voltando (45min) |
| `WA_QR_DECIFRAR_TETO` · `WA_QR_DECIFRAR_JANELA_MS` | disjuntor da guerra de sessão: quantas falhas ao decifrar numa janela derrubam a conta (60 em 60s) |
| `WA_QR_ESPERA_POS_440_MS` | base da espera pra retomar conta substituída — dobra a cada tentativa (5, 10, 20, 40, 80min) |
| `WA_QR_IGNORAR_GRUPOS` | `0` volta a decifrar mensagem de grupo (padrão: cortada antes de decifrar — ver "Grupo não é decifrado") |
| `WA_QR_HIST_CONCORRENCIA` · `WA_QR_HIST_PAUSA_MS` · `WA_QR_HIST_RECUO_MS` | vazão do repasse do histórico: conversas em paralelo (2), pausa entre POSTs (100ms) e recuo quando o web recusa (2s) — ver "O histórico derrubou o web" |
| `WA_QR_FILA_DRENA_MS` · `WA_QR_FILA_PARAR_EM` · `WA_QR_FILA_TIMEOUT_MS` | outbox do repasse: de quanto em quanto o que falhou volta (15s), quantas tentativas até virar dead-letter (12, ~6h) e timeout do POST (15s) — ver "Nenhuma mensagem se perde num 502" |
| `WA_QR_BAILEYS6_CONTAS` | ids que ficam no Baileys **6.7.24**. Vazia por padrão: desde 16/09 todo mundo roda no 7 — ver "O Baileys 7 é o padrão" |

## Diagnóstico sem abrir o dashboard

O log do Render **não se lê de fora**: o dashboard exige sessão de navegador e
`api.render.com` cai em 403 na política de egresso do ambiente do agente. Num
chamado real isso custou horas — dava pra provar pelo banco QUE uma sessão tinha
emudecido, e não POR QUÊ. Por isso o serviço escreve também no Postgres:

```sql
-- o que aconteceu com a conta 35 na última hora
select criado_em, nivel, msg, dados from wa_qr_log
 where conta_id = 35 and criado_em > now() - interval '1 hour' order by id;

-- o que cada sessão diz de si mesma AGORA (status vem da memória do processo)
select conta_id, status, mudo_s, religamentos, atualizado from wa_qr_sessao_estado
 order by mudo_s desc nulls last;
```

`mudo_s` alto com `status='conectado'` é o retrato da sessão que emudeceu sem
cair — o vigia religa sozinho, e `religamentos` conta quantas tentativas não
trouxeram nada de volta. Retenção do log: 48h (é ferramenta de diagnóstico, não
arquivo). Do Baileys só `error`/`fatal` são espelhados; `debug`/`trace`, nunca.

No **web** (app Python), configure também:

| Var | Descrição |
|-----|-----------|
| `WA_QR_SERVICE_URL` | URL pública deste serviço (ex.: `https://zaq-waqr.onrender.com`) |
| `WA_QR_SHARED_SECRET` | **o mesmo** segredo daqui |

## Deploy no Render (serviço novo, manual)

1. **New → Web Service**, aponte pro mesmo repo, **Root Directory** `services/wa-qr`.
2. Runtime **Node**. Build: `npm install`. Start: `npm start`. Health check path: `/saude`.
3. Plano com **1 instância** (a sessão é stateful em memória; não escale horizontal).
4. Env vars acima. Deploy.
5. No **web**, adicione `WA_QR_SERVICE_URL` + `WA_QR_SHARED_SECRET` e redeploy.
6. No painel: Canais → WhatsApp → **QR Code** → **Gerar QR** → escaneie no celular.

## A conta é a MESMA vindo do banco ou da rota

`conta_id` chega por dois caminhos com tipos diferentes: as rotas fazem `parseInt`
(número) e o `restaurarSessoes` lê do Postgres, que devolve `bigint` como **string**.
Todo mapa indexado por conta tem que normalizar — é o que a classe `MapaPorConta`
faz, e o que `sessao-lock.js` já fazia com o `num()` dele.

Sem isso o serviço mente sobre si mesmo. Medido em 21/08/2026: o painel da Prime
mostrava o chip 2 (conta 36) como **desconectado** enquanto ele recebia 25 mensagens
em 3 horas. `GET /session/36/status` respondia `desconectado` e a `wa_qr_sessao_estado`,
escrita pelo mesmo processo percorrendo o mesmo mapa no mesmo minuto, dizia
`conectado`. A sessão tinha sido religada como `'36'` e a rota procurava `36`.

E o `/enviar` erra igual — só que ele religa em vez de desistir, criando uma segunda
entrada com um **segundo socket na mesma credencial**, sem fechar o primeiro. Dois
sockets no mesmo número é o que faz o WhatsApp derrubar um com 440: a guerra de
sessão fabricada pelo próprio serviço.

Mapa de chave COMPOSTA (`enviadas`, `jidsResolvidos`, `lidsPendentes`) não precisa:
`contaId + ':' + x` já vira texto nos dois casos.

`teste-mapa-por-conta.js` tranca isso.

## Memória (o serviço já morreu por isso)

O Render matou a instância com `Ran out of memory (used over 512MB)` — quem mata é o
kernel (cgroup), então **não sobra stack trace nenhum no log**. Duas coisas mudaram por
causa disso:

- **`npm start` roda com `--max-old-space-size=1024`.** Sem o limite, o V8 dimensiona o
  heap pela memória da MÁQUINA e não pelo limite do container: ele se acha dono de
  vários GB, faz GC preguiçoso e o cgroup mata antes de ele sentir qualquer pressão.
  O teto é menor que a RAM do plano porque o RSS é heap + `external`/`arrayBuffers`
  (os Buffers de áudio e do socket) + nativo. Com o teto, um estouro vira
  `JavaScript heap out of memory` COM stack trace, em vez de morte silenciosa.

  **O número acompanha o plano — e uma vez não acompanhou.** 320 foi escolhido no
  plano de 512MB e ficou pra trás quando o serviço subiu pro standard, de 2GB. Em
  20/08 uma onda de histórico levou o heap a 314MB e o Node abortou com
  `FATAL ERROR: Ineffective mark-compacts near heap limit` — com 1,7GB de RAM parada
  ao lado, e levando junto os quatro chips que a instância segurava. Quem mudar de
  plano muda este número no mesmo passo: **no painel do Render**, campo *Start
  Command*, porque o `render.yaml` aqui é só documentação.
- **O serviço loga a memória de minuto em minuto** (`msg: "memória"`): `rssMB`,
  `heapMB`, `externalMB`, o tamanho de cada cache em memória e `pgFila` (consultas
  esperando uma das 4 conexões do pool). É o que separa "pico legítimo de sincronização"
  de "vazamento em rampa" — que pedem consertos opostos.

### O histórico do pareamento não é mais importado (e por quê)

Depois do teto de heap, o serviço **estourou de novo** — e desta vez com reprodução: apagar
o dispositivo no celular e parear de novo derrubava a instância na hora. A morte veio com a
mensagem do **Render** (`used over 512MB`), não com a do Node (`heap out of memory`): o teto
de heap segurou o heap, e o RSS estourou mesmo assim. A memória estava **fora do heap**.

O culpado é o blob de histórico. Na fonte do Baileys (`Utils/history.js`), baixar uma onda
faz `Buffer.concat` → `inflate` → `decode`, com as cópias coexistindo — e as três primeiras
são `Buffer`, ou seja memória **externa**, que o `--max-old-space-size` não limita.

O gate `shouldSyncHistoryMessage` roda **antes** do download (`Utils/process-message.js`:
`if (process) { await downloadAndProcessHistorySyncNotification(...) }`), então recusar um
tipo faz o blob nunca existir. Hoje aceitamos só:

| Tipo | Situação |
|---|---|
| `RECENT` | ✅ a janela recente — é a importação de conversa que sobrou |
| `PUSH_NAME` | ✅ só nomes, sem mensagem: barato e é a melhor fonte de nome |
| `INITIAL_BOOTSTRAP` | ❌ o blob do pareamento — **era ele que estourava** |
| `FULL` | ❌ backfill de meses/anos |
| `ON_DEMAND` | ❌ não pedimos |

**Custo aceito:** parear não importa mais o histórico do bootstrap. Se a conversa importada
nascer vazia, o caminho é devolver o `INITIAL_BOOTSTRAP` **e** subir de plano, nesta ordem —
nenhum ajuste nosso encolhe o blob, quem baixa e descompacta é o Baileys por dentro.

> **Medido em produção depois do corte:** pico de **189 MB** de RSS (era morte em 512), o
> gate recusando `INITIAL_BOOTSTRAP`/`INITIAL_STATUS_V3`/`NON_BLOCKING_DATA`, e o `RECENT`
> chegando e sendo processado — ou seja, **a importação de conversa sobreviveu**. O risco
> de "nasce vazia" não se confirmou.

### Por que uma onda descarta quase tudo (`histórico peneirado`)

A linha dizia só `descartadas: 5000` — e 5000 pode ser tudo certo ou pode ser conversa
perdida. Hoje ela abre o número por motivo:

| motivo | descarte legítimo? |
|---|---|
| `fora_da_janela` | ✅ mais velha que os 30 dias (`HISTORICO_JANELA_SEGUNDOS`) |
| `grupo` · `canal` · `status` | ✅ não é conversa de lead |
| `sem_texto` | ✅ mídia sem legenda, mensagem de protocolo |
| `sem_data` · `sem_app_url` | ⚠️ não deveria acontecer |
| `lid_sem_mapa` | ❌ **perda real** — ver abaixo |

`lidsPerdidos` conta **contatos distintos**, não mensagens (1400 mensagens de 8 pessoas é
um problema; de 300 pessoas é outro), com 5 jids de exemplo pra conferência.

O `lid_sem_mapa` é um buraco anterior a tudo isso: num pareamento novo o mapa lid→telefone
nasce vazio, e mensagem de **histórico não traz `senderPn`** (só a ao vivo traz), então
quem só aparece no histórico é descartado mesmo que o número seja aprendido minutos depois.
O conserto seria guardar o descartado e reprocessar quando o par for aprendido — trabalho
grande e estado novo, que só vale se o número medido justificar. **Meça antes.**

O `restaurarSessoes` também passou a espaçar as contas em **30s** (`WA_QR_ESPACO_CONTAS_MS`),
não 3s: cada conta trabalha pesado por minutos depois de conectar, e três delas sincronizando
juntas era o amplificador do laço de crash.

Referência medida: **~106 MB de RSS ocioso, com zero sessões**. É o custo de Node +
Baileys parado; o resto do orçamento é dividido entre as contas pareadas, e cada socket
Baileys tem caches próprios. Se o log mostrar RSS estável mas alto com várias contas, o
caminho é subir de plano (Starter 512 MB → Standard 2 GB), não caçar vazamento: o
serviço é de **instância única** por natureza, então escalar horizontalmente não é opção.

## Testes (manuais, precisam de Node + Postgres descartável)

```bash
cd services/wa-qr && npm install
createdb wa_qr_test
# estado de auth do Baileys (foco na chave da agenda)
WA_AUTH_TEST_URL=postgresql://postgres@localhost:5432/wa_qr_test node teste-auth-db.js
# caches em memória: lote de gravação do mapa @lid, teto de bytes, limpeza por conta
WA_QR_TEST_URL=postgresql://postgres@localhost:5432/wa_qr_test node teste-lidmap.js
# histórico: gate das ondas + peneira mensagem a mensagem (não precisa de banco)
node teste-historico.js
# a conta é a mesma vindo do banco ('36') ou da rota (36) — não precisa de banco
node teste-mapa-por-conta.js
# trava de sessão única por conta: disputa, prazo vencendo, batimento, SIGTERM
createdb wa_lock_test
WA_LOCK_TEST_URL=postgresql://postgres@localhost:5432/wa_lock_test node teste-sessao-lock.js
WA_LOCK_TEST_URL=postgresql://postgres@localhost:5432/wa_lock_test node teste-trava-integrada.js
# guerra de sessão: disjuntor da enxurrada de decifragem + espera que sobrevive ao restart
createdb wa_guerra_test
WA_QR_TEST_URL=postgresql://postgres@localhost:5432/wa_guerra_test node teste-guerra-sessao.js

# filtro pré-decifragem (grupo/status/canal) + retentativa: não precisa de banco
node teste-ignorar-jid.js
WA_QR_IGNORAR_GRUPOS=0 node teste-ignorar-jid.js   # o modo antigo também tem que passar
# o que o libsignal grita no console (Bad MAC) vira agregado no wa_qr_log — sem banco
node teste-console-libsignal.js
# vazão do repasse do histórico (o que derrubou o web em 15/09) — sem banco
node teste-vazao-historico.js
# outbox do repasse: grava antes, entrega depois, nunca perde (precisa de banco)
createdb wa_fila_test
psql wa_fila_test -f ../../db/migracoes/261_wa_qr_entrada_fila.sql
WA_QR_TEST_URL=postgresql://postgres@localhost:5432/wa_fila_test node teste-entrada-fila.js
# ...e o agregado sai carimbado com a conta do worker (precisa de banco)
createdb wa_qr_log_test
psql wa_qr_log_test -f ../../db/migracoes/158_wa_qr_log.sql
WA_QR_TEST_URL=postgresql://postgres@localhost:5432/wa_qr_log_test node teste-log-agregado-conta.js
```

## O Baileys 7 é o padrão (16/09/2026)

O sintoma era o `stream errored out` de ~50 em ~50 minutos: a conta caía, voltava
sozinha em segundos, e caía de novo. Medido em três contas:

| conta | no 6.7.24 | no 7.0.0-rc14 |
|---|---|---|
| 34 (Prime) | 8 quedas num dia | **zero em 3 dias** |
| 23 (Ramo) | 14 em 12h30 | **zero em 5 dias** |
| 38 (Liberal) | 10 em 24h | era a próxima da fila |

Duas contas migradas, duas confirmações, **nenhuma mensagem perdida em nenhuma
delas**. Continuar exigindo que alguém lembrasse de escrever o id numa variável
era deixar todo cliente novo nascer caindo a cada 50 minutos — então a lista
mudou de lado: `WA_QR_BAILEYS6_CONTAS` diz quem **fica** no 6, e nasce vazia.

**Por que a variável não foi apagada**, que é a pergunta seguinte: o 7.0.0 ainda é
*release candidate* — o `rc14` é o topo no npm, não existe final. Enquanto for,
ficam de pé as duas redes:

* o **6.7.24 continua instalado**, e é ele que a volta automática usa — se o rc14
  não carregar (código 3) ou a conta morrer `WA_QR_BAILEYS7_QUEDAS_MAX` vezes, o
  supervisor devolve aquela conta pro 6 sozinho;
* **prender uma conta no 6 é uma variável**, sem deploy e sem esperar por ninguém.

Quando sair o 7.0.0 final, a variável e o pacote velho saem juntos.

A variável antiga (`WA_QR_BAILEYS7_CONTAS`, a lista de quem *entrava* no 7) não
manda mais em nada. Se ficar setada, o supervisor avisa no arranque em vez de
ignorar calado.

## Nenhuma mensagem se perde num 502 (o outbox, 15/09/2026)

No mesmo incidente que a seção abaixo conta, os ~2 minutos de 502 **comeram três
mensagens de cliente** (contas 23 e 34) e um eco de saída. O repasse era um `fetch`
único, sem timeout e sem retentativa: cada falha virava uma linha `warn` e a
mensagem sumia. O WhatsApp já a tinha dado por entregue e não reenvia — as três
estavam no celular do vendedor e nunca no painel.

Consertar a vazão trata a CAUSA daquele dia. Web fora do ar acontece por outros
motivos — **todo deploy é uma janela de 502** — e a consequência seria a mesma.

Agora a mensagem é **gravada antes de virar rede**, em `wa_qr_entrada_fila`
(migração 261), e só ganha `entregue_em` quando o web responde 2xx. O que falha
fica com a próxima tentativa marcada e um drenador volta de 15 em 15s, com espera
crescente (5s, 30s, 2min, 10min, 30min, 1h). **Nada é apagado enquanto não
entregue.** Depois de 12 tentativas (~6h) a linha vira dead-letter (`parada_em`):
sai do caminho pra não segurar a fila da conta, e fica na tabela pra alguém olhar.

Reentregar é seguro: o lado Python é idempotente por `provider_sid` nos dois
webhooks.

**Três decisões que valem ler antes de mexer** (estão no topo do `entrada-fila.js`):

1. **O POST acontece FORA da transação.** A forma óbvia — abrir transação, `for
   update skip locked`, postar dentro — seguraria uma das **quatro** conexões do
   pool durante uma chamada de rede de até 15s, travando log, trava de sessão e
   `auth-db` junto. Então a transação só **arrenda** as linhas e solta; o POST corre
   livre; o resultado volta num update curto.
2. **O caminho quente drena LOTE 1.** Drenar o lote inteiro ali poria o
   processamento da mensagem atrás de até 20 POSTs. Um basta: se a fila está limpa,
   é o desta mensagem; se há atraso, é o da mais antiga — que é a que tem que sair
   primeiro. O resto fica com o tique de 15s.
3. **A ordem vem do JS, não do SQL.** O `RETURNING` de um `UPDATE` devolve as linhas
   na ordem em que o Postgres as atualizou, **não** na do `order by` da subconsulta.
   A primeira versão postava fora de ordem por causa disso e o SQL "parecia" certo —
   quem pegou foi o teste.

`recebido_em` acompanha o repasse pra a conversa não sair fora de ordem quando a
entrega atrasa, e o lado Python tem **teto** (`WA_RECEBIDO_EM_JANELA_H`): o Baileys
reentrega mensagem antiga com o timestamp original, e aceitar qualquer data
ressuscitaria conversa no lugar errado da caixa.

```sql
-- o único KPI que importa: mensagem presa
select conta_id, rota, count(*) filter (where parada_em is null) presas,
       count(*) filter (where parada_em is not null) paradas, min(criado_em) mais_antiga
  from wa_qr_entrada_fila where entregue_em is null group by 1,2;

-- a prova de que a fila salvou alguma: entregue DEPOIS de ter falhado
select conta_id, rota, count(*) n, max(tentativas) pior
  from wa_qr_entrada_fila
 where entregue_em > now() - interval '24 hours' and tentativas > 0 group by 1,2;
```

## O histórico derrubou o web (15/09/2026)

A conta 38 foi pareada às 09:56 e o sync de histórico dela despejou no web, em dois
minutos e meio, **~5.176 `POST /historico` (um por MENSAGEM) e ~3.799
`POST /contatos`** — oito conversas em paralelo, sem pausa. Uns **60 req/s** contra
um web de dois workers que também serve o painel.

O web parou de responder ao `/saude`, o Render matou a instância, e durante os 502
o wa-qr **perdeu três mensagens de cliente** das contas 23 e 34: o repasse era um
`fetch` único, sem retentativa. De quebra a fila de log estourou e **10.659 linhas
foram descartadas** — o diagnóstico ficou cego no minuto em que mais se precisava
dele.

**O teto de ondas não pega isso.** `HIST_ONDAS_MAX` limita quantas ondas se BAIXA;
não limita a que velocidade o que foi baixado vira requisição. São coisas
diferentes, e a confusão entre as duas custou o web no ar.

O que passou a existir:

| | |
|---|---|
| `WA_QR_HIST_CONCORRENCIA` (2) | conversas repassadas ao mesmo tempo. Dentro de uma conversa continua **sequencial** — é o que impede a conversa de sair embaralhada no painel |
| `WA_QR_HIST_PAUSA_MS` (100) | respiro entre POSTs da mesma conversa |
| `WA_QR_HIST_RECUO_MS` (2000) | espera depois de um não-ok: web em dificuldade passa a receber **menos** carga, não mais |
| `WA_QR_HIST_TIMEOUT_MS` (15000) | sem ele, um web que PENDURA trava a corrente pra sempre e o recuo nunca chega a valer |

O teto de requisições fica em `concorrência / (latência + pausa)` — uns **13/s** com
50ms de latência, contra os ~60/s do incidente. `teste-vazao-historico.js` tranca
isso.

**O preço:** o histórico de um cliente novo demora alguns minutos a mais pra
aparecer. É conversa antiga e órfã, que ninguém está esperando — contra a instância
cair no meio do pareamento levando junto os chips dos outros clientes.

**O que isto NÃO resolve:** a perda em si. Enquanto o repasse for um `fetch` único,
qualquer 502 — deploy, pico, o que for — ainda come mensagem de cliente. Esse é o
outbox, e é outro PR.

## Grupo não é decifrado (13/09/2026)

Decisão do dono: **ninguém recebe lead por grupo**. Então `deveIgnorarNoBaileys`
devolve `true` pra `@g.us`, e o Baileys confirma o recebimento e sai — sem cripto,
sem retentativa, sem ida ao Postgres. `WA_QR_IGNORAR_GRUPOS=0` volta atrás sem
deploy.

**Quanto isso vale.** Contado na conta 23 no dia 12/09 inteiro: 3.478 entradas e
337 saídas de grupo, contra 12.766 eventos no total — **~30%** do caminho quente.
Não confunda com os 6.918 descartes de eco de saída pra pessoa (`@lid`, sem
texto), que são o balde maior e **não** têm nada a ver com grupo.

**Por que é seguro cortar antes de decifrar**, que é a pergunta certa a fazer de
qualquer filtro deste tipo (ver `teste-ignorar-jid.js`, invariante
`!(cortado && usavel)`):

* `ehConversaValida` **já** descartava grupo. Nenhuma mensagem que o app usaria
  deixa de chegar — o corte só antecipa um descarte que já existia.
* `marcarVivo` e `aprenderLid` moram no listener do nó cru (`CB:message`), que o
  `shouldIgnoreJid` não filtra. O segundo é o que importa: sem ele, quem
  aparecesse primeiro num grupo perderia o número no mapa `lid->telefone`, e uma
  mensagem futura dessa pessoa cairia em `semNumeroReal` — sumiria calada.
* Envio pra grupo (Raio-X) segue igual: `shouldIgnoreJid` só olha entrada.

**O que se perde:** nome/número aprendido só por grupo via `repassarContatos`. A
agenda (`contacts.upsert`) e a conversa real continuam ensinando.

## CPU: a guerra de sessão derrubava a instância (20/08/2026)

Depois da memória, veio a CPU — e a leitura errada custa tempo, porque o sintoma no Render
é o mesmo ("Instance failed"). **Como separar:** se o gráfico de memória está no chão e o
de CPU encosta no teto de `1 CPU`, não é OOM. O log da aplicação dá o veredito:
`event loop travou — nesse intervalo /saude não respondia`.

**O que aconteceu.** A conta 34 foi substituída por outra sessão (o 440 de
`connectionReplaced`) pela enésima vez no dia — 59 substituições em 24h. A substituição
invalida as sessões Signal desta ponta, e a partir daí cada eco de mensagem chega
indecifrável: **1119 `failed to decrypt message` numa hora**, contra 50-150 num dia
inteiro normal. Cada falha faz o Baileys pedir reenvio, o WhatsApp reentregar e falhar de
novo — criptografia em rajada num contêiner de 1 CPU.

O event loop travou por 25, 34, 40, 66, 71 e **73 segundos**. O health check do Render bate
em `/saude` e desiste em **5s**: a instância foi morta e reiniciada **7 vezes na mesma
hora** (13:19, 13:22, 13:26, 13:29, 13:33...).

**Por que virava um laço fechado.** A defesa contra guerra de sessão já existia e estava
certa: depois de um 440 a retomada espera 5, 10, 20, 40, 80 minutos (`esperaPos440`). Só
que o contador vivia na **memória do processo**. Cada morte zerava a espera, o
`restaurarSessoes` religava a conta na hora, pegava o mesmo lote indecifrável e recomeçava.
A proteção era desarmada justamente pelo reinício que a briga provocava — quanto pior a
briga, mais rápido a gente voltava pra ela.

Duas coisas mudaram:

- **A espera passou pro banco** (`wa_qr_sessao_estado.substituida_em` e `tentativas_440`,
  migração 182). O arranque lê antes de religar: conta em castigo não abre socket, o estado
  é recriado em memória e o vigia resgata na hora certa, pelo caminho que já existia.
- **Disjuntor da enxurrada** (`abrirDisjuntor`): passando de `WA_QR_DECIFRAR_TETO` falhas
  na janela, a conta para sozinha e entra na mesma espera do 440. O 440 nem sempre chega
  pra avisar — dá pra ficar com o socket de pé sem conseguir decifrar coisa alguma. O teto
  é por conta: a vizinha continua trabalhando.

O logger que vai pro Baileys também passou a ser **por conta**: até aqui o
`failed to decrypt message` chegava no `wa_qr_log` com `conta_id` VAZIO, e "qual conta está
sofrendo" é a primeira pergunta numa tempestade dessas.

**O que isto NÃO conserta.** A causa raiz é outro aparelho disputando a credencial —
WhatsApp Web aberto num computador, outro celular, outra ferramenta. O disjuntor evita que
isso derrube o serviço; ele não tira o rival de lá. A primeira ação continua sendo no
celular: **WhatsApp → Aparelhos conectados**, remover o que não for a sessão do ZAQ.

Pra ver se está acontecendo agora:

```sql
select date_trunc('hour', criado_em) hora,
       count(*) filter (where msg = 'failed to decrypt message')  falhas,
       count(*) filter (where msg ilike '%substituída%')          substituicoes,
       count(*) filter (where msg ilike '%event loop%')           travas,
       count(*) filter (where msg ilike '%wa-qr no ar%')          boots
  from wa_qr_log
 where criado_em > now() - interval '24 hours'
 group by 1 order by 1;
```

Fora de deploy, `boots` e `travas` têm que ser zero.

### Cortar a enxurrada na origem (os três padrões do Baileys)

O disjuntor acima trata o **efeito**: quando a decifragem começa a falhar em série, a conta
sai de cena e espera. Faltava atacar o **volume** — e três padrões do Baileys 6.7.9
trabalhavam contra (`Defaults/index.ts`):

| Padrão | Era | Ficou | Por quê |
|---|---|---|---|
| `shouldIgnoreJid` | `() => false` | `deveIgnorarNoBaileys` | não pagar decifragem por status de contato |
| `maxMsgRetryCount` | `5` | `2` | o que não decifra na 2ª não decifra na 5ª |
| `retryRequestDelayMs` | `250` | `2000` | 250ms vira laço apertado que não devolve o event loop |

A conta da amplificação com os padrões antigos: 1119 falhas/hora × 5 retentativas a cada
250ms = até **5.600 ciclos/hora**, cada um com criptografia, ida à rede e **uma consulta ao
Postgres** (o `getMessage` chama `buscarEnviada`).

**O `shouldIgnoreJid` é a maior das três.** O `ehConversaValida` já descartava
`status@broadcast`, mas só **depois** de decifrar — a CPU já tinha sido paga. O Baileys
checa o `shouldIgnoreJid` no topo do `handleMessage`, **antes** do `decryptMessageNode`
(`Socket/messages-recv.ts:727` na v6.7.9): confirma o recebimento e sai. Com ~10 mil
contatos mapeados, cada status que qualquer um deles posta deixa de custar.

**Grupo NÃO entra na lista, de propósito.** Mensagem de grupo também não vira lead, mas
alimenta o aprendizado de contato (`repassarContatos`); ignorá-la no Baileys perderia isso.
Status e canal não têm esse valor. O `teste-ignorar-jid.js` trava esse invariante: nada que
o `ehConversaValida` aproveitaria pode ser cortado antes de chegar.

**O que se perde:** o status de um contato desconhecido não vai mais ensinar o nome dele.
Na prática o contato já vem da agenda ou de conversa real — e o preço de manter era a
instância morrer.

## Trava de sessão única por conta

Duas instâncias com a MESMA credencial fazem o WhatsApp derrubar uma delas com 440
(`connectionReplaced`). Isso acontecia **em todo deploy**: o Render sobe a instância nova
antes de matar a velha, e as duas rodavam `restaurarSessoes()`. Em 15/08/2026 a conta 35
abriu 7 sessões entre 13:53 e 20:00 e todas morreram assim — e cada morte reinicia o
ciclo (reconecta, rebaixa a agenda inteira, redespeja no webhook).

Agora `iniciarSessao` só abre socket com a conta alugada na tabela `wa_qr_sessao_lock`
(migração 157, aplicada pelo web). O aluguel vale 60s e é renovado a cada 20s:

- **conta ocupada** → não abre socket, status fica `reconectando` e tenta de novo a cada
  15s (`WA_QR_RETENTAR_TRAVA_MS`). Nada é apagado.
- **SIGTERM** → fecha os sockets, solta os aluguéis e sai. É o que faz o deploy trocar
  de dono em segundos em vez de esperar o prazo vencer.
- **processo morto sem aviso** (SIGKILL/OOM) → ninguém renova, o prazo vence e a próxima
  instância assume em no máximo um TTL. Não existe trava presa pra sempre.
- **aluguel perdido** (o batimento não conseguiu renovar) → larga o socket na hora, senão
  viram dois de novo.
- **tabela ainda não existe** (o web não migrou; ver abaixo) → segue SEM trava, mas só
  por 60s de cada vez: quando a tabela aparece, as contas que estavam rodando sem aluguel
  são reconciliadas. Se a conta já for de outra instância, a nossa sessão sai.

### A janela entre os dois deploys

O `wa-qr` não roda migração — quem cria a `wa_qr_sessao_lock` é o web. Quando os dois
deployam juntos, o `wa-qr` costuma ficar de pé ANTES: na estreia disto em produção
(15/08, 21:50) as três contas religaram e a tabela só nasceu às 21:52.

Nesse intervalo o serviço segue **sem trava**, para não deixar o WhatsApp de todo mundo
no chão esperando deploy alheio — mas a desistência tem prazo e se corrige sozinha:

```
trava: tabela wa_qr_sessao_lock ainda não existe — seguindo SEM trava por ora
trava: tabela apareceu — conta agora está protegida ✓
```

Se a segunda linha não aparecer em ~1 min depois do deploy do web, a reconciliação não
rodou e vale investigar.

Quem está com o quê:

```sql
select conta_id, dono, expira_em from wa_qr_sessao_lock order by conta_id;
```

`expira_em` no passado significa que ninguém está segurando. Levar 440 **segurando** a
trava quer dizer que quem assumiu não é outra instância nossa — é o celular do vendedor
abrindo o WhatsApp Web noutro lugar; o log diz isso em `seguravaATrava`.

## Local (dev)

```bash
cd services/wa-qr
npm install
DATABASE_URL=postgres://... WA_QR_SHARED_SECRET=dev APP_URL=http://localhost:8000 npm start
```
