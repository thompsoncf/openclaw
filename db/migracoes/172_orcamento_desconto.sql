-- 172_orcamento_desconto.sql
-- O desconto sai do jsonb do evento e vira campo do ORÇAMENTO.
--
-- POR QUE
-- Até aqui existia um desconto só: `evento->>'desconto'`, um inteiro em %, dentro
-- do jsonb que só o modo evento tem. Consequências:
--
--   1. consultoria, advocacia, tecnologia — todo o modo recorrente — não tinham
--      desconto NENHUM, embora vendam por orçamento igual;
--   2. só dava pra descontar em percentual. Quem queria tirar R$ 200 calculava a
--      porcentagem na mão;
--   3. desconto não é atributo de evento. Estava ali porque foi lá que nasceu.
--
-- O DESCONTO POR ITEM mora no `itens` jsonb (chaves `desc_tipo` e `desc` em cada
-- linha), e não em coluna: `itens` já é o SNAPSHOT da linha no momento da
-- proposta — nome, valor unitário, quantidade —, e o desconto daquela linha é
-- parte do mesmo retrato. Coluna separada obrigaria a casar duas listas por
-- índice, que é a armadilha que a 162 existiu pra tirar dos títulos.
--
-- `primeiro_ano_centavos` CONTINUA sendo o total líquido — títulos, DRE, funil e
-- a folha do cliente não mudam de fonte. Só passa a existir mais de um caminho
-- pra chegar nele.
--
-- Aditivo e idempotente.
alter table public.orcamentos
  add column if not exists desconto_tipo text not null default 'pct',
  -- percentual (0–100) quando desconto_tipo='pct'
  add column if not exists desconto_pct numeric(5,2) not null default 0,
  -- centavos quando desconto_tipo='valor'
  add column if not exists desconto_centavos bigint not null default 0;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'ck_orcamentos_desconto_tipo') then
    alter table public.orcamentos
      add constraint ck_orcamentos_desconto_tipo
      check (desconto_tipo in ('pct','valor'));
  end if;
end $$;

-- O QUE JÁ EXISTE. Medido em 16/08/2026: UM orçamento em toda a produção tem
-- desconto — o nº 1 da conta 34, 10%, já fechado. Carrega-se o valor e pronto.
-- A chave velha no jsonb fica onde está, sem uso: enquanto isto estreia, ela é o
-- rollback. Sai numa migração posterior, como foi com as colunas de contrato.
update public.orcamentos
   set desconto_tipo = 'pct',
       desconto_pct  = least(100, greatest(0, (evento->>'desconto')::numeric))
 where evento ? 'desconto'
   and coalesce((evento->>'desconto'), '') ~ '^[0-9]+(\.[0-9]+)?$'
   and (evento->>'desconto')::numeric > 0
   and desconto_pct = 0;

-- rollback:
--   alter table public.orcamentos drop constraint if exists ck_orcamentos_desconto_tipo;
--   alter table public.orcamentos drop column if exists desconto_centavos;
--   alter table public.orcamentos drop column if exists desconto_pct;
--   alter table public.orcamentos drop column if exists desconto_tipo;
