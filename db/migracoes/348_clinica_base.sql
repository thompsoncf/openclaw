-- 348_clinica_base.sql
-- Fase 1 da clínica ("base"), aprovada em docs/mockups/clinica_visao_geral.html
-- (seção 10) e desenhada em clinica_prototipo.html (Configurar › Profissionais,
-- Atendimentos, Locais e Grade). É o cadastro que tudo depois usa: a agenda do dia
-- (fase 2), o agente oferecendo horário (fase 3) e o roteiro por cidade (fase 4).
--
-- O ZAQ É A AGENDA DA CLÍNICA. Em 25/09/2026 o dono decidiu que o Amigo sai; não
-- há integração nem duas agendas pra conciliar.
--
-- PROFISSIONAL NÃO É MEMBRO. `membros` é quem usa o sistema (dono, gestor,
-- vendedor). O médico tem agenda mesmo sem login — o Dr. Manoel talvez nunca abra
-- o Zaq. Quando o profissional também entra no sistema, `membro_id` liga os dois.
--
-- TIPO DE ATENDIMENTO É UMA LINHA DO CATÁLOGO (servicos_catalogo), não tabela
-- nova: o agente já lê o catálogo pra responder preço (`agente._linha_catalogo`),
-- e o preço particular mora em `setup_centavos` — zero é "sob consulta", nunca
-- grátis. A categoria da clínica (consulta, retorno, procedimento, cirurgia,
-- exame, sessão) vai na coluna `categoria` que já existe (148).
--
-- Nada aqui guarda dado de saúde: é quem atende, onde, quando e quanto custa.
--
-- Aditiva e idempotente.

create table if not exists public.clinica_profissionais (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  nome text not null,                 -- como aparece na agenda: "Dr. Manoel"
  funcao text,                        -- Dermatologista, Fisioterapeuta..., Recepção
  especialidade text,
  conselho text,                      -- "CRM-MA 12345"; opcional
  cor text,
  membro_id bigint references public.membros(id) on delete set null,
  acesso text not null default 'sem_login'
    check (acesso in ('sem_login','propria','gestor')),
  aviso_agenda boolean not null default false,   -- recebe a agenda do dia às 7h (fase 2)
  ativo boolean not null default true,
  ordem int not null default 0,
  criado_em timestamptz not null default now());
create index if not exists idx_clinica_prof_conta
  on public.clinica_profissionais (conta_id, ativo, ordem);

create table if not exists public.clinica_locais (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  nome text not null,                 -- "Espaço Pelle", "Bacabal"
  endereco text,                      -- vai na mensagem de confirmação
  cidade text,
  tipo text not null default 'sede' check (tipo in ('sede','viagem')),
  ativo boolean not null default true,
  ordem int not null default 0,
  criado_em timestamptz not null default now());
create index if not exists idx_clinica_locais_conta
  on public.clinica_locais (conta_id, ativo, ordem);

alter table public.servicos_catalogo
  add column if not exists duracao_min int,              -- o fim do horário se calcula sozinho
  add column if not exists cor text,
  add column if not exists volta_dias int,               -- prazo de volta (retorno programado, fase 6)
  add column if not exists volta_motivo text,            -- texto pra RECEPÇÃO, nunca pro paciente
  add column if not exists agente_diz_preco boolean not null default false,
  add column if not exists agente_marca boolean not null default false;

-- quem faz o quê: é o que deixa o agente oferecer horário certo (quem pede botox
-- só recebe horário de quem faz procedimento estético)
create table if not exists public.clinica_profissional_tipos (
  conta_id bigint not null references public.contas(id),
  profissional_id bigint not null references public.clinica_profissionais(id) on delete cascade,
  servico_id bigint not null references public.servicos_catalogo(id) on delete cascade,
  primary key (profissional_id, servico_id));

-- A SEMANA DO PROFISSIONAL. Uma linha por faixa: "seg a sex 08:00–12:00" e
-- "seg a sex 13:30–16:30" são duas linhas. `repete`:
--   semanal    toda semana nos `dias`
--   quinzenal  semana sim, semana não, contando da `referencia`
--   mensal     a n-ésima ocorrência do dia no mês (`semana_do_mes`: 3 = "3ª quinta";
--              5 = a última)
create table if not exists public.clinica_grade (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  profissional_id bigint not null references public.clinica_profissionais(id),
  local_id bigint not null references public.clinica_locais(id),
  dias text not null,                 -- dias ISO, "1,2,3,4,5" (1 = segunda)
  inicio time not null,
  fim time not null,
  repete text not null default 'semanal' check (repete in ('semanal','quinzenal','mensal')),
  semana_do_mes smallint check (semana_do_mes between 1 and 5),
  referencia date,
  encaixes smallint not null default 0 check (encaixes between 0 and 20),
  ativo boolean not null default true,
  criado_em timestamptz not null default now(),
  check (fim > inicio));
create index if not exists idx_clinica_grade_prof
  on public.clinica_grade (conta_id, profissional_id) where ativo;

-- Exceções: congresso, férias, feriado. Sem profissional = a clínica inteira;
-- sem horário = o dia todo.
create table if not exists public.clinica_bloqueios (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  profissional_id bigint references public.clinica_profissionais(id),
  de date not null,
  ate date not null,
  inicio time,
  fim time,
  motivo text,
  criado_em timestamptz not null default now(),
  check (ate >= de),
  check ((inicio is null and fim is null) or (inicio is not null and fim is not null and fim > inicio)));
create index if not exists idx_clinica_bloq_conta
  on public.clinica_bloqueios (conta_id, de, ate);

-- rollback:
--   drop table if exists public.clinica_bloqueios;
--   drop table if exists public.clinica_grade;
--   drop table if exists public.clinica_profissional_tipos;
--   alter table public.servicos_catalogo drop column if exists duracao_min,
--     drop column if exists cor, drop column if exists volta_dias,
--     drop column if exists volta_motivo, drop column if exists agente_diz_preco,
--     drop column if exists agente_marca;
--   drop table if exists public.clinica_locais;
--   drop table if exists public.clinica_profissionais;
