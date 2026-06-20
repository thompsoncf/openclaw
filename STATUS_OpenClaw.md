# OpenClaw — Status geral do projeto

_Assistente financeiro IA multi-tenant (PT-BR), via Telegram + WhatsApp.
Stack: Python/FastAPI, Postgres/Supabase, Render, API Claude (claude-sonnet-4-6).
Repo: github.com/thompsoncf/openclaw_

Legenda: ✅ no ar e confirmado · 🟡 entregue, falta aplicar/confirmar · 🔴 pendente

---

## ✅ PRONTO E NO AR (confirmado)

**Custo de API (os dois grandes levers)**
- **Prompt caching** (`core/brain.py`) — ~65% de economia no input; logs confirmam `cache_read` constante (visto no log: cache_write 1x, cache_read 2x no loop do cupom).
- **History pruning** (40→16 mensagens) — crescimento de token estabilizado.
- **Métrica `_eh_foto` corrigida** (`core/agent.py`, commit `1e914b2`) — usa `imagem_b64 is not None` em vez de chutar pelo `cache_write`; agora a 2ª+ foto de uma rajada (cache quente) também conta como foto no `uso_api`. Base correta pra precificação.
- **Modelo por economia** (`core/agent.py`) — texto usa Haiku (`MODELO_TEXTO`), imagem usa Sonnet (`MODELO_FOTO`).

**Infra / banco**
- **Trava de email único — APLICADA E TESTADA** (20/jun). Índice `idx_contas_email_unico` (`create unique index on contas(lower(email)) where email is not null`). **Apenas o índice foi aplicado** (sem o `delete`/`set permitir_limpeza` da migração 030, por segurança — dry-run = 0 duplicados). Bloqueia cadastro duplicado (case-insensitive).
- **Deploy 20/jun confirmado** — `openclaw-web-bcu3` e `openclaw-bot-bcu3` no ar com commit `2acf754`, sem erro de startup.
- **Backup diário ativo** no Supabase (15-20/jun confirmados). **PITR não ligado** (add-on pago, adiado pra Fase B). Regra enquanto isso: backup manual antes de QUALQUER migração que mexa em dado.

**Onboarding / acesso**
- **E-mail de boas-vindas** integrado ao cadastro (SPF/DKIM/DMARC no DNS).
- **Link de convite WhatsApp** na tela /bem-vindo.

**Pagamento / monetização (Asaas) — PONTA A PONTA NO AR E TESTADO** ✅ (deploy 20/jun, commit `2acf754`)
- **Fluxo completo TESTADO** no log: PIX no sandbox → webhook → `consultar_pagamento` confirma `RECEIVED` na API → conta ativada sozinha. **Confirmado com a conta 5** (Vicente Parente): log `ASAAS conta 5 ATIVADA`, e no banco `status=ativa` + `plano=pf_individual` (código certo do banco, não o texto do Asaas).
- **Idempotência confirmada**: Asaas reentrega o evento, mas `reivindicar_mensagem` barra o reprocessamento (não ativa 2x).
- **Webhook robusto** (`web/app.py`): parse de `application/x-www-form-urlencoded` (o Asaas embrulha o JSON em `data=`), token tolerante (sandbox vazio / produção com header), sempre responde 200 (não penaliza a fila), corpo vazio/ping tratado.
- **Confirmação na API antes de ativar** (`finance/asaas.py::consultar_pagamento`, commit `dc4c4a8`) — usa `_base_url()` (sandbox/produção via `ASAAS_AMBIENTE`); protege contra webhook forjado.
- **Plano vem do banco**, não do `description` do Asaas (`select plano from contas`).
- **Log limpo** (commit `186b33f`): rastro `ASAAS >> evento=... conta=... valor=...` por evento real; pings em `debug`.
- _Decisão registrada:_ avaliado trocar pra Stripe — **descartado**. Stripe não faz PIX recorrente (Pix Automático) pra conta BR; só cartão + PIX avulso (invite-only). Asaas faz PIX recorrente nativo em BRL. Ficar no Asaas foi o certo.

**Painel financeiro (web)**
- **Editor de categoria** por lançamento (dropdown filtra por tipo, salva sem reload).
- **Apagar lançamento** (botão ✕ por linha, multi-tenant safe, limpa preços do item).
- **Raio-x por lista branca** — só mostra Mercado / Saúde / Restaurante / Pet.

**Banco de preços (colaborativo)**
- **Escala/agregação por mediana** (migração 017: `precos_vigentes`).
- **Admin: Categorias + Banco de preços** (`finance/estatisticas.py`): uso por categoria, contadores de cupons/produtos/lojas, "preços mais confirmados".
- **Métricas do banco** (commit `c6bc2d4`):
  - `pronto_para_fase_b(pool)` — gatilho da Fase B (observações, produtos, lojas, % confirmado, cobertura GTIN) + contexto (produtos multi-loja, cupons faltando estimado).
  - `estatisticas_leituras_qr(pool)` — assertividade do QR por tipo (Foto × PDF) + KB médio acerto/falha.
- **Alerta de progresso no Telegram — TESTADO** (commit `ecebf35`). **Confirmado 20/jun:** mensagem com barras de progresso chegou no Telegram do dono. Falta só o cron diário (Claude Code).

