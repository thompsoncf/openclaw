# OpenClaw — assistente pessoal (piloto multi-usuário)

Assistente pessoal de IA com cérebro no **Claude (Anthropic)**, rodando no seu
VPS e acessível pelo **Telegram**. O primeiro agente pronto é o **financeiro**:
registra despesas e receitas, lê cupom/nota por **foto**, mostra saldo e
relatório por categoria. Já nasce **multi-usuário**: cada pessoa enxerga só os
dados dela.

## A ideia em uma frase

Uma **Fábrica de agentes** produz agentes a partir de quatro peças — persona,
ferramentas, memória e cérebro. Trocando o recheio, a mesma fábrica gera o
financeiro, o de agenda, o proativo, etc. O financeiro é o primeiro a sair dela.

## Estrutura

```
openclaw/
  core/            A Fábrica de agentes (o núcleo)
    brain.py         Conexão com o Claude API
    agent.py         Agente + loop de ferramentas + leitura de foto
    memory.py        Memória de conversa
  finance/         O agente financeiro
    models.py        Lançamento, tipos e categorias (valores em centavos)
    livro_caixa.py   Persistência no Postgres, sempre por usuário
    tools.py         Ferramentas: lançar, ver saldo, relatório
    agente_financeiro.py
  usuarios/        Identificação por Telegram + limite de uso (custo)
  db/              Schema e conexão (Postgres)
  telegram_bot.py  A interface do piloto
  cli.py           Teste local no terminal
  tests/           Testes (rodam contra um Postgres de teste)
```

## Rodar local (na sua máquina)

1. Crie o ambiente e instale:
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Suba um Postgres e crie o banco `openclaw`.
3. Copie `.env.example` para `.env` e preencha `ANTHROPIC_API_KEY`,
   `DATABASE_URL` e `TELEGRAM_TOKEN` (crie o bot com o **@BotFather** no Telegram).
4. Teste no terminal, sem Telegram:
   ```bash
   python cli.py
   ```
5. Ou suba o bot:
   ```bash
   python telegram_bot.py
   ```

## Deploy no Render + Supabase (recomendado)

Sem máquina nova: banco no **Supabase**, bot no **Render** como worker.

1. **Supabase** — crie um projeto. Em *Project Settings > Database*, copie a
   string de conexão no **modo Session** (host termina em `pooler.supabase.com`,
   usuário no formato `postgres.<ref>`). Essa é a sua `DATABASE_URL`.
   (O conector já vem blindado pra funcionar com o pooler do Supabase.)
2. **Repo** — suba este projeto pro GitHub (o `.gitignore` já protege o `.env`).
3. **Render** — *New + > Blueprint*, aponte pro repo. O `render.yaml` cria
   **dois** serviços: o `openclaw-bot` (worker, o bot — sem URL) e o
   `openclaw-web` (web, o embrião — esse **ganha a URL** `…onrender.com`).
   A web sobe no plano grátis por enquanto e vira o painel financeiro depois.
4. No painel do Render, preencha as variáveis secretas:
   `ANTHROPIC_API_KEY`, `DATABASE_URL` (a do Supabase) e `TELEGRAM_TOKEN`.
5. Deploy. Pra criar as tabelas na primeira vez, rode uma vez localmente
   apontando pro Supabase, ou abra um Shell no Render e rode
   `python -c "from db.conexao import get_pool, init_schema; init_schema(get_pool())"`.

O bot sobe e fica de pé sozinho (o worker reinicia se cair).

## Subir no VPS (alternativa)

1. Instale Postgres e crie o banco/usuário:
   ```bash
   sudo apt install postgresql
   sudo -u postgres psql -c "create user openclaw with password 'troque-isto';"
   sudo -u postgres psql -c "create database openclaw owner openclaw;"
   ```
2. Clone o projeto, crie a venv e instale (passos acima).
3. Preencha o `.env` com a `DATABASE_URL` apontando pro Postgres do VPS.
4. Deixe rodando como serviço com **systemd** (`/etc/systemd/system/openclaw.service`):
   ```ini
   [Unit]
   Description=OpenClaw bot
   After=network.target postgresql.service

   [Service]
   WorkingDirectory=/opt/openclaw
   EnvironmentFile=/opt/openclaw/.env
   ExecStart=/opt/openclaw/.venv/bin/python telegram_bot.py
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```
   ```bash
   sudo systemctl enable --now openclaw
   ```
   O bot usa *long polling*, então **não precisa abrir porta** nem configurar
   domínio/HTTPS pro piloto.

## Custo e segurança (importante)

- **Limite de uso:** cada usuário tem um teto diário de mensagens
  (`limite_mensagens_dia`, padrão 50) pra proteger sua conta da Anthropic.
- **Chaves:** ficam só no `.env` do servidor — nunca no código.
- **Dados de terceiros:** é dado financeiro sensível; guarde só o necessário e
  mantenha cada usuário isolado (o sistema já faz isso). Se for comercializar,
  vale uma consulta jurídica sobre LGPD.

## Testes

```bash
export DATABASE_URL="postgresql://...banco-de-TESTE..."
pytest -q
```
Os testes **limpam as tabelas** — aponte para um banco separado, nunca o de produção.

## Próximos passos sugeridos

- Modo **proativo** (alertas de gastos, contas a vencer) com um agendador.
- Novos agentes pela mesma Fábrica: agenda/reuniões, e-mail, tarefas.
- Camada **web** (a interface "depois") reaproveitando este mesmo núcleo.
- Confirmação de valores altos com botões inline no Telegram.
