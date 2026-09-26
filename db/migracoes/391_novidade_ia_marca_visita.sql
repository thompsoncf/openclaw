-- 391_novidade_ia_marca_visita.sql
-- Os avisos da etapa 2 do vendedor IA (migração 390, finance/ia_visita.py), seguindo a
-- seção 5 do CLAUDE.md. Dois avisos, porque são dois públicos:
--
-- 1. 'ia-do-numero-marca-visita' — PÚBLICO `visita_da_ia`, portão NOVO: dois chips E vende
--    festa. É quem vê "A IA marca a visita ao espaço" no cartão Regras por número; a
--    corretora de dois chips vê a regra e não vê a visita (CLAUDE.md §6).
--    PRA QUEM: dono e gestor (só a gerência configura).
--    QUEM RECEBE, conferido na produção em 26/09/2026 (só leitura):
--      34 Prime Eventos
--
-- 2. 'visita-meia-hora' — PÚBLICO `eventos`. O app do vendedor passa a marcar visita
--    em meia hora (9h30, 17h30); muda a rotina de quem marca visita.
--    PRA QUEM: dono, gestor e vendedor.
--    QUEM RECEBE (nicho eventos, só leitura em 26/09/2026):
--      34 Prime Eventos, 35 Doce Mell
--
-- Aditiva e idempotente.

-- ────────────────────────────────────────────── 1. o portão novo no check
alter table public.novidades drop constraint if exists novidades_publico_check;
alter table public.novidades add constraint novidades_publico_check
  check (publico in ('todos','produto','servico','eventos','recorrente',
                     'canal_proprio','seguros','suplementos','empresa',
                     'clinica','construcao','mais_de_um_chip','visita_da_ia'));

-- ────────────────────────────────────────────── 2. os avisos
insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('ia-do-numero-marca-visita', 'novidade', 'visita_da_ia', '{dono,gestor}',
 'A IA do número agora marca a visita ao espaço',
 'Com a regra por número, a IA marca a visita conferindo a agenda, as festas e as conversas, confirma na véspera e 2h antes, remarca e avisa quem recebe o cliente.',
 '/painel/prospeccao/comunicacao?aba=agente',
 $txt$A IA que atende um número (Regras por número) agora pode marcar a visita ao espaço.

COMO LIGAR

- Em Comunicação › Agente, no cartão do número, ligue "A IA marca a visita ao espaço" e escolha a anfitriã: quem recebe o cliente e é avisada de cada visita.
- A grade de visita já vem com os horários em que mais gente apareceu (manhã e fim de tarde; 15h e 16h só confirmando no dia; 19h só em dia sem festa; domingo só a pedido). Toque num horário para mudar.

O QUE A IA CONFERE ANTES DE MARCAR

- A agenda inteira: outra visita, festa, pré-reserva ou compromisso, com a folga depois da visita.
- A festa do dia: nenhuma visita nas horas antes de uma festa.
- As conversas: se alguém combinou uma visita naquele dia e hora e não pôs na agenda, a IA não marca em cima e chama a anfitriã.

DEPOIS DE MARCAR

- O cliente recebe a confirmação com o endereço e o convite pra agenda do celular, pelo mesmo número em que conversou.
- Véspera às 18h: "1 confirma, 2 remarca". Duas horas antes: o lembrete. Silêncio não cancela: a anfitriã é avisada de que ninguém confirmou.
- O cliente pode remarcar pela conversa (até 2 vezes); a IA move a mesma visita.
- Visita marcada como "não apareceu" vira uma mensagem de "sentimos sua falta, quer remarcar?".$txt$,
 timestamptz '2026-09-27 00:15:00+00'),
('visita-meia-hora', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'Visita em meia hora pelo app',
 'O app do vendedor passa a marcar visita em meia hora (9h30, 17h30), além das horas cheias.',
 '/cockpit',
 $txt$Na tela de agendar visita do app, escolha a hora e toque em "+ meia hora" pra marcar 9h30, 17h30 e assim por diante. Tocar de novo volta pra hora cheia.

E a confirmação que o cliente recebe agora sai pelo mesmo número em que ele conversou com a empresa.$txt$,
 timestamptz '2026-09-27 00:16:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave in ('ia-do-numero-marca-visita','visita-meia-hora');
--   alter table public.novidades drop constraint if exists novidades_publico_check;
--   alter table public.novidades add constraint novidades_publico_check
--     check (publico in ('todos','produto','servico','eventos','recorrente',
--                        'canal_proprio','seguros','suplementos','empresa',
--                        'clinica','construcao','mais_de_um_chip'));
