# PLANO_DOMESTICA.md — Módulo Doméstica (eSocial Doméstico) · design

> **Status:** design aprovado, **zero código escrito**. Este arquivo é o plano
> pra o próximo builder. Segue a régua do `PLANO.md` (lucro/economia do cliente)
> e os guard-rails de dinheiro real.

---

## 0. A restrição que define o escopo (não negociável)

Não existe API / webservice / webhook no **eSocial Doméstico**. O empregador só
fecha pelo eSocial Web, com login gov.br prata/ouro, e a orientação oficial
**proíbe repassar senha a terceiros**. Portanto:

- ❌ **Nunca** implementar envio, transmissão, RPA no portal ou guarda de
  credencial gov.br.
- ✅ O módulo **espelha o cálculo**: antecipa a DAE, confere a guia emitida,
  lança no livro-caixa pessoal, gera recibo e informe.
- ✅ **Copy honesta**, igual ao "não substitui o contador" do PJ:
  *"cálculo de conferência; o fechamento continua no portal gov.br."*

Concorrentes (Hora do Lar, Doméstica App, Doméstica Legal) fazem exatamente
isso e chamam de "integrado ao eSocial". Ninguém tem vantagem de pipe — o
diferencial é o **custo real com provisões** (§7), não a transmissão.

---

## 1. Onde encaixa: é módulo do **PF/pessoal**, não do PJ

O empregador doméstico é **pessoa física**. Consequência de arquitetura:

- Os lançamentos gerados usam **`natureza="pessoal"`** (já existe no código,
  ver `livro_caixa`/`empresa`), caindo no **livro-caixa / DRE pessoal** — nunca
  no empresarial (`natureza="empresa"`).
- Por isso ela vive no "zaq PF", ao lado do financeiro pessoal, e **não** herda
  o `tipo_conta='pj'`.
- **Babá, caseiro, cuidador(a), motorista particular, cozinheira, jardineiro,
  governanta, diarista mensalista = a MESMA categoria** ("empregado doméstico",
  **LC 150/2015**: 3+ dias/semana pra família, sem fim lucrativo). Mesmo eSocial
  Doméstico, mesma DAE, mesmo cálculo. **Um módulo só** cobre todos — o que muda
  é apenas o campo `cargo` do vínculo. Não há sub-módulo "babá".

---

## 2. Fronteira com o PJ — **PJ intocado**, importar só o estável

**Decisão (dono):** a folha PJ (`empresa.py`) **NÃO é tocada agora** — nenhum
edit, nenhuma refatoração. Ela está viva e em evolução (commits recentes `#90`
holerite pronto pra imprimir, `#91` adiantamento/benefício/VT); acoplar a um
alvo que se mexe é pedir regressão. "Incrementar lá" fica pra um refactor
deliberado no futuro, com os dois módulos já estáveis.

Régua de reuso: **importa o estável, espelha o que muda, nunca edita o PJ.**

| | Como | Toca no PJ? |
|---|---|---|
| ✅ **Importar** (read-only) | só o genérico e **estável** — ex. `formatar_brl` (já mora em `models.py`, util compartilhado) | Não |
| 🪞 **Espelhar** (copiar a forma, código local) | o que **muda** ou é volátil — layout do recibo (em evolução no PJ), transação atômica do fechamento, eventos por competência | Não |
| ❌ **Não reusar** | o **cálculo** — bases/encargos diferentes (ver tabela abaixo); as funções de INSS/FGTS do PJ não servem | — |

Por que o cálculo **não** se reusa:

| Domínio | PJ (`empresa.py`) | **Doméstica** |
|---|---|---|
| Empregador | CNPJ | **Pessoa física** |
| Regime | Simples → INSS patronal **dentro do DAS** (`FATOR_ENCARGOS` omite patronal) | **Simples Doméstico → DAE única** |
| Encargos | FGTS 8% + provisão 13º + provisão férias | INSS patronal 8% + **GILRAT 0,8%** + FGTS 8% + **FGTS compensatório 3,2%** + IRRF retido |
| Arredondamento | `round` | **truncado em 2 casas** (67,776 → 67,77) |
| Natureza do lançamento | `"empresa"` | **`"pessoal"`** |

