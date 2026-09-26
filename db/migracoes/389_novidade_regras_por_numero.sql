-- 389_novidade_regras_por_numero.sql
-- O aviso das Regras por número (migração 388, finance/chip_regra.py), seguindo a
-- seção 5 do CLAUDE.md.
--
-- PÚBLICO `mais_de_um_chip`, portão NOVO: nenhum dos outros descreve o alcance. A
-- tela só aparece pra empresa com dois números ou mais (com um só, quem recebe o
-- lead é o rodízio de sempre), e isso não é nicho — é a conta. O portão é o mesmo da
-- tela (`chip_regra.tem_mais_de_um_chip`).
-- PRA QUEM: dono e gestor (só a gerência configura; a rotina do vendedor não muda).
-- QUEM RECEBE, conferido na produção em 26/09/2026 (só leitura, contas com chip_de):
--   34 Prime Eventos (eventos)
--   37 Liberal Neto Corretagem de Seguros (seguros)
--
-- Aditiva e idempotente.

-- ────────────────────────────────────────────── 1. o portão novo no check
alter table public.novidades drop constraint if exists novidades_publico_check;
alter table public.novidades add constraint novidades_publico_check
  check (publico in ('todos','produto','servico','eventos','recorrente',
                     'canal_proprio','seguros','suplementos','empresa',
                     'clinica','construcao','mais_de_um_chip'));

-- ────────────────────────────────────────────── 2. o aviso
insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('regras-por-numero', 'novidade', 'mais_de_um_chip', '{dono,gestor}',
 'Regras por número: cada chip com o seu dono, e a IA atendendo só nele',
 'Quem tem dois números de WhatsApp escolhe, por número, quem recebe os contatos novos e se a IA atende, em qual horário e quem ela chama quando precisa de alguém da equipe.',
 '/painel/prospeccao/comunicacao?aba=agente',
 $txt$Cada número de WhatsApp da empresa agora pode ter a sua regra.

QUEM RECEBE O CONTATO NOVO

- Em Comunicação › Agente, no cartão Regras por número, escolha quem recebe os contatos novos de cada chip. Sem regra, o chip segue no rodízio de sempre.
- Vale só para contato novo, a partir do momento em que você liga a regra. Nenhum lead sai de quem já atende. Quem já é cliente de alguém pelo outro número continua com essa pessoa.

A IA ATENDENDO SÓ NAQUELE NÚMERO

- Ligue "A IA atende" na regra: ela responde os contatos novos daquele número, e a chave geral do agente não muda.
- Horário: 24 horas ou um horário próprio (dias e horas). Fora do horário, o cliente recebe um recado, e a IA responde quando abrir.
- Ela se apresenta do jeito que você escrever (ex.: "Sou a assistente Zaq, da sua empresa").
- Preço: a IA só cita o valor dos itens que você liberar no catálogo ("A IA pode dizer este preço", em Serviços › Catálogo), sempre como valor de referência. Orçamento formal ela ainda não manda.
- Quando precisa de alguém (agenda, desconto, fechamento, reclamação), ela avisa quem você escolheu na regra, por push, e-mail e WhatsApp, e continua a conversa. Se ela não conseguir responder, avisa também.
- Se alguém da equipe responder a conversa, pelo celular ou pelo painel, a IA sai daquela conversa na hora, para o cliente não ouvir duas vozes.$txt$,
 timestamptz '2026-09-27 00:10:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'regras-por-numero';
--   alter table public.novidades drop constraint if exists novidades_publico_check;
--   alter table public.novidades add constraint novidades_publico_check
--     check (publico in ('todos','produto','servico','eventos','recorrente',
--                        'canal_proprio','seguros','suplementos','empresa',
--                        'clinica','construcao'));
