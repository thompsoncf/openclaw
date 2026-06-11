# OpenClaw — Configuração

## Supabase

- **Projeto:** hkgonlezxdmyjcvdysxs
- **Link:** https://hkgonlezxdmyjcvdysxs.supabase.co
- **Chave Pública:** `sb_publishable_oZ-8Y3S5-osSNo7qwwVmfw_eSPYbYXh`

## Variáveis de Ambiente (.env)

Preencher **localmente** (nunca commit):
```
ANTHROPIC_API_KEY=sk-ant-xxxxx
DATABASE_URL=postgresql://postgres.xxx:password@db.xxx.pooler.supabase.com:6543/postgres
TELEGRAM_TOKEN=123456:ABCxyz
OPENCLAW_MODEL=claude-sonnet-4-6
```

## Próximos Passos

1. ✅ Populate `.env` com DATABASE_URL do Supabase
2. ⏳ Rodar `python -c "from db.conexao import get_pool; from db import init_schema; init_schema(get_pool())"`
3. ⏳ Deploy no Render (Blueprint)

---

**Segurança:** Chave pública é segura aqui. DATABASE_URL e TELEGRAM_TOKEN ficam só no `.env` e no painel do Render.
