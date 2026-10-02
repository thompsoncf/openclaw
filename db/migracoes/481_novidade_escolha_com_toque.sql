-- 481_novidade_escolha_com_toque.sql
-- O aviso dos botões de escolha no WhatsApp e no Telegram (finance/escolhas.py),
-- seguindo a seção 5 do CLAUDE.md. Pedido do dono em 02/10/2026. Precisa da 350
-- (o portão `construcao`).
--
-- PRA QUEM: dono e gestor — quem conversa com o assistente sobre as obras.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-escolha-com-toque', 'novidade', 'construcao', '{dono,gestor}',
 'De qual obra? Agora é um toque',
 'Depois da foto da nota, o assistente manda um botão com as suas obras: um toque e a nota está na casa certa. O mesmo pra dizer que etapa ficou pronta.',
 '/painel/obras',
 $txt$Responder "de qual obra é?" digitando o nome toda vez cansa. Agora não precisa.

A OBRA DA NOTA

Mandou a foto da nota, o assistente lê, mostra o resumo e manda o botão "Ver obras". Toque na obra e pronto: a nota está nela. Na lista também aparecem "Dividir entre todas" e, se você usa quadras, "Quadra X (dividir)" — que divide pelo m².

A ETAPA QUE FICOU PRONTA

"Terminei mais uma etapa da casa 2": vem a lista das etapas que faltam naquela obra. Um toque marca.

NO TELEGRAM TAMBÉM

No Telegram as opções aparecem como botões no lugar do teclado. E escrever continua valendo: o botão é só um atalho.$txt$,
 timestamptz '2026-10-02 15:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-escolha-com-toque';
