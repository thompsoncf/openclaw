-- 472_novidade_relatorios_datas_do_periodo.sql
-- O período dos Relatórios passa a DIZER as datas (reclamação do dono em
-- 01/10/2026: "quando coloco as datas o sistema não informa os intervalos"),
-- seguindo a seção 5 do CLAUDE.md.
--
-- O QUE MUDOU NA TELA: cada opção do seletor mostra o intervalo ("Este mês (01/10
-- a 01/10)"), o "período:" do topo e do PDF traz as datas exatas, e o "Período
-- específico…" abre com as caixas preenchidas. Em Contas a pagar/receber o
-- seletor some — ali o período nunca filtrou (mostram tudo em aberto).
--
-- PÚBLICO `todos`, pra dono e gestor: os Relatórios existem pra toda conta e o
-- vendedor não abre Relatórios. Sem resumo: ajuste de tela interna.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, link, corpo, publicado_em) values
('relatorios-datas-do-periodo', 'mudanca', 'todos', '{dono,gestor}',
 'Os Relatórios mostram as datas do período',
 '/painel/relatorios',
 $txt$O período dos Relatórios agora diz as datas exatas que estão sendo usadas:

- cada opção do seletor mostra o intervalo — por exemplo, "Este mês (01/10 a 01/10)";
- o "período:" do topo e do PDF traz as datas (na Agenda e no Funil, "Este mês" é o mês inteiro; nos outros, do dia 1º até hoje);
- ao escolher "Período específico…", as caixas já vêm com as datas do período que estava escolhido — é só ajustar e clicar em Filtrar.

Em Contas a pagar e Contas a receber o seletor de período saiu: ali sempre aparece tudo que está em aberto.$txt$,
 timestamptz '2026-10-01 23:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'relatorios-datas-do-periodo';
