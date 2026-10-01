-- 458_novidade_relatorios_periodo_especifico.sql
-- O "Período específico" em todos os Relatórios (pedido do dono em 01/10/2026:
-- "coloca uma opção por período"), seguindo a seção 5 do CLAUDE.md.
--
-- O QUE MUDOU NA TELA: o seletor de período de Vendas, Contas pagas, Contas
-- recebidas, Comissão, Orçamentos, Contratos, Leads e conversão e Funil ganha
-- "Período específico…", com as duas datas — antes só a Agenda tinha. Trocar de
-- aba leva as datas junto. E "Últimos 90 dias" passa a ter 90 dias (eram 91).
--
-- PÚBLICO `todos`: os Relatórios existem pra qualquer conta. PRA QUEM: dono e
-- gestor — o vendedor não abre Relatórios.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('relatorios-periodo-especifico', 'novidade', 'todos', '{dono,gestor}',
 'Relatórios com o período que você escolher',
 'Todos os relatórios agora aceitam um período específico, de uma data a outra.',
 '/painel/relatorios',
 $txt$No seletor de período dos Relatórios, além de "Este mês", "Mês passado", "Últimos 90 dias", "Este ano" e "Todo o período", agora tem "Período específico…": escolha de que data até que data e clique em Filtrar.

Vale em Vendas, Contas pagas, Contas recebidas, Comissão, Orçamentos, Contratos, Leads e conversão e Funil (a Agenda já tinha). Ao trocar de aba, as datas vão junto, e o PDF sai com o mesmo período.

"Este mês" continua indo do dia 1º até hoje. "Últimos 90 dias" agora conta exatamente 90 dias, contando hoje.$txt$,
 timestamptz '2026-10-01 20:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'relatorios-periodo-especifico';
