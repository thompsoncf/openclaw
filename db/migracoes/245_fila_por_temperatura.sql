-- 245_fila_por_temperatura.sql
-- A fila do vendedor pode ordenar pelos seis níveis do § 7 do Projeto Adaptado da
-- Prime (12/09/2026), em vez de só por prazo.
--
-- O QUE O CLIENTE PEDIU
--   "Além da visualização temporal das conversas, o CRM deverá criar uma fila de
--    prioridade para responder: Quem eu preciso atender agora?
--      1º Leads QUENTES aguardando ação do vendedor.
--      2º Leads com tarefas atrasadas.
--      3º Leads QUENTES com próxima ação vencendo.
--      4º Clientes que responderam e aguardam retorno da equipe.
--      5º Leads MORNOS com possibilidade de avanço.
--      6º Demais leads dentro do fluxo normal."
--
-- POR QUE UM INTERRUPTOR, E NÃO O COMPORTAMENTO NOVO DIRETO
-- Mudar a ordem da fila muda o que três pessoas veem primeiro todo dia de manhã.
-- 'prazo' é exatamente o que a tela faz hoje, então o deploy não mexe na rotina de
-- ninguém; 'temperatura' põe os seis níveis na frente, e quem liga é o dono.
--
-- A ORDEM ANTIGA NÃO SOME quando o modo é 'temperatura': ela vira o desempate
-- DENTRO de cada nível. Sem isso o 1º nível seria uma lista de quentes em ordem
-- aleatória, e a fila trocaria um problema por outro.
--
-- NOT NULL DEFAULT de propósito, ao contrário da migração 228: os MODOS não se
-- herdam do ramo. 'prazo' não é "ainda não escolheu", é o comportamento que a conta
-- já tem — e ligar uma automação é uma declaração sobre a empresa, não sobre o ramo.
--
-- Aditiva e idempotente.

alter table public.funil_regua
  add column if not exists fila_modo text not null default 'prazo';

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'funil_regua_fila_modo_check') then
    alter table public.funil_regua
      add constraint funil_regua_fila_modo_check check (fila_modo in ('prazo','temperatura'));
  end if;
end $$;

comment on column public.funil_regua.fila_modo is
  'ordem da fila do vendedor: prazo (a de sempre) ou temperatura (os 6 níveis do § 7)';

-- rollback:
--   alter table public.funil_regua drop constraint funil_regua_fila_modo_check;
--   alter table public.funil_regua drop column fila_modo;
