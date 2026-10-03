-- 202610031459_obra_inventario_rotativo.sql
-- O QUE FAZ: a contagem do inventário rotativo do CD — o que o sistema dizia, o
--   que foi contado, o motivo da diferença e o ajuste que ela virou.
-- POR QUÊ: PR 3b do CD das obras (docs/mockups/obras_cd_almoxarifado.html, aba
--   "Inventário"), aprovado pelo dono em 03/10/2026: conta-se pouco todo dia,
--   classe A primeiro, e a diferença vira ajuste com motivo.
--
--   obra_contagens   uma por material contado: sistema × contado, o motivo
--                    (quebra, perda, furto, erro, achado), o ajuste gravado no
--                    estoque_mov (tipo 'ajuste', com sinal) e o valor da
--                    diferença pelo preço da nota NA HORA (o indicador de perdas
--                    não muda quando o preço muda depois)
--
-- A curva ABC, a contagem do dia e os indicadores são calculados — sem cadastro.
--
-- Aditiva e idempotente (if not exists). Banco SEM o estoque (sem a 032, como a
-- base antiga do test_blindagem_migracoes): pula inteira — o código trata a
-- tabela ausente como "nada contado".

do $$
begin
  if to_regclass('public.estoque_mov') is null then
    raise notice 'inventário do CD: sem estoque_mov (032) — nada a fazer';
    return;
  end if;

create table if not exists public.obra_contagens (
  id               bigserial primary key,
  conta_id         bigint not null references public.contas(id) on delete cascade,
  produto_id       bigint not null references public.catalogo_produtos(id),
  sistema          numeric(12,3) not null,
  contado          numeric(12,3) not null,
  motivo           text check (motivo in ('quebra','perda','furto','erro','achado')),
  mov_id           bigint references public.estoque_mov(id) on delete set null,
  valor_centavos   bigint,
  contado_por      bigint references public.membros(id) on delete set null,
  contado_em       timestamptz not null default now()
);
create index if not exists idx_obra_contagens_conta on public.obra_contagens (conta_id, contado_em desc);
create index if not exists idx_obra_contagens_produto on public.obra_contagens (conta_id, produto_id, contado_em desc);
end $$;

-- rollback:
--   drop table if exists public.obra_contagens;
--   (os ajustes ficam no estoque_mov com motivo 'inventário: …')
