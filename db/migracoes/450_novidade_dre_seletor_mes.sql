-- 450_novidade_dre_seletor_mes.sql
-- O aviso do seletor de mês no card do DRE, seguindo a seção 5 do CLAUDE.md.
--
-- Pedido chegou de fora de novo: a Iris (cliente da conta 34, Manoel Soares/
-- Prime) reparou, olhando o CSV do relatório do contador, que só dava pra ver
-- e baixar o mês atual — sem jeito de olhar meses anteriores. O dono repassou
-- em 29/09/2026, aprovou o mockup com "6 meses tá bom" e pediu pra implementar.
--
-- O QUE MUDOU NA TELA. O card "DRE do mês" (`empresa#dre`) ganhou um seletor
-- com os últimos 6 meses (mesmo padrão `?mes=AAAA-MM` que a aba Financeiro já
-- usa). Escolher um mês recarrega SÓ o card — DRE, "ver por centro de custo",
-- "baixar planilha" e "baixar PDF" passam a ser daquele mês. O resto da aba
-- (títulos, folha, "a classificar", planejamento da semana) continua sendo do
-- mês atual de verdade, sem seguir o seletor.
--
-- O PORTÃO: `empresa` (mesmo de 338/448/449) — é quem tem o módulo PJ e vê o
-- card do DRE.
--
-- PRA QUEM: dono e gestor. O vendedor não tem a aba Empresa.
--
-- QUEM RECEBE, conferido na produção em 29/09/2026 (só leitura, módulo PJ
-- ativo — mesma consulta da 338/448/449):
--   3 Thompson · 7 João Pedro · 9 Zé do Arroz · 16 Danilo · 21 Maylson ·
--   23 Rawilson · 26 Katheley · 30 Paulo · 31 Juliana · 33 Pablo · 34 Manoel
--   (Prime) · 35 Louana · 37 Liberal · 39 Espaço Pelle Clínica Dermatológica ·
--   40 M.R. Rocha Assessoria de Imprensa
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('empresa-dre-seletor-mes', 'novidade', 'empresa', '{dono,gestor}',
 'Dá pra ver o DRE de meses anteriores',
 'O card do DRE ganhou um seletor com os últimos 6 meses — escolha o mês e o card, a planilha e o PDF acompanham.',
 '/painel/empresa',
 $txt$Antes, o card "DRE do mês" só mostrava o mês atual — não tinha como ver nem baixar (planilha ou PDF) um mês anterior.

AGORA tem um seletor no topo do card, com os últimos 6 meses. Escolher um mês recarrega o card com o DRE daquele mês — e "baixar planilha" e "baixar PDF" já saem com o mês escolhido.

O que não mudou: o resto da aba (títulos em aberto, folha, planejamento da semana) continua mostrando o mês atual, sempre.$txt$,
 timestamptz '2026-09-29 23:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'empresa-dre-seletor-mes';
