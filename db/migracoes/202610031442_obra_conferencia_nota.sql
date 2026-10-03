-- 202610031442_obra_conferencia_nota.sql
-- O QUE FAZ: a conferência da nota na entrada do CD — o que a nota dizia, o que
--   chegou de verdade, quem conferiu e quando; a diferença fica contra o fornecedor.
-- POR QUÊ: PR 3a do CD das obras (docs/mockups/obras_cd_almoxarifado.html, aba
--   "Entradas"), aprovado pelo dono em 03/10/2026: "a nota diz 60, chegaram 58"
--   é onde o material some ANTES de chegar no estoque.
--
--   obra_conferencias        uma por nota (lançamento) conferida: quem, quando, se
--                            teve divergência, e o fornecedor (o texto da nota)
--   obra_conferencia_itens   cada item: a quantidade da nota e a que chegou, e o
--                            movimento de entrada que foi corrigido
--
-- O CD FICA COM O QUE CHEGOU: a conferência corrige a quantidade da própria
-- entrada no estoque_mov (e guarda a da nota aqui) — assim, se a nota mudar de
-- obra depois, o que vai junto é o que chegou de verdade. Desfazer a conferência
-- devolve a quantidade da nota.
--
-- Aditiva e idempotente (if not exists / on conflict do nothing). Banco SEM o
-- estoque (sem a 032, como a base antiga do test_blindagem_migracoes): pula
-- inteira — o código trata a tabela ausente como "nada pra conferir".

do $$
begin
  if to_regclass('public.estoque_mov') is null then
    raise notice 'conferência da nota: sem estoque_mov (032) — nada a fazer';
    return;
  end if;

create table if not exists public.obra_conferencias (
  id              bigserial primary key,
  conta_id        bigint not null references public.contas(id) on delete cascade,
  lancamento_id   bigint not null references public.lancamentos(id) on delete cascade,
  fornecedor      text   not null default '',
  divergente      boolean not null default false,
  conferido_por   bigint references public.membros(id) on delete set null,
  conferido_em    timestamptz not null default now()
);
create unique index if not exists uq_obra_conferencias_nota
    on public.obra_conferencias (conta_id, lancamento_id);

create table if not exists public.obra_conferencia_itens (
  id               bigserial primary key,
  conta_id         bigint not null references public.contas(id) on delete cascade,
  conferencia_id   bigint not null references public.obra_conferencias(id) on delete cascade,
  mov_id           bigint references public.estoque_mov(id) on delete set null,
  produto_id       bigint not null references public.catalogo_produtos(id),
  nota_qtd         numeric(12,3) not null,
  chegou_qtd       numeric(12,3) not null
);
create index if not exists idx_obra_conferencia_itens on public.obra_conferencia_itens (conferencia_id);
end $$;

-- rollback:
--   drop table if exists public.obra_conferencia_itens;
--   drop table if exists public.obra_conferencias;
