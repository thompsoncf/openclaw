-- 174_novidades.sql
-- Aviso de atualização pro dono, mirado por nicho.
--
-- POR QUE
-- Uma mudança sobe e a pessoa afetada não sabe. Aconteceu duas vezes em três dias
-- com a mesma conta: a Doce Mell (35) perdeu o botão "Fechar contrato" do funil na
-- #459 e ganhou a agenda de eventos na #448, sem ser avisada de nenhum dos dois.
-- Como cada nicho recebe atualização diferente, isso só piora com o tempo.
--
-- O CHECK DE `publico` É A PRIMEIRA TRAVA
-- Ele recusa valor que não existe — `publico='evento'` (singular, typo) falha aqui,
-- na migração, antes de qualquer conta ver. A lista tem que ser IGUAL às chaves de
-- `finance.novidades.PUBLICOS`, e um teste compara as duas: acrescentar portão de um
-- lado e esquecer o outro vira falha, não surpresa.
--
-- Cada valor é o nome de um portão que JÁ decide quem vê a funcionalidade
-- (vende_servico, tem_contrato…), nunca uma lista de nichos escrita à mão — lista
-- paralela acerta no primeiro dia e diverge no terceiro.
--
-- Aditivo e idempotente.
create table if not exists public.novidades (
    id           bigserial primary key,
    -- chave estável: é ela que torna a inserção idempotente (on conflict do nothing),
    -- então reaplicar a migração não duplica aviso nem "desmarca" quem já leu.
    chave        text not null unique,
    tipo         text not null default 'novidade'
                 check (tipo in ('novidade','mudanca')),
    -- 'novidade' = ganhou algo, marca lida ao abrir.
    -- 'mudanca'  = perdeu algo ou o hábito mudou; exige "Entendi" explícito, e aí
    --              dá pra saber QUEM já viu — que é a pergunta que a Doce Mell criou.
    publico      text not null default 'todos'
                 check (publico in ('todos','produto','servico','eventos','recorrente')),
    titulo       text not null,
    corpo        text not null,
    publicado_em timestamptz not null default now()
);

create index if not exists idx_novidades_pub
    on public.novidades (publicado_em desc, id desc);

create table if not exists public.novidade_lida (
    novidade_id bigint not null references public.novidades(id) on delete cascade,
    conta_id    bigint not null references public.contas(id) on delete cascade,
    -- NULO quando quem leu foi o dono: a sessão dele carrega membro_id nulo
    -- (web/painel_servicos._ator). Chave só por membro perderia justamente quem
    -- mais importa — daí o coalesce no índice único abaixo.
    membro_id   bigint,
    lida_em     timestamptz not null default now()
);

-- por PESSOA, não por conta: numa conta com dono e gerente cada um lê o seu, senão
-- o segundo nunca vê o aviso que o primeiro abriu — e é o segundo que costuma
-- operar a tela.
create unique index if not exists ux_novidade_lida
    on public.novidade_lida (novidade_id, conta_id, coalesce(membro_id, 0));

-- rollback:
--   drop table if exists public.novidade_lida;
--   drop table if exists public.novidades;
