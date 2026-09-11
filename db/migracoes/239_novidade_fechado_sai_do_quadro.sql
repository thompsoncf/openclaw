-- 239_novidade_fechado_sai_do_quadro.sql
-- O aviso da etapa que sai do quadro e da ponte com a Agenda (CLAUDE.md §5).
--
-- PÚBLICO 'todos' (§6): tirar uma etapa do quadro é mecânica de funil, de qualquer
-- nicho. A ponte com a Agenda só faz efeito em quem tem data de evento no cadastro,
-- e o texto diz isso como condição — não como se toda conta fosse de festa.
--
-- PRA QUEM: dono e gestor. O vendedor não configura nada disso, e o quadro dele
-- muda só depois que o dono marcar a caixa — avisá-lo antes seria avisar de uma
-- mudança que talvez nunca aconteça na conta dele.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('fechado-sai-do-quadro', 'novidade', 'todos', '{dono,gestor}',
 'Etapa que não é mais prospecção pode sair do quadro — e virar compromisso na Agenda',
 'Cada etapa pode deixar de aparecer no Kanban e, quando o lead tem data de evento, criar o compromisso na Agenda ao receber o lead. O cadastro continua inteiro.',
 '/painel/prospeccao/regua',
 $txt$O quadro do funil é para o que ainda precisa de venda. O que já foi fechado só ocupa espaço ali.

Na Régua, cada etapa ganhou duas caixas novas.

A primeira tira a etapa do quadro: ela deixa de ser coluna e os leads dela somem da prospecção. Nada é apagado — o cadastro, o contrato, as conversas e o histórico continuam inteiros, e o lead segue aparecendo na busca, nos relatórios e na ficha. O que muda é só a tela do funil, que passa a mostrar apenas quem ainda exige atuação da equipe.

A segunda cria o compromisso na Agenda quando o lead entra na etapa, usando a data do evento que está no cadastro. Se você já tinha digitado esse compromisso na agenda à mão, ele não é duplicado: o sistema liga a ficha ao que já existe, no mesmo dia, e não mexe no que você escreveu. A hora entra como 19h e fica marcada como sugerida, para você ajustar.

Ligue a segunda antes da primeira. Tirar a etapa do quadro sem a Agenda receber o evento troca uma coluna cheia por uma agenda vazia — e é exatamente assim que um evento fechado desaparece de vista.

E o relatório de Agenda → Eventos ganhou três números: receita contratada, receita recebida e a receber. Eles saem dos orçamentos dos leads cujo evento cai no período. Se aparecerem zerados, é porque os orçamentos ainda não têm valor preenchido ou não foram aprovados — o número está certo, o que falta é o preenchimento.

As duas caixas nascem desmarcadas em todas as etapas: nada muda até você marcar.$txt$,
 timestamptz '2026-09-11 23:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'fechado-sai-do-quadro';