O motor `domestica.py` sobe como **stdlib pura, zero import do repo,
autoverificável** — o cálculo bate centavo a centavo contra o demonstrativo real
(competência **11/2025**, 2 empregadas). Regra do handoff mantida: **se algo não
encaixar, reportar em vez de reescrever** — a autoverificação é a prova.

O gate (`conta_modulos`, §5), o padrão de tools (`tools_pj.py` como molde, §6) e
a visão-de-agente do cupom NFC-e (§8) entram como **molde a espelhar**, não como
import de `empresa.py`.

---

## 3. Motor `finance/domestica.py` (puro, autoverificável)

Regras não-óbvias que o motor implementa e valida contra o PDF real:

- INSS **empregado** progressivo por faixa.
- **Desconto simplificado (R$ 607,20)** *substitui* o INSS na base do IRRF, não
  soma: `1.412 − 607,20 = 804,80`.
- **Adiantamento do 13º** entra na base do FGTS mas **não** na do INSS/IRRF
  (base FGTS 2.118 vs base INSS 1.412).
- Encargos patronais **truncados** em 2 casas (67,776 → 67,77; 11,296 → 11,29).
- **Redutor da Lei 15.270/2025** para 2026+ (isenta até R$ 5.000, decresce até
  R$ 7.350).
- Tabelas **2025 e 2026 versionadas por ano** em `TABELAS` — nunca hardcode fora.

**API pública (contrato — não mudar sem versionar):**
`Vinculo`, `Lancamentos`, `calcular_empregado()`, `calcular_folha()`,
`lancamentos_livro_caixa()`, `texto_resumo()`.

**Autoverificação:** `python -m finance.domestica` → 25 checks, todos verdes.

---

## 4. Migração — número **094** (não 069)

> O handoff dizia "069", mas as migrações já vão até **093**
> (`093_folha_beneficios_e_org.sql`) e **069 já está ocupado**
> (`069_orcamento_cnpj.sql`). A próxima livre é **094**. Depois de aplicar,
> inserir manual em `public.schema_migrations (nome)`.

Estilo 053: **só tabelas novas**, zero `alter` em tabela existente, tudo escopado
por `conta_id`. Três tabelas + registro no catálogo `modulos`.

