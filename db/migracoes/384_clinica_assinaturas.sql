-- 384_clinica_assinaturas.sql
-- Fase 7b da clínica: ASSINATURA DA CLÍNICA (docs/mockups/clinica_planos_pacotes_assinatura.html,
-- seção 05, "o que faz o faturamento não depender do movimento do mês").
-- finance/clinica_assinaturas.py, tela /painel/clinica/assinaturas.
--
--   * O PLANO é cadastrado pela clínica (nome, mensalidade, dia de cobrança) com o que
--     ele dá: sessões inclusas por mês de um atendimento do catálogo, desconto em
--     procedimentos (vale no plano de tratamento), desconto em produtos (fase 7c) e
--     prioridade na fila de vagas liberadas.
--   * O ASSINANTE guarda o preço e o dia do momento em que assinou (mudar o plano
--     depois não muda o que foi combinado).
--   * A MENSALIDADE de cada mês vira um título a receber (o caixa de verdade é o
--     Financeiro). A cobrança automática no cartão fica para quando a clínica tiver a
--     própria conta no Asaas: o ASAAS_API_KEY de hoje é o do Zaq, e o dinheiro do
--     paciente não pode cair nele.
--   * A SESSÃO INCLUSA usada fica registrada com o atendimento (uma por atendimento),
--     e não baixa do pacote.
--
-- Aditiva e idempotente.

create table if not exists public.clinica_assinatura_planos (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  nome text not null,
  preco_centavos integer not null check (preco_centavos > 0),
  dia_cobranca smallint not null default 10 check (dia_cobranca between 1 and 28),
  servico_id bigint,                       -- o atendimento incluso (do catálogo); null = nenhum
  sessoes_mes smallint not null default 0 check (sessoes_mes between 0 and 10),
  desconto_procedimento_pct numeric(5,2) not null default 0 check (desconto_procedimento_pct between 0 and 50),
  desconto_produto_pct numeric(5,2) not null default 0 check (desconto_produto_pct between 0 and 50),
  prioridade_vagas boolean not null default true,
  ativo boolean not null default true,
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now());
create index if not exists clinica_assinatura_planos_conta on public.clinica_assinatura_planos (conta_id);

create table if not exists public.clinica_assinantes (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  plano_id bigint not null references public.clinica_assinatura_planos(id),
  prospeccao_id bigint references public.prospeccao(id) on delete set null,
  paciente_nome text not null,
  paciente_fone text not null default '',
  cliente_id bigint,
  preco_centavos integer not null check (preco_centavos > 0),
  dia_cobranca smallint not null check (dia_cobranca between 1 and 28),
  inicio date not null,
  estado text not null default 'ativa' check (estado in ('ativa','cancelada')),
  cancelada_em timestamptz,
  cancelada_motivo text,
  cancelada_por bigint,
  criado_por bigint,
  criado_em timestamptz not null default now());
create index if not exists clinica_assinantes_conta on public.clinica_assinantes (conta_id, estado);
-- um paciente (card) tem no máximo uma assinatura ativa
create unique index if not exists clinica_assinantes_um_ativo
  on public.clinica_assinantes (conta_id, prospeccao_id) where estado = 'ativa' and prospeccao_id is not null;

create table if not exists public.clinica_assinatura_mensalidades (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  assinante_id bigint not null references public.clinica_assinantes(id) on delete cascade,
  competencia date not null,               -- o 1º dia do mês
  vencimento date not null,
  valor_centavos integer not null,
  titulo_id bigint,
  criado_em timestamptz not null default now(),
  unique (assinante_id, competencia));
create index if not exists clinica_assinatura_mensalidades_conta on public.clinica_assinatura_mensalidades (conta_id, competencia);

create table if not exists public.clinica_assinatura_usos (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  assinante_id bigint not null references public.clinica_assinantes(id) on delete cascade,
  competencia date not null,
  evento_id bigint not null unique,        -- um atendimento usa o benefício uma vez
  criado_em timestamptz not null default now());
create index if not exists clinica_assinatura_usos_assinante on public.clinica_assinatura_usos (assinante_id, competencia);

-- rollback:
--   drop table if exists public.clinica_assinatura_usos; drop table if exists public.clinica_assinatura_mensalidades;
--   drop table if exists public.clinica_assinantes; drop table if exists public.clinica_assinatura_planos;
