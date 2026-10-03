-- 570_novidade_mestre_pelo_whatsapp.sql
-- O aviso do cadastro do mestre sem e-mail e do link mágico pelo WhatsApp
-- (finance/obra_acesso.py, web/painel_equipe.py), seguindo a seção 5 do
-- CLAUDE.md. Pedido do dono em 03/10/2026. Precisa da 350 (o portão `construcao`).
--
-- NÚMERO 570: o maior em uso em main + PRs abertos era 551 — pulo pra longe.
--
-- PRA QUEM: o dono — só ele mexe na Equipe.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-mestre-pelo-whatsapp', 'novidade', 'construcao', '{dono}',
 'O mestre de obras entra pelo WhatsApp, sem senha',
 'Cadastre o mestre só com o nome e o WhatsApp. Um toque manda o link de entrada pelo seu WhatsApp, ele toca e já está no app da obra — sem e-mail, sem senha. O celular dele fica lembrado.',
 '/painel/equipe',
 $txt$Mestre de obras não lê e-mail nem guarda senha. Agora não precisa.

CADASTRAR

Em Equipe, no quadro "Mestre de obras", ponha o nome e o WhatsApp dele e toque em "Cadastrar e gerar o link".

MANDAR O LINK

Aparece o botão verde "Mandar pelo WhatsApp": ele abre o SEU WhatsApp com a mensagem pronta pro mestre. É só enviar. Ele toca no link e já entra no app da obra.

DEPOIS

O celular dele fica lembrado — não pede mais nada. Trocou de celular ou o link venceu (vale 48 horas)? Na linha dele em Equipe, toque em "Link pelo WhatsApp" e mande um novo; o anterior para de valer. Desativou o mestre? Ele sai do app na hora.

E não esqueça: na ficha de cada casa, em "Dados da obra", escolha quem é o mestre dela — é isso que faz a casa aparecer no app dele.$txt$,
 timestamptz '2026-10-03 15:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-mestre-pelo-whatsapp';
