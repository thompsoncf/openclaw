# Acompanhar deploys e logs do Render

Como saber que um deploy quebrou **sem** abrir o dashboard — e como dar ao
agente (Claude Code) visibilidade real do que acontece em produção.

---

## O problema

Existem duas formas de olhar o Render, e as duas estavam com defeito:

1. **Perguntar** (`scripts/render_cli.py` → `api.render.com`). Funciona no seu
   terminal, mas **não** no ambiente do Claude Code na web: `api.render.com`
   volta **403 no CONNECT** na política de egresso. O mesmo vale pra
   `app.zaq-ia.com` e `*.onrender.com` — o único host externo alcançável de lá
   é o GitHub. Ou seja: o agente não conseguia ver deploy nenhum.

2. **Ser avisado**. Não existia. Deploy quebrado só aparecia quando alguém
   reparava que o site estava fora.

As duas seções abaixo consertam uma cada. São independentes — dá pra fazer só
uma — mas juntas cobrem tempo real *e* histórico.

---

## Parte 1 — Webhook: o Render avisa a gente

Inverte a direção. Em vez do agente perguntar (bloqueado), o Render avisa. O
receptor roda dentro do `openclaw-web`, que está hospedado no próprio Render e
**alcança a API sem problema** — então é ele quem busca os detalhes e deixa
tudo mastigado no Postgres.

```
Render  ──POST assinado──▶  openclaw-web  ──enriquece via API──▶  Postgres
(deploy)                    /webhook/render                       render_evento
                                  │
                                  └── falhou? ──▶ Telegram + e-mail do admin
                                                  (com commit e log junto)
```

### Passo 1: gerar o segredo do webhook

