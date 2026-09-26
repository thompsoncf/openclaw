-- 366_novidade_obras_segunda_e_margem.sql
-- O aviso do lembrete de segunda das obras e da margem da casa, seguindo a seção 5
-- do CLAUDE.md: PR que muda tela leva o aviso, no mesmo PR. Precisa da 365 (a
-- trava do lembrete) e da 350 (o portão `construcao`). Desenho aprovado pelo dono
-- em 25/09/2026: docs/mockups/nicho_construcao.html, seção 06.
--
-- PORTÃO `construcao`: a margem, a faixa do Financeiro e o lembrete só existem no
-- perfil `obras`.
--
-- QUEM RECEBE, conferido na produção em 26/09/2026 (contas × nichos, só leitura):
--   conta 33 · Pablo Thyago G. Dias / PX2 Empreendimentos · Lago da Pedra-MA
--
-- PRA QUEM: dono e gestor.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-segunda-e-margem', 'novidade', 'construcao', '{dono,gestor}',
 'Toda segunda, o que está parado nas obras',
 'Segunda de manhã chega a lista do que trava cada casa e das parcelas de reforma que já dá pra cobrar. A ficha da obra mostra a margem, e o Financeiro mostra o dinheiro parado em casa.',
 '/painel/obras',
 $txt$Casa pronta esperando papel é dinheiro parado — e ninguém abre o sistema pra ouvir isso. Agora o Zaq avisa.

TODA SEGUNDA DE MANHÃ

Chega uma mensagem com o que trava cada casa (habite-se, CND, averbação, a Caixa), os prazos que estão vencendo (CNO, certidões, avaliação, registro) e as parcelas de reforma cuja etapa já ficou pronta e ainda não foram pagas. Semana sem pendência, não chega nada.

Sai pelo WhatsApp quando dá, e senão por e-mail. Quando um papel sair, é só responder contando ("saiu o habite-se da casa 2") que a casa anda.

A MARGEM DA OBRA

Na ficha da obra, ao lado da venda: quanto sobra do preço depois do custo. Enquanto a obra não termina, a conta usa o custo previsto se ele for maior que o gasto até agora — pra margem não parecer maior do que vai ser. Na casa com venda cadastrada, vale o preço da venda.

NO FINANCEIRO

No topo, uma faixa com o total gasto em casa que a Caixa ainda não pagou, as casas travadas e os gastos de obra que ainda estão sem obra. Um toque leva pra Obras.$txt$,
 timestamptz '2026-09-26 12:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-segunda-e-margem';