**Qualidade do dado de cupom**
- **Trava de duplicidade por chave NFC-e** (migração 018) — cupom repetido nem chama a API.
- **Camada 1 — nomes legíveis** (FD→Fralda etc., custo zero).
- **Camada 2 Parte 1 — captura de EAN/GTIN** (migração 019) — **CONFIRMADA COM FOTO REAL** ✅. Query real retornou GTIN-13 válido + `loja_id` preenchido + descrição legível (ex: "Requeijão Cremoso Forno de Minas 200g" → `7891000084663`, loja 1). Encerra a antiga pendência #1.

---

## 🟡 ENTREGUE — FALTA APLICAR / CONFIRMAR

- **Painel admin: 2 seções novas** (Claude Code, `web/`) — renderizar `pronto_para_fase_b` (barras de progresso + veredito) e `estatisticas_leituras_qr` (tabela Foto/PDF). Camada de dados pronta; falta a tela.
- **Cron diário do alerta** (Claude Code, infra) — chamar `finance.notificar.alerta_fase_b(pool, sempre=True)` 1x/dia. `ADMIN_TELEGRAM_ID` já setado e testado (mensagem chegou). Sugestão: 9h Teresina (12h UTC).
- **Limite SEPARADO de cupons/dia** (migração 020) — cria a capacidade; ainda NÃO liga aos planos nem mexe em preço.
- **Cliente Data Market** (`finance/datamarket.py`) — ENGAVETADO de propósito (dado raso, SP/online). Só faz sentido no futuro.

---

## 🔴 PENDÊNCIAS / PRÓXIMOS PASSOS (em ordem)

**1. Encher o banco de ouro (Fase A — em andamento)**
Cupom com QR → Sonnet, item a item, alimentando o banco. NÃO otimizar isso agora.
Estado atual: ~64 observações cruas, 64 produtos distintos, 5 lojas, **0% confirmado** (nenhum produto visto 2x), 41% cobertura GTIN, 5 cupons com QR (~13 itens/cupom). Faltam ~89 cupons pro volume.

**2. Fechar a PRECIFICAÇÃO** (o que trava a monetização)
- Levar `limite_mensagens_dia` + `limite_cupons_dia` pra tabela `planos`.
- Planos: **Básico 5 / Família 10 / Pro 15 / Business 25** cupons/dia.
- Cadastro/upgrade copiar limites pra conta.
- Preços: **R$29 / R$49 / R$89 / R$149** (validar com fatura real antes de fixar).

**3. Validar números com fatura real** (1-2 semanas)
Custo de API por cupom na fatura Anthropic + Twilio; calibrar limites/preços pelo real.

**4. Camada 2 Parte 2 — comparador por GTIN**
Agrupar por `coalesce(gtin, descricao_norm)`. Só com EAN em volume. Resolve "roso↔roxo" (mesmo GTIN colapsa) e "catchup 397↔567" (GTIN diferente separa). GTIN já confirmado entrando bem.

**5. Backfill da loja órfã** — `scripts/backfill_lojas_qr.py` no Render.

---

## 📐 ESTRATÉGIA DE CUSTO — Fase A vs Fase B

**Fase A (AGORA):** cupom com QR no Sonnet enchendo o banco. Margem protegida pelo **limite de cupons/dia**, não por trocar modelo. Já feito: `_eh_foto` + métricas/gatilho.

**Fase B (FUTURO — só com o banco cheio):** raio-x opt-in (recurso premium por plano) + Haiku no cupom simples + comprovante. Detecção é **grátis e local** (QR no servidor): com chave → cupom fiscal → Sonnet; sem chave → comprovante/cupom-foto-ruim → Haiku (não alimenta o banco, seguro mexer).

**Gatilho numérico da Fase B** (calibrável em `GATILHOS_FASE_B`):
| Métrica | Gatilho |
|---|---|
| Observações cruas | ≥ 1.200 |
| Produtos distintos | ≥ 400 |
| Lojas distintas | ≥ 6 |
| % confirmado (≥2 leituras) | ≥ 35% ← **gargalo real** |
| Cobertura GTIN | ≥ 40% (já em 41%) |

O **% confirmado** (repetição do mesmo produto) é o que dá valor ao comparador e o que mais vai demorar. Acompanhar via alerta/painel.

---

## Princípios que guiam o trabalho
- **Verificar o código REAL** (git clone / `inspect.getsource` no Render), nunca a memória.
- **Fronteira de ferramentas:** `finance/`, `core/`, `scripts/`, `tests/` = este assistente. Commit/deploy, `web/`, `contas/`, webhooks, migrações, infra = Claude Code.
- **Entrega em `finance/`/`core/` via SCRIPT à prova de falha** (escreve/anexa bytes exatos, valida, commita). Upload de arquivo modificado já falhou no deploy 3x; reescrita pelo Claude Code já dropou função (`consultar_pagamento`).
- **Cupom (imagem) custa ~5x texto** — limite separado protege a margem.
- **Decisões de custo/limite saem do log real**, não de estimativa.
- **Migração = perigo:** truncate já esvaziou o banco 3x. Conferir duplicados antes de índice único; PITR sempre ligado.
