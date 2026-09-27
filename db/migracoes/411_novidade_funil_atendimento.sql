-- 411_novidade_funil_atendimento.sql
-- O aviso da vista Atendimento do funil (docs/mockups/funil_atendimento.html,
-- aprovado pelo dono em 27/09/2026 "com as recomendações"), seguindo a seção 5 do
-- CLAUDE.md.
--
-- PÚBLICO `funil_atendimento` (portão novo de NICHO: os perfis `eventos` e
-- `recorrente`, o mesmo `raio_x_perfil` que a rota lê — finance/atendimento.py). O
-- `servico` alcançaria clínica, corretora e obras, que não têm a vista.
-- PRA QUEM: dono e gestor (decisão 2 do mockup) — a vista, a pílula "Quem atende" e
-- o Desafio na barra de Relatórios são da gerência.
-- QUEM RECEBE: toda conta com nicho de eventos ou de serviço recorrente declarado.
--
-- Aditiva e idempotente.

alter table public.novidades drop constraint if exists novidades_publico_check;
alter table public.novidades add constraint novidades_publico_check
  check (publico in ('todos','produto','servico','eventos','recorrente',
                     'canal_proprio','seguros','suplementos','empresa',
                     'clinica','construcao','mais_de_um_chip','visita_da_ia',
                     'resgate_ligado','resgate_eventos','esteira_ligada',
                     'resgate_ativo','funil_atendimento'));

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('funil-atendimento', 'novidade', 'funil_atendimento', '{dono,gestor}',
 'O funil ganhou a vista Atendimento',
 'Uma segunda vista no funil mostra o começo de cada conversa, em etapas que o sistema move sozinho, e compara a IA com a equipe etapa por etapa.',
 '/painel/prospeccao/atendimento',
 $txt$O funil tem agora duas vistas, com um toque no cabeçalho: Vendas e Atendimento.

- VENDAS é o quadro de sempre, com as etapas que a equipe move.
- ATENDIMENTO mostra o começo da conversa: chegou, respondido, qualificado, ofertada, marcada, e quem parou. Ninguém arrasta card: a etapa vem da conversa, da ficha e da agenda. Cada card diz quem está atendendo, a IA ou alguém da equipe.
- No topo, a régua compara a IA com a equipe: das conversas que chegaram, quantas cada lado respondeu, qualificou, convidou e marcou, e em quanto tempo respondeu a primeira.
- Em Vendas, com um número atendido pela IA, a pílula "Quem atende" filtra o quadro: todos, IA ou equipe.
- A mensagem de saudação automática do WhatsApp Business não conta mais como resposta de gente: ela não tira a IA da conversa e não entra no tempo da 1ª resposta.$txt$,
 timestamptz '2026-09-28 11:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'funil-atendimento';
--   (e o check volta ao da 410)