Requer plano **Professional ou superior**
(<https://dashboard.render.com/billing/update-plan>).

> **Webhook no Render é do WORKSPACE, não do serviço.** Não adianta procurar
> dentro de `openclaw-web-bcu3` — não tem lá. Fica num item de menu próprio, no
> nível do workspace, e um único webhook cobre **todos** os serviços. É por isso
> que o receptor grava `servico_id`/`servico_nome` em cada evento e o
> `historico` tem `--servico`: vai chegar evento de tudo.

Vá direto em **<https://dashboard.render.com/webhooks>** → **New Webhook**
(ou direto em <https://dashboard.render.com/webhooks/new>):

- **URL**: `https://openclaw-web-bcu3.onrender.com/webhook/render`
- **Eventos**: marque no mínimo `deploy_ended`. Vale marcar também
  `deploy_started` e `server_failed` — o receptor grava qualquer tipo, e só
  alerta em falha de deploy.

Salve o **signing secret** que ele mostrar (começa com `whsec_`). Ele aparece
**uma vez só** — se perder, é preciso gerar outro.

### Passo 2: gerar a API key

Em **<https://dashboard.render.com/settings#api-keys>**
(Account Settings → API Keys) → **Create API Key**. Começa com `rnd_`, e
também só aparece uma vez.

Cuidado: a API key vale pra **todos os workspaces** do seu usuário, não só
este. Trate como segredo de produção.

### Passo 3: variáveis de ambiente no `openclaw-web`

| Variável | Onde nasce | Obrigatória | Pra quê |
|---|---|---|---|
| `RENDER_WEBHOOK_SECRET` | Passo 1 (`whsec_…`) | **sim** | Valida a assinatura. Sem ela o receptor fica **inerte** (responde 200 e ignora). |
| `RENDER_API_KEY` | Passo 2 (`rnd_…`) | não, mas ajuda | Enriquece: commit, cauda do log e o status fino (em que etapa quebrou). |

São duas coisas diferentes e não se substituem: o **segredo** prova que o
evento veio do Render; a **API key** busca o resto da história.

**O que já vem de graça no corpo do webhook** (medido numa entrega real, e mais
do que a documentação de tipos do exemplo oficial sugere):

```json
{ "type": "deploy_ended",
  "timestamp": "2026-08-15T18:24:23.351041589Z",
  "data": { "id": "evt-…", "serviceId": "srv-…",
            "serviceName": "openclaw-web-bcu3", "status": "succeeded" } }
```

Ou seja: **nome do serviço e sucesso/falha saem sem chamar a API**. O alerta de
deploy quebrado funciona mesmo sem `RENDER_API_KEY` — o que a chave acrescenta é
o *commit*, o *log* e trocar o `failed` grosso pelo `build_failed` fino, que diz
em que etapa parou.

> A ordem importa pouco, mas subir o código **antes** de criar o webhook é
> seguro: sem `RENDER_WEBHOOK_SECRET` a rota não aceita nada e não grava nada.

### Passo 4: conferir

A migração `154_render_evento.sql` roda sozinha no deploy (o `preDeployCommand`
já chama `python -m db.aplicar_migracoes`).

Dispare um deploy qualquer e veja:

```bash
python -m scripts.render_cli historico
```

```
15/08 17:34  ok     openclaw-web-bcu3     live           abc123de  Ajusta o funil
15/08 17:12  FALHA  openclaw-web-bcu3     build_failed   9f2a11c0  Refatora o portal
```

### O que dá pra fazer com isso

```bash
python -m scripts.render_cli historico                       # últimos 20
python -m scripts.render_cli historico --servico openclaw-web-bcu3
python -m scripts.render_cli historico --falhas              # só o que quebrou
python -m scripts.render_cli historico --falhas --log        # com a cauda do log
```

`historico` lê **só do Postgres**. É o único comando do `render_cli` que
funciona com `api.render.com` bloqueada — por isso ele não exige
`RENDER_API_KEY`, só `DATABASE_URL`.

Direto em SQL, se preferir:

```sql
-- o que quebrou nos últimos 7 dias
select recebido_em, servico_nome, status, commit_id, commit_msg
  from render_evento
 where sucesso is false
   and recebido_em > now() - interval '7 days'
 order by recebido_em desc;

-- taxa de sucesso por serviço no último mês
select servico_nome,
       count(*) filter (where sucesso) as ok,
       count(*) filter (where sucesso is false) as falhou
  from render_evento
 where tipo = 'deploy_ended' and recebido_em > now() - interval '30 days'
 group by servico_nome;
```

O corpo cru do webhook fica em `payload` (jsonb) e a resposta da API em
`detalhes` — o Render adiciona campos sem avisar, e assim nada se perde mesmo
sem coluna mapeada.

### Alertas

Quando um deploy termina em `build_failed` / `update_failed` /
`pre_deploy_failed`, o admin recebe Telegram + e-mail com o commit e o final do
log — pelo mesmo canal configurado em `/admin/comunicacao`.

Três cuidados que evitam alerta chato:

- **Cancelado por gente não alerta.** `canceled` não é falha.
- **Reentrega não realerta.** O Render remanda o webhook quando não recebe 200
  a tempo; a trava por `webhook-id` garante um alerta por deploy.
- **Na dúvida, não alerta.** Se a API não respondeu e não dá pra afirmar que
  quebrou, `sucesso` fica `NULL` em vez de `false`. Alerta falso é pior que
  alerta que faltou.

---

## Parte 2 — Liberar `api.render.com` pro agente

Isto é **configuração do ambiente, não do código** — e é o maior ganho pelo
menor esforço: o `render_cli.py` já existe e já faz tudo.

Enquanto `api.render.com` estiver bloqueada, estes comandos **não funcionam**
em sessão web do Claude Code (403 no CONNECT, sem contorno pelo lado do
script):

```bash
python -m scripts.render_cli services              # todos os serviços
python -m scripts.render_cli services --projetos   # + a qual projeto pertencem
python -m scripts.render_cli projetos              # inventário em árvore
python -m scripts.render_cli status  openclaw-web-bcu3
python -m scripts.render_cli deploys openclaw-web-bcu3 --limit 5
python -m scripts.render_cli logs    openclaw-web-bcu3 --limit 200
python -m scripts.render_cli deploy  openclaw-web-bcu3
```

### Inventário ≠ histórico

Duas perguntas diferentes, e é fácil confundir:

- **`projetos` / `services`** — "o que existe nesta conta agora?" Retrato do
  estado. Precisa da API alcançável.
- **`historico`** — "o que aconteceu?" Registro de eventos. Um serviço só
  aparece depois de deployar, e só a partir da criação do webhook. Funciona com
  a API bloqueada.

O `projetos` monta a árvore **Projeto → Ambiente → Serviço** — no Render o
serviço não aponta pro projeto direto, quem faz a ponte é o ambiente. Serviço
criado fora de projeto (é o caso dos `-bcu3`, feitos na mão) sai listado
separado, em `(sem projeto)`, pra não sumir do inventário.

### Como liberar

A política de rede é escolhida quando o ambiente é criado, nas configurações do
Claude Code na web. Edite o ambiente e ou (a) troque pra uma política que
permita HTTPS de saída, ou (b) acrescente `api.render.com` à lista de hosts
permitidos.

Documentação: <https://code.claude.com/docs/en/claude-code-on-the-web>

Depois é preciso deixar a chave visível pra sessão:

```bash
export RENDER_API_KEY=rnd_xxx
```

Pra conferir se passou a funcionar:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://api.render.com/v1/services
# 401 já é sucesso de REDE (chegou lá e reclamou da falta de chave).
# 000 com "CONNECT tunnel failed" = ainda bloqueado.
```

O diagnóstico completo do proxy, incluindo os últimos hosts recusados:

```bash
curl -sS "$HTTPS_PROXY/__agentproxy/status"
```

### Vale liberar mesmo tendo o webhook?

Vale — eles resolvem coisas diferentes:

| | Webhook (Parte 1) | API liberada (Parte 2) |
|---|---|---|
| Deploy quebrou agora | avisa sozinho | só se você perguntar |
| Histórico | permanente, no seu banco | últimos que a API devolver |
| Log completo | só a cauda, e só em falha | qualquer serviço, qualquer hora |
| Ver env vars, disparar deploy | não | sim |
| Funciona com rede bloqueada | **sim** | não |

---

## Sobre o `render-examples/webhook-github-action`

Foi o ponto de partida deste trabalho, mas resolve outro problema: ele dispara
um *workflow* do GitHub quando o deploy termina — não traz log nenhum e não
guarda histórico. O que a gente aproveitou dele foi o **padrão de assinatura**
(Standard Webhooks) e o fato de que o payload do Render é magro e **precisa**
ser enriquecido via `/v1/events/{id}` pra chegar no `deployId`.

Se um dia fizer sentido rodar E2E ou limpar cache de CDN a cada deploy, o
gancho pra isso é o `core/render_eventos.processar` — dá pra disparar o
workflow de lá, e o GitHub é justamente o único host que o agente alcança.

---

## Arquivos

| Arquivo | Papel |
|---|---|
| `core/render_eventos.py` | assinatura, enriquecimento, gravação, alerta e leitura |
| `web/app.py` → `POST /webhook/render` | recebe, valida e joga pro background |
| `db/migracoes/154_render_evento.sql` | tabela `render_evento` |
| `scripts/render_cli.py` → `historico` | lê o histórico sem depender da API |
| `tests/test_render_webhook.py` | 31 testes: assinatura, idempotência, alerta, rota |
