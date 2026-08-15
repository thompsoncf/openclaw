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
```

## Local (dev)

```bash
cd services/wa-qr
npm install
DATABASE_URL=postgres://... WA_QR_SHARED_SECRET=dev APP_URL=http://localhost:8000 npm start
```
