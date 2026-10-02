-- 520_novidade_aba_mapa_das_obras.sql
-- O aviso da aba "Mapa das obras" no menu (web/painel_obras_mapa.py), seguindo a
-- seção 5 do CLAUDE.md. Pedido do dono em 02/10/2026 ("cria uma aba nova com tudo
-- pra acompanhar aquele mockup"). Precisa da 350 (o portão `construcao`).
--
-- NÚMERO 520, e não 498: outras sessões estavam até a 497 em PRs abertos — a
-- regra nova é pular pra longe e não colidir (482–485 colidiram em 02/10).
--
-- PRA QUEM: dono e gestor — quem acompanha as obras pelo painel.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-aba-mapa', 'novidade', 'construcao', '{dono,gestor}',
 'O mapa das obras ganhou aba própria',
 'Tudo do loteamento numa tela: o resumo da área, o mapa 3D com os alertas em selo, a lista do que precisa de atenção e o perfil de cada casa com o material e as fotos. E um exemplo pronto pra ver antes de riscar a sua planta.',
 '/painel/obras/mapa',
 $txt$O mapa das obras saiu de dentro da tela de Obras e virou aba no menu: "Mapa das obras", logo abaixo de Obras.

TUDO NUMA TELA

No topo, o resumo da área: casas no mapa, andamento médio, prontas, gasto e quantas estão com alerta. No meio, o mapa 3D — cada lote com a cor do andamento e um selo quando pede atenção (⚠️ da obra, 🧱 do material). Ao lado, a lista "Precisa de atenção": um toque leva direto ao lote.

O PERFIL DA CASA

Tocou no lote, abre a casa desenhada por camadas, as etapas, o gasto contra o previsto, a tabela do material (entrou, usado, na obra) e as últimas fotos.

QUER VER ANTES?

Ainda não riscou a sua planta? Toque em "Ver um exemplo pronto": abre um loteamento inventado, completo, pra você ver como fica. Nada dele é gravado.$txt$,
 timestamptz '2026-10-02 22:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-aba-mapa';
