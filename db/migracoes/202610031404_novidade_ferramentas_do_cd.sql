-- 202610031404_novidade_ferramentas_do_cd.sql
-- O QUE FAZ: o aviso das ferramentas do CD (finance/obra_ferramentas.py), pela
--   seção 5 do CLAUDE.md. Portão `construcao` (migração 350); pra dono e gestor
--   (o pra_quem só aceita dono/gestor/vendedor — 199).
-- POR QUÊ: PR 2 do CD das obras, aprovado pelo dono em 03/10/2026.
--
-- Aditiva e idempotente (if not exists / on conflict do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-ferramentas-cd', 'novidade', 'construcao', '{dono,gestor}',
 'Betoneira, andaime e martelete: onde está cada um',
 'As ferramentas e equipamentos ganharam controle no CD: cada um com código, em qual obra está, com quem e desde quando. O Zaq avisa a que ficou esquecida em casa pronta ou está fora há mais de 7 dias, e o mestre devolve pelo app.',
 '/painel/obras/deposito?aba=ferramentas',
 $txt$Ferramenta não se gasta: sai e volta. E é justamente por isso que some — fica na obra que acabou, ou na mão de quem nem é da casa.

CADASTRAR

Em Depósito (CD) › Ferramentas, ponha o nome e quantas são ("Carrinho de mão", 4): cada uma ganha um código (FER-01, FER-02…) que dá pra escrever nela.

EMPRESTAR E DEVOLVER

"Mandar pra obra": escolha a casa e com quem vai. A lista mostra onde está cada ferramenta, com quem e há quantos dias. Voltou? "Devolver ao CD". Mudou de casa? Manda pra outra obra direto, sem passar pelo galpão.

O AVISO

A ferramenta que ficou numa casa pronta, ou que está fora há mais de 7 dias, aparece em destaque — e em "Precisa de você hoje". Quebrou ou sumiu? "Dar baixa", com o motivo: ela sai da lista e o histórico fica.

NO APP DO MESTRE

Na tela da obra, o mestre vê as ferramentas que estão com ela e devolve ao CD com um toque. E, quando a casa fica pronta, o "devolver ao CD" leva o material e as ferramentas juntos.$txt$,
 timestamptz '2026-10-03 22:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-ferramentas-cd';
