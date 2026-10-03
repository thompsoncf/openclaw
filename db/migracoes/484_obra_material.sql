-- 484_obra_material.sql
-- O controle de material da obra (PR 2 do desenho docs/mockups/obras_mapa_3d.html,
-- aprovado pelo dono em 02/10/2026: "tudo entra da nota; apontar o uso é opcional;
-- um depósito por conta; o custo NÃO é tocado").
--
-- REAPROVEITA O MOTOR DO FORNECEDOR (migração 032: catalogo_produtos +
-- estoque_mov), como a clínica já fez. O que faltava era a dimensão LOCAL:
--
--   estoque_mov.obra_id        onde o material está: a obra, ou NULL = o depósito
--                              central (um por conta — decisão 5 do dono)
--   estoque_mov.transf_id      pareia as duas pernas de uma transferência
--                              ("levei 10 sacos do depósito pra casa 2" =
--                              saída no depósito + entrada na obra)
--   estoque_mov.lancamento_id  a nota que trouxe o material (a entrada nasce dos
--                              itens que o leitor já extrai — migração 016)
--   estoque_mov.item_id        o item exato da nota, ÚNICO: absorver de novo o
--                              mesmo cupom (ou o lote seguinte de um cupom em
--                              partes) não duplica quantidade
--
-- O CUSTO NÃO MUDA DE LUGAR: o dinheiro continua no lançamento, como sempre.
-- As entradas de obra entram SEM custo unitário (o CMP do fornecedor não é
-- tocado) — material é régua de QUANTIDADE; dinheiro é outra régua. E o cache
-- `catalogo_produtos.saldo` também não: o saldo de material é por LOCAL, e sai
-- sempre da soma de estoque_mov (um cache só da conta inteira mentiria, e o
-- cascade acima não teria como acertá-lo).
--
-- Aditiva e idempotente. Banco SEM o estoque (sem a 032, como a base antiga que
-- tests/test_blindagem_migracoes.py simula): pula inteira, sem erro — o código
-- (obra_material._tem_484) já trata a coluna ausente como "sem material".

do $$
begin
  if to_regclass('public.estoque_mov') is null then
    raise notice '484: sem estoque_mov (migração 032) — nada a fazer';
    return;
  end if;

  alter table public.estoque_mov add column if not exists obra_id bigint
      references public.obras(id) on delete set null;
  alter table public.estoque_mov add column if not exists transf_id text;
  -- CASCADE, e não set null: a entrada É da nota. Apagar o lançamento (o "apaga
  -- e lança de novo" que o agente ensina) ou trocar os itens ("substituir") leva
  -- o material junto — senão os 60 sacos ficariam contados pra sempre, e a nota
  -- lançada de novo somaria mais 60. Uso e transferência não têm nota: ficam.
  alter table public.estoque_mov add column if not exists lancamento_id bigint
      references public.lancamentos(id) on delete cascade;
  alter table public.estoque_mov add column if not exists item_id bigint
      references public.itens_lancamento(id) on delete cascade;

  create unique index if not exists ux_estoque_mov_item
      on public.estoque_mov (item_id) where item_id is not null;
  create index if not exists ix_estoque_mov_conta_obra
      on public.estoque_mov (fornecedor_id, obra_id);
  create index if not exists ix_estoque_mov_lanc
      on public.estoque_mov (lancamento_id) where lancamento_id is not null;
end $$;

-- rollback:
--   drop index if exists ix_estoque_mov_lanc;
--   drop index if exists ix_estoque_mov_conta_obra;
--   drop index if exists ux_estoque_mov_item;
--   alter table public.estoque_mov drop column if exists item_id,
--       drop column if exists lancamento_id, drop column if exists transf_id,
--       drop column if exists obra_id;
