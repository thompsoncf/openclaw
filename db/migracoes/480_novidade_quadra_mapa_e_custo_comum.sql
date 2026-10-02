-- 480_novidade_quadra_mapa_e_custo_comum.sql
-- O aviso do PR 2 das obras por quadra (o mapa e o custo comum), seguindo a seção 5
-- do CLAUDE.md. Sem tabela nova: o centro de custo da quadra já nasceu na 478
-- (`obra_grupos.centro_custo_id`). Precisa da 478 e da 350 (o portão `construcao`).
-- Desenho aprovado pelo dono em 01/10/2026: docs/mockups/obras_por_quadra.html,
-- seções 4 e 6 (decisões 3 e 5).
--
-- PRA QUEM: dono e gestor.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-quadra-mapa-e-custo-comum', 'novidade', 'construcao', '{dono,gestor}',
 'O mapa da quadra e o custo comum dividido pelo m²',
 'A quadra ganhou o mapa dos lotes, colorido pelo andamento. E o que é de todas as casas — terraplanagem, rede, muro — entra no custo de cada uma pelo m².',
 '/painel/obras',
 $txt$A casa parecia mais barata do que é: a terraplanagem, a rede de esgoto e o muro da quadra não entravam no custo de casa nenhuma. Agora entram.

O CUSTO COMUM

Cada quadra tem o seu custo comum. Mande pelo WhatsApp ("paguei 8 mil da terraplanagem da quadra 4") ou escolha "Quadra 4 (comum)" na lista "Sem obra". O lançamento fica inteiro na quadra, e cada casa recebe a parte dela pela área: casa de 90 m² leva o dobro da de 45 m². A ficha da casa mostra o custo cheio — o que foi lançado nela mais a parte do comum —, e a margem e a comparação com o SINAPI passam a usar esse número.

O MAPA

Na página da quadra, os lotes aparecem em grade, cada um com a cor do andamento — de "não começou" a "pronta" — e em laranja quando tem alerta (CNO vencendo, casa pronta esperando papel). Um toque abre a casa.

O LEMBRETE DE SEGUNDA

O resumo de segunda agora junta as casas por quadra: "Quadra 4 — Lote 1: o que trava é habite-se; Lote 2: CNO atrasado".$txt$,
 timestamptz '2026-10-02 12:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-quadra-mapa-e-custo-comum';
