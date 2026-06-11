# COMECE AQUI — OpenClaw

Guia rápido pra sair do zero ao "no ar". Siga de cima pra baixo.

- **Nome da pasta / repositório:** `openclaw` (minúsculo, sem espaço/acento)
- **Repositório no GitHub:** crie como **Private** (projeto financeiro)
- **Onde os segredos moram:** no painel do Render — **nunca** no GitHub

---

## FASE 1 — Pôr no ar (faça agora)

### 1. Virar repositório git
Com a pasta `openclaw` aberta no VS Code, no terminal (Ctrl+'):
```bash
git init
git add .
git commit -m "OpenClaw: esqueleto inicial (fabrica + financeiro + bot + web)"
```
Confira que o `.env` NÃO está na lista:
```bash
git status   # o .env nao pode aparecer aqui
```

### 2. Subir pro GitHub
Crie um repo vazio em github.com/new (nome `openclaw`, **Private**, sem README/gitignore). Depois:
```bash
git remote add origin https://github.com/SEU_USUARIO/openclaw.git
git branch -M main
git push -u origin main
```

### 3. Banco no Supabase
- Crie um projeto no Supabase.
- *Project Settings > Database* → copie a conexão no **modo Session**
  (host com `pooler.supabase.com`, usuário `postgres.<ref>`).
- Essa string é a sua `DATABASE_URL`.

### 4. Criar as tabelas (uma vez só)
Escolha **um** dos caminhos:

**A) Pelo SQL Editor do Supabase (mais visual):** abra o SQL Editor, cole
todo o conteúdo de `db/supabase_setup.sql` e clique em **Run**. Esse arquivo
já cria as tabelas e liga o RLS (tranca a API pública do Supabase; o app
continua funcionando pela conexão direta).

**B) Pelo código:** na sua máquina, copie `.env.example` para `.env`, preencha
a `DATABASE_URL` do Supabase e rode:
```bash
python -c "from db.conexao import get_pool, init_schema; init_schema(get_pool())"
```

### 5. Deploy no Render
- *New + > Blueprint* → aponte pro repo `openclaw`.
- O `render.yaml` cria dois serviços: `openclaw-bot` (worker) e `openclaw-web`
  (web, ganha a URL `…onrender.com`).
- Preencha os segredos no painel: `ANTHROPIC_API_KEY`, `DATABASE_URL`,
  `TELEGRAM_TOKEN` (crie o bot com o **@BotFather** no Telegram).

### 6. Primeiro teste
No Telegram, dê `/start` no seu bot e mande:
> gastei 50 no mercado

Depois: `qual meu saldo?`. Se responder certo, a Fase 1 está fechada. 🎉

---

## Ciclo de trabalho (daqui pra frente)
Codou no VS Code → `git add .` → `git commit -m "..."` → `git push`.
O Render redeploya sozinho a cada push.

---

## ROADMAP

| Fase | O que é | Status |
|------|---------|--------|
| 0 | Fundação: fábrica, financeiro, livro-caixa, bot, web embrião, testes | ✅ feito |
| 1 | Pôr no ar: git, GitHub, Supabase, Render, 1º teste | 👉 agora |
| 2 | Foto vira lançamento: validar leitura de cupom com a API real | a fazer |
| 3 | Modo proativo: agendador + alertas de gasto / contas a vencer | a fazer |
| 4 | Painel web: login, saldo, gráfico por categoria, lista | a fazer |
| 5 | Mais agentes pela fábrica: agenda, e-mail, tarefas | a fazer |
| 6 | Piloto com pessoas: convidar, vigiar custo, colher feedback | a fazer |
| 7 | Virar produto: cobrança, LGPD, termos, onboarding | se comercializar |

---

## Cuidados que valem ouro
- O `.env` nunca vai pro GitHub (o `.gitignore` já barra).
- Segredos só no painel do Render.
- Os testes limpam tabelas: aponte sempre pra um banco de TESTE, nunca produção.
- Cada usuário tem teto diário de mensagens (protege seu custo com a Anthropic).
