-- 222_novidade_convidado.sql
-- O aviso do papel de convidado (CLAUDE.md §5: PR que muda tela leva o aviso).
--
-- PÚBLICO 'canal_proprio', mesma razão do aviso de Origens (221): quem usa isto é
-- quem recebe lead pelo WhatsApp da própria empresa, onde o código do anúncio
-- viaja no texto. Não é recorte de nicho.
--
-- PRA QUEM: só o DONO. Quem convida a agência é o titular — é ele que manda o
-- link e ele que revoga. O gestor não convida ninguém (a tela de Equipe é da
-- capacidade `gerir`), e avisar quem não pode agir só gera pergunta.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('convidado-agencia', 'novidade', 'canal_proprio', '{dono}',
 'Sua agência de tráfego pode entrar e ver os resultados, sem ver o resto',
 'Um papel novo, Convidado, dá acesso só à tela de Origens. A agência entra com senha própria, acompanha o que os anúncios dela viraram, e não alcança conversa, funil, agenda nem financeiro.',
 '/painel/equipe',
 $txt$Antes, mostrar resultado pra quem cuida do tráfego era print no WhatsApp uma vez por semana. Agora dá pra convidar.

Em Equipe, ao convidar alguém, apareceu o papel **Convidado (agência)**. Quem entra por ele vê uma tela só — Origens — e mais nada: nem conversa de cliente, nem funil, nem agenda, nem contas, nem o app do vendedor. O convite é o mesmo link de sempre; a pessoa cria a própria senha, e você revoga quando quiser, igual a qualquer membro.

O que ela enxerga: quantas conversas cada criativo trouxe, quantas foram atendidas e em quanto tempo, quantas marcaram visita, quantas compareceram, quantas fecharam e por quanto.

E o que ela NÃO enxerga: o dinheiro que entrou por fora do anúncio. A linha "sem código" aparece pra ela em quantidade — ela precisa disso pra perceber quando a origem está se perdendo —, mas sem vendas e sem faturamento. O que a casa vendeu por indicação, pelo Google ou pelo balcão continua sendo assunto seu.

Nenhum texto de conversa aparece em nenhum momento, pra ninguém de fora.$txt$,
 timestamptz '2026-09-07 17:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'convidado-agencia';
