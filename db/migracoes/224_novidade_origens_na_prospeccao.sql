-- 224_novidade_origens_na_prospeccao.sql
-- O aviso de que a tela de Origens mudou de lugar (CLAUDE.md §5).
--
-- PÚBLICO 'canal_proprio', o MESMO da 221, que anunciou a tela. O portão tem que
-- ser o mesmo dos dois avisos: quem foi apresentado à tela é quem precisa saber
-- que ela mudou de endereço. Mandar por nicho aqui alcançaria conta que nunca
-- ouviu falar dela.
--
-- PRA QUEM: dono e gestor — os mesmos da 221. O vendedor não tem Origens
-- (`caps.origens` é False pro papel dele) e não pode receber aviso de tela que
-- não tem, que é a regra da §5.
--
-- O convidado (a agência) NÃO é alcançado, e é de propósito: pra ele nada mudou
-- — ele entra direto na tela, que continua no mesmo endereço, sem barra de abas.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('origens-na-prospeccao', 'novidade', 'canal_proprio', '{dono,gestor}',
 'Origens agora fica dentro de Prospecção',
 'A tela de Origens saiu do menu lateral e virou uma aba de Prospecção, junto do Funil, do Follow-up e da Régua — tudo que é sobre lead no mesmo lugar.',
 '/painel/origens',
 $txt$Origens conta de onde veio cada conversa e o que ela virou. É assunto de lead — mesma matéria do Funil, do Follow-up e da Régua, que já viviam juntos em Prospecção.

Agora ela mora lá também, como aba. O endereço não mudou: quem tem o link salvo continua chegando igual, e o que a tela mostra é exatamente o mesmo.

Pra quem cuida do tráfego da sua empresa, nada muda: a agência continua entrando direto na tela dela, pelo convite que você mandou, e não passa por Prospecção em momento nenhum.$txt$,
 timestamptz '2026-09-07 18:05:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'origens-na-prospeccao';
