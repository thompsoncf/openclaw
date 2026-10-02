-- 551_novidade_app_do_mestre.sql
-- O aviso do app do mestre de obras (web/app_obra.py), seguindo a seção 5 do
-- CLAUDE.md. Desenho aprovado pelo dono em 02/10/2026 (PR 3 do mapa). Precisa
-- da 350 (o portão `construcao`).
--
-- PRA QUEM: dono e gestor — quem convida o mestre e recebe o que vem do campo.
-- (O mestre não recebe Novidades: o aviso muda o jeito de trabalhar do negócio.)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-app-do-mestre', 'novidade', 'construcao', '{dono,gestor}',
 'O mestre de obras ganhou o app dele',
 'O mestre entra pelo celular e vê só as obras dele: tira a foto da etapa, marca a etapa feita, aponta o material que usou e vê o quadro da quadra. Sem nenhum valor em dinheiro na tela — e tudo que ele faz aparece pra você em "Do campo".',
 '/painel/equipe',
 $txt$O encarregado da obra agora tem o app dele, feito pro celular e pro canteiro.

COMO LIGAR

Em Equipe, convide o mestre com o papel "Mestre de obras" (ou gere uma senha temporária pra ele). Depois, na ficha de cada casa, em "Dados da obra", escolha quem é o mestre dela. Ele entra pelo mesmo endereço do Zaq e cai direto no app.

O QUE ELE FAZ

Quatro coisas, com o dedo: tirar a foto da etapa, marcar a etapa que ficou pronta, apontar o material ("usei 15 sacos", "chegou 60 sacos") e ver o quadro da quadra — que casa está em qual etapa. Ele só vê as obras dele, e não vê nenhum valor: dinheiro é do painel.

O QUE CHEGA PRA VOCÊ

Tudo que o mestre faz aparece em Obras, na lista "Do campo": quem, em qual casa, o quê e quando. A etapa marcada já conta no andamento e no mapa. Errou? Ele mesmo desfaz pelo app, e você também desfaz pela lista.$txt$,
 timestamptz '2026-10-03 12:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-app-do-mestre';
