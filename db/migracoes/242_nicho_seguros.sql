-- 242_nicho_seguros.sql
-- Nicho 'seguros' (corretora de seguros): SERVIÇO — ela não tem estoque, ela
-- intermedeia. Casa o slug com finance/nichos.py e com o ramo Seguros de
-- finance/cnpj_info.py. contas.nicho_id é FK -> nichos, então precisa existir na
-- tabela.
--
-- POR QUE AGORA: a conta 37 (Liberal Seguros, adm@liberalseguros.com.br, criada
-- em 09/09/2026) é a primeira corretora da base, e nenhum dos 22 nichos descreve
-- uma. Sem esta linha ela cai em 'generico' e cadastra apólice em kg, caixa e
-- pacote, com categoria livre e o agente falando como PJ genérico.
--
-- O QUE ESTE NICHO TEM DE DIFERENTE, e está escrito na persona: numa corretora
-- quem paga é a SEGURADORA, não o segurado. A receita é comissão — um % do
-- prêmio. É por isso que a persona dela não reusa o `_molde_servico` que
-- advocacia, agência, consultoria e tecnologia compartilham: a linha do molde
-- que diz "o nome do cliente vai na CONTRAPARTE" faria todo título a receber
-- nascer com o nome errado no lugar de quem deve.
--
-- MODO DO ORÇAMENTO: nada a fazer. Fora de `NICHOS_EVENTO` (finance/vendas.py) o
-- modo é 'recorrente', que é o certo — apólice é anual e renova, não é evento
-- com sinal e data segurada.
--
-- Aditiva e idempotente.

insert into nichos (nome, slug, tipo)
select 'Corretora de Seguros', 'seguros', 'servico'
where not exists (select 1 from nichos n where n.slug = 'seguros');

-- rollback:
--   delete from nichos where slug = 'seguros';