```sql
-- 094_modulo_domestica.sql
-- Módulo Doméstica (eSocial Doméstico) — cálculo de CONFERÊNCIA.
-- Empregador PF: os lançamentos caem no livro-caixa PESSOAL (natureza='pessoal').
-- Só tabelas NOVAS: rodar não muda nada pra quem está no ar.

-- ── Vínculo empregatício doméstico ──────────────────────────────────────────
create table if not exists domestica_vinculo (
    id                bigserial primary key,
    conta_id          bigint not null references contas(id) on delete restrict,
    nome              text   not null,
    cpf               text   not null default '',
    cargo             text   not null default '',   -- babá, caseiro, diarista mensalista...
    salario_centavos  int    not null check (salario_centavos >= 0),
    admitido_em        date,
    ativo             boolean not null default true,
    criado_em         timestamptz not null default now()
);
create index if not exists idx_domestica_vinculo_conta
    on domestica_vinculo (conta_id, ativo);

-- ── Folha por competência (cabeçalho / o "recibo do mês") ────────────────────
create table if not exists domestica_folha (
    id                bigserial primary key,
    conta_id          bigint not null references contas(id) on delete restrict,
    vinculo_id        bigint not null references domestica_vinculo(id) on delete cascade,
    competencia       date   not null,               -- 1º dia do mês
    -- snapshot do cálculo (centavos) pra o recibo não mudar se a tabela mudar:
    base_inss_centavos        int not null default 0,
    base_fgts_centavos        int not null default 0,
    base_irrf_centavos        int not null default 0,
    inss_empregado_centavos   int not null default 0,
    inss_patronal_centavos    int not null default 0,
    gilrat_centavos           int not null default 0,
    fgts_centavos             int not null default 0,
    fgts_compensatorio_centavos int not null default 0,
    irrf_centavos             int not null default 0,
    dae_prevista_centavos     int not null default 0, -- soma dos tributos da DAE
    liquido_centavos          int not null default 0,
    custo_real_centavos       int not null default 0, -- desembolso + provisões (§7)
    vencimento_dae            date,
    -- conferência: prevista (nosso cálculo) → conferida (bateu com a guia)
    status            text   not null default 'prevista'
                      check (status in ('prevista','conferida','paga')),
    tabela_ano        int    not null,               -- versão da TABELA usada
    lancamento_id     bigint references lancamentos(id) on delete set null,
    criado_em         timestamptz not null default now(),
    unique (conta_id, vinculo_id, competencia)
);
create index if not exists idx_domestica_folha_conta
    on domestica_folha (conta_id, competencia);

-- ── Variáveis do mês (horas-extra, DSR, faltas, adiant. 13º, benefícios) ─────
create table if not exists domestica_folha_item (
    id                bigserial primary key,
    conta_id          bigint not null references contas(id) on delete restrict,
    folha_id          bigint not null references domestica_folha(id) on delete cascade,
    tipo              text   not null
                      check (tipo in ('extra','dsr','falta','adiantamento_13',
                                      'desconto','beneficio','adiantamento')),
    descricao         text   not null default '',
    valor_centavos    int    not null,
    criado_em         timestamptz not null default now()
);
create index if not exists idx_domestica_folha_item_folha
    on domestica_folha_item (folha_id);

-- ── Módulo no catálogo (liberado por conta via conta_modulos = cortesia/admin) ─
insert into modulos (codigo, nome, preco_centavos)
values ('domestica', 'Módulo Doméstica (eSocial)', 0)
on conflict (codigo) do nothing;
```

> Validar no parser real do Postgres (`pglast`) antes de aplicar, como foi feito
> com a 053/069.

---

## 5. Gate — via `conta_modulos` (decisão tomada)

Nada de novo `tipo_conta` nem mexer em planos/cobrança. Segue o mesmo override
de cortesia do PJ. Ponto único de decisão:

```python
def modulo_domestica_ativo(pool, conta_id: int) -> bool:
    """True se a conta tem o módulo Doméstica liberado (override admin/cortesia).
    Diferente do PJ, NÃO é automático por plano — é sempre opt-in por conta,
    porque o empregador doméstico é PF e não tem tipo_conta próprio."""
    with pool.connection() as c:
        r = c.execute(
            "select 1 from conta_modulos "
            "where conta_id=%s and modulo='domestica' and ativo",
            (conta_id,)).fetchone()
    return r is not None
```

Função isolada de propósito: se um dia virar plano/flag, troca-se só aqui.

---

## 6. Persistência + tools do agente

**Persistência** (`finance/domestica_repo.py`, ou dentro do módulo conforme o
padrão do repo): CRUD do vínculo, lançar variáveis, fechar a competência.
Lembrar: **`conta_logada(request)` devolve tupla, o id é `conta[0]`**. O
fechamento espelha `empresa.pagar_folha()` — **atômico**: snapshot da folha +
lançamento no caixa pessoal numa única transação.

**Tools do agente** no padrão `tools_pj.py` (`Ferramenta`, `bloco_persona_*`),
plugadas **exatamente** como o PJ em `agente_financeiro.py` (bloco `if papel ==
"dono"`, dentro de `try/except` que **nunca derruba o agente PF**):

```python
if _dom.modulo_domestica_ativo(pool, conta_id):
    from .tools_domestica import bloco_persona_domestica, construir_ferramentas_domestica
    persona = persona + bloco_persona_domestica(pool, conta_id)
    ferramentas = ferramentas + construir_ferramentas_domestica(pool, conta_id, membro_id)
```

