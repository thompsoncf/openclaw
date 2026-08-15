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

No **web** (app Python), configure também:

| Var | Descrição |
|-----|-----------|
| `WA_QR_SERVICE_URL` | URL pública deste serviço (ex.: `https://openclaw-waqr.onrender.com`) |
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

- **`npm start` roda com `--max-old-space-size=320`.** Sem o limite, o V8 dimensiona o
  heap pela memória da MÁQUINA e não pelo limite do container: ele se acha dono de
  vários GB, faz GC preguiçoso e o cgroup mata antes de ele sentir qualquer pressão.
  320 e não 512 porque o RSS é heap + `external`/`arrayBuffers` (os Buffers de áudio e
  do socket) + nativo. Com o teto, um estouro vira `JavaScript heap out of memory` COM
  stack trace, em vez de morte silenciosa.
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
```

## Local (dev)

```bash
cd services/wa-qr
npm install
DATABASE_URL=postgres://... WA_QR_SHARED_SECRET=dev APP_URL=http://localhost:8000 npm start
```
