-- 231_novidade_teto_da_etapa.sql
-- O aviso do teto de dias na etapa (CLAUDE.md §5).
--
-- PÚBLICO 'todos' e não 'eventos' (§6): o teto é propriedade de qualquer etapa, de
-- qualquer funil — o que veio de eventos foi o PEDIDO, não a mecânica. O aviso não
-- fala de festa, de visita nem de data.
--
-- PRA QUEM: dono, gestor e vendedor. O vendedor entra porque muda a rotina dele —
-- é ele que recebe o aviso de vencimento e é ele que escreve a justificativa. A
-- configuração (quantos dias, quantas renovações) é do dono, na Régua.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('teto-de-dias-na-etapa', 'novidade', 'todos', '{dono,gestor,vendedor}',
 'Etapa com prazo: o lead não fica parado para sempre, e renovar exige justificativa',
 'Cada coluna do funil pode ter um teto de dias e um número de renovações. O vendedor é avisado antes de vencer, e para ganhar mais dias precisa escrever por quê.',
 '/painel/prospeccao/regua',
 $txt$Um lead pode ficar parado numa coluna até alguém lembrar dele. Agora cada etapa pode ter prazo.

Na Régua, cada coluna ganhou três campos: quantos dias um lead pode ficar ali, quantas renovações são permitidas, e se renovar exige justificativa. Sete dias com duas renovações, por exemplo, dão um teto de vinte e um. Deixar em branco é o que todas as contas têm hoje: sem teto, nada muda.

Na ficha do lead aparece a barra do prazo — quantos dias ele está ali, de quantos, em que período está e quantas renovações restam. Quando falta pouco, o vendedor é avisado; quando vence, também.

Para ganhar mais dias, o vendedor renova. Se a etapa exigir justificativa, ela é obrigatória: sem o texto, a renovação não é liberada. Cada renovação fica registrada com autor, data e motivo, e a ficha mostra o histórico — é assim que se enxerga a diferença entre quem renova porque está trabalhando o lead e quem renova só para o aviso parar.

Quando as renovações acabam, o sistema não move o lead sozinho. Ele fica marcado, esperando a decisão de quem falou com o cliente: levar adiante ou encerrar.

Os avisos saem agrupados e dentro do horário de atendimento — ninguém recebe um push por lead. E como tudo nesta régua, nasce desligado: o dono liga quando quiser, e pode rodar em modo observação primeiro, que calcula tudo sem avisar ninguém.$txt$,
 timestamptz '2026-09-11 20:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'teto-de-dias-na-etapa';
