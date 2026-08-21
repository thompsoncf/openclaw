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
# trava de sessão única por conta: disputa, prazo vencendo, batimento, SIGTERM
createdb wa_lock_test
WA_LOCK_TEST_URL=postgresql://postgres@localhost:5432/wa_lock_test node teste-sessao-lock.js
WA_LOCK_TEST_URL=postgresql://postgres@localhost:5432/wa_lock_test node teste-trava-integrada.js
# guerra de sessão: disjuntor da enxurrada de decifragem + espera que sobrevive ao restart
createdb wa_guerra_test
WA_QR_TEST_URL=postgresql://postgres@localhost:5432/wa_guerra_test node teste-guerra-sessao.js

# filtro pré-decifragem (status/canal) + retentativa: não precisa de banco
node teste-ignorar-jid.js
```

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
