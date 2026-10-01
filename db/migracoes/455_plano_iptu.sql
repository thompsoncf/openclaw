-- 455_plano_iptu.sql
-- 5.1.14 IPTU, em Despesas Operacionais.
--
-- Pedido do dono em 01/10/2026: "Cria pra mim por favor no plano de contas uma
-- conta para desp. Com IPTU". Conferido antes (só leitura no repositório): não
-- existia conta de IPTU em nenhuma migração; a mais próxima é a 2.1.01 'Impostos
-- sobre Vendas (Simples/ISS/ICMS)', que é imposto sobre VENDA (deduz a receita,
-- grupo 2) — IPTU é imposto sobre o IMÓVEL, não sobre venda nenhuma.
--
-- POR QUE NO GRUPO 5, NÃO NO 2
-- Grupo 2 (Deduções da Receita) é só o que sai de cima da venda. IPTU não varia
-- com venda — é custo fixo de ocupar o imóvel, mesma família da 5.1.01 'Aluguel e
-- Condomínio'. Fica como conta própria (não dentro de Aluguel e Condomínio)
-- porque IPTU tem guia e vencimento próprios, separado do boleto do aluguel —
-- igual critério que já separa Material de Limpeza de Manutenção (143).
--
-- POR QUE ENTRA PRA TODO MUNDO
-- O plano de contas é GLOBAL (finance/plano_contas.py): uma árvore só, cada
-- empresa liga ou desliga o que usa, e ausência de linha em
-- `plano_conta_habilitada` quer dizer habilitada. Mesmo caminho da 143, 186, 336,
-- 351 e 453. Quem não paga IPTU (ex.: não tem imóvel próprio nem alugado) desliga
-- em Empresa.
--
-- Nada é reclassificado (regra 0 do CLAUDE.md). Aditiva e idempotente; a `ordem`
-- é recalculada por `order by codigo`, como na 143, 351 e 453.

insert into public.plano_contas (codigo, nome, grupo, natureza, ordem) values
  ('5.1.14', 'IPTU', 5, 'despesa', 35)
on conflict (codigo) do nothing;

with seq as (
    select id, row_number() over (order by codigo) as n from public.plano_contas
)
update public.plano_contas p
   set ordem = seq.n::smallint
  from seq
 where seq.id = p.id and p.ordem <> seq.n;

-- rollback (manual):
--   delete from public.plano_contas where codigo = '5.1.14';
--   (antes, conferir que nenhum lançamento/título aponta pra ela)
