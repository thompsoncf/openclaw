# 🦅 OpenClaw

**Seu assistente financeiro inteligente com IA.**

Registre gastos via Telegram, analise sua jornada financeira com um painel web, receba alertas proativos — tudo rodando na nuvem.

## Fases

- **Fase 1** (agora): Fundação + Bot básico + Deploy
- **Fase 2**: Leitura de cupom (foto → IA)
- **Fase 3**: Agendador + Alertas
- **Fase 4**: Painel web completo
- **5-7**: Expansão (e-mail, tarefas, piloto, cobrança)

## Quick Start

### 1. Banco (Supabase)

```bash
# .env com DATABASE_URL do Supabase
cp .env.example .env

# Criar tabelas
python -c "from db.conexao import get_pool, init_schema; init_schema(get_pool())"
```

### 2. Bot (Telegram local)

```bash
pip install -r requirements.txt
python bot/main.py
```

No Telegram: `/start` → `gastei 50 no mercado` → `qual meu saldo?`

### 3. Deploy (Render)

- GitHub: Push automático
- Render: Nova Blueprint → `openclaw` repo
- Segredos: `ANTHROPIC_API_KEY`, `DATABASE_URL`, `TELEGRAM_TOKEN`

## Estrutura

```
openclaw/
├── db/              # Conexão, schema
├── financeiro/      # Lógica (Livro, transações)
├── bot/             # Telegram + Anthropic
├── web/             # Flask API (Fase 2+)
├── testes/          # Testes (sempre apontam pra DB teste)
├── render.yaml      # Deploy
└── requirements.txt
```

## Cuidados

- ⚠️ **Nunca** commit `.env` (o `.gitignore` já barra)
- ⚠️ **Segredos só** no painel do Render
- ⚠️ **Testes** sempre em banco de TESTE
- ⚠️ **Taxa diária** de mensagens Anthropic (não deixe sair do controle)

---

**Mantém simples, entrega valor.**
