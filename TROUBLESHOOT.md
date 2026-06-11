# 🛠️ Troubleshooting — Fase 1

## 1️⃣ Erro de conexão misteriosa com o banco

**O problema:** Deploy no Render, mas a web ou bot não conecta ao Supabase.

**A armadilha:** Você copiou a "direct connection" em vez da "Session" connection.

**A solução:**
- Vá em **Project Settings > Database** no Supabase
- Copie a string de conexão no **modo Session** (host termina em `pooler.supabase.com`)
- **Não use** a "direct connection" — Render com IPv6 não alcança

**Teste:** Se vir `connection refused` ou `timeout` no log, é isso.

---

## 2️⃣ Bot não responde ao /start

**O problema:** Manda `/start` no Telegram, nada acontece.

**Onde olhar:**
- **NÃO** o log da web (`openclaw-web`)
- **SIM** o log do worker (`openclaw-bot`)

**Como achar:**
- Painel do Render → serviço `openclaw-bot` → aba **Logs**
- Procure por:
  - `TELEGRAM_TOKEN not found` → segredo não preenchido no painel
  - `database connection error` → DATABASE_URL inválida
  - `module not found` → requirements.txt incompleto

**Teste:** `/start` deve aparecer no log com `200 OK` ou `Update received`.

---

## 3️⃣ Web demora pra carregar na primeira visita

**O problema:** Abre a URL `…onrender.com`, demora uns segundos, depois carrega.

**Não é bug.** É o plano grátis do Render:
- Serviços gratuitos dormem depois de inatividade
- Acordam na primeira requisição
- Primeira visita paga esse "cold start" (~3-5 segundos)

**Teste:** Recarregue a página — da segunda vez é instantâneo.

---

## Se nada disso resolver

Cole a **saída do erro** aqui. Pode ser:
- Saída do terminal local (se testou localmente)
- Log do Render (se foi via Blueprint)
- Response da API (se foi via curl)
- Mensagem do Telegram (se foi pelo bot)

**Quanto mais detalhe, mais rápido achamos.**
