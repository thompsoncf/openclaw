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

## Local (dev)

```bash
cd services/wa-qr
npm install
DATABASE_URL=postgres://... WA_QR_SHARED_SECRET=dev APP_URL=http://localhost:8000 npm start
```