Ferramentas mínimas (Fase 1):
1. `cadastrar_vinculo` — nome, CPF, cargo, salário, admissão.
2. `lancar_variaveis_mes` — extra/DSR/falta/adiant. 13º/benefício da competência.
3. `mostrar_dae_prevista` — nosso cálculo da DAE + vencimento.
4. `fechar_folha` — snapshot + lançamento pessoal (atômico).
5. `conferir_dae_oficial` — recebe os campos do demonstrativo e bate contra o
   previsto (marca `status='conferida'`, aponta divergência centavo a centavo).

---

## 7. Provisões — **é aqui que está o valor**

O eSocial mostra **desembolso**; o ZAQ mostra **custo real**. Na folha de teste:

| | Valor |
|---|---|
| Desembolso (o que a DAE + líquido tiram do bolso) | **R$ 5.713,46** |
| Custo real (com provisão de 13º e férias+1/3) | **R$ 6.509,58** |
| Diferença que estoura em dezembro | **~R$ 800/mês** |

Esse delta vai pro **DRE/raio-x pessoal** (`natureza="pessoal"`), não só pro
recibo. É o que move a régua "economia do cliente" do PLANO — o dono da casa
passa a **enxergar** o custo antes de dezembro.

---

## 8. Leitura do demonstrativo — o diferencial de conferência

**Realidade técnica:** não há OCR dedicado no repo. O `nfce_qr.py` só extrai a
**chave** do QR; o parse dos itens do cupom é **visão do próprio agente** (o
modelo lê a foto e chama `registrar_itens_cupom` com campos estruturados).

Reaproveitar essa pipeline pro PDF do eSocial = dar o PDF/imagem ao agente e
expor a tool `conferir_dae_oficial(...)`, que recebe os campos **já parseados**
(empregado, bases, FGTS, INSS patronal, GILRAT, IRRF, líquido, vencimento) e
cai direto na conferência + livro-caixa. O formato do demonstrativo é **estável
e bem mais limpo** que cupom amassado — funciona bem. (Internamente: é
visão-de-agente, não "OCR" — não vender como OCR.)

---

## 9. Tela web — política de consolidação

Seguir o padrão de `web/painel_equipe.py` (que reusa `_render`, `_env`,
`conta_logada` do `portal`):

- tokens CSS do `:root` do `_BASE`;
- pills `.aba/.abas`, nav agrupada com `secao_ativa` (nova seção `domestica`);
- **sem `location.reload()`**;
- escopo sempre por `conta[0]`.

Abas: **Empregados** (vínculos) · **Folha do mês** (variáveis + DAE prevista) ·
**Conferência** (subir demonstrativo, bater com o previsto) · **Custo real**
(desembolso × provisões).

---

## 10. Pendências e decisões de produto

- **Piso salarial 2026 = R$ 1.621,00** — hoje o motor passa direto. **Decidir:
  avisa ou bloqueia** na validação de salário.
- **Validar 2026 contra DAE real** — INSS e IRRF batem em três fontes e nos dois
  métodos, mas há erro publicado em blog grande (Contabilizei publicou INSS de
  R$ 3.000 como 263,06 quando é **248,60**). **Antes de virar produto: fechar uma
  competência real de 2026 e bater com a guia emitida.**
- **Rescisão — Fase 2** (aviso prévio, saldo de salário, férias proporcionais,
  multa 40% **com abatimento do compensatório**). Não implementada.

---

## 11. Ordem de execução sugerida (quando sair do "só design")

1. Subir `finance/domestica.py` **verbatim** (não reescrever) + rodar
   `python -m finance.domestica` (25 checks verdes).
2. Migração **094** validada no `pglast` → aplicar → `schema_migrations`.
3. `modulo_domestica_ativo()` + persistência (`domestica_repo.py`).
4. Tools do agente + plug em `agente_financeiro.py` (try/except isolado).
5. Tela web (`web/painel_domestica.py`) seguindo §9.
6. Provisões no DRE/raio-x pessoal.
7. Validar 2026 contra guia real → só então promover de piloto a produto.
