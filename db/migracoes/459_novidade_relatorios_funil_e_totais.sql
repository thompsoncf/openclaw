-- 459_novidade_relatorios_funil_e_totais.sql
-- Duas réguas dos Relatórios (decisão do dono em 01/10/2026), seguindo a seção 5
-- do CLAUDE.md.
--
-- O QUE MUDOU NA TELA:
--   * Funil em "Este mês"/"Este ano" olha o período inteiro, como a Agenda: a
--     visita marcada pro dia 20 conta como agendada desde que foi marcada (antes
--     só entrava quando o dia chegava). Comparecimento não muda — mede só as que
--     já passaram.
--   * O total e os números do topo contam TODOS os registros do período. A tabela
--     mostra até 300 e, quando corta, diz quantos existem. Antes o total era
--     somado só das linhas mostradas.
--
-- PÚBLICO `todos`: os Relatórios existem pra qualquer conta. PRA QUEM: dono e
-- gestor. Sem resumo: é ajuste de régua interna, não vai pro site.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, link, corpo, publicado_em) values
('relatorios-funil-mes-inteiro-e-totais', 'mudanca', 'todos', '{dono,gestor}',
 'Funil do mês inteiro e totais completos nos Relatórios',
 '/painel/relatorios?tipo=funil',
 $txt$Duas mudanças nos Relatórios:

- Funil: em "Este mês", o que está marcado pra mais adiante no mês já conta como agendado — antes só entrava quando o dia chegava. A taxa de comparecimento continua medindo só o que já aconteceu.
- Totais: o total e os números do topo de cada relatório somam todos os registros do período. Quando o período é grande, a tabela mostra os 300 primeiros e avisa quantos existem.$txt$,
 timestamptz '2026-10-01 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'relatorios-funil-mes-inteiro-e-totais';
