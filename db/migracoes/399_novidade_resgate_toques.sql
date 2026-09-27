-- 399_novidade_resgate_toques.sql
-- O aviso da ETAPA 2 do resgate da IA (migração 398, finance/resgate.py e a aba
-- Resgate em finance/desafio_ia.py), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `resgate_eventos` (novo portão de CONTA): resgate LIGADO e vende festa. A
-- aba nova mora na tela do Desafio, que só existe pra quem vende festa (a rota
-- redireciona os outros) — o aviso mira o mesmo portão da tela.
-- PRA QUEM: dono e gestor (o Desafio é visão de gerência). Os toques não mudam a
-- rotina do vendedor: quem manda é a IA, no lead que já é dela.
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura): nenhuma empresa
-- ainda — o resgate nasce desligado. A Prime (34) passa a receber quando ligar.
--
-- Aditiva e idempotente.

alter table public.novidades drop constraint if exists novidades_publico_check;
alter table public.novidades add constraint novidades_publico_check
  check (publico in ('todos','produto','servico','eventos','recorrente',
                     'canal_proprio','seguros','suplementos','empresa',
                     'clinica','construcao','mais_de_um_chip','visita_da_ia',
                     'resgate_ligado','resgate_eventos'));

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('resgate-toques-e-desafio', 'novidade', 'resgate_eventos', '{dono,gestor}',
 'Resgate: 3 toques, o perdido no 10º dia e a aba no Desafio',
 'A IA do resgate agora faz os 3 toques de sempre — a retomada, um lembrete no dia 3 e a última chamada no dia 7 — e o lead que não responde vira perdido no dia 10, com o resultado medido no Desafio.',
 '/painel/prospeccao/desafio-ia',
 $txt$O resgate agora vai até o fim, com a regra de sempre: 3 toques e o 4º fica como perdido.

OS TOQUES

- Dia 0: a retomada (a IA lê a conversa e volta de onde parou).
- Dia 3: um lembrete curto, com uma coisa nova — nunca a mesma mensagem.
- Dia 7: a última chamada, perguntando se ainda faz sentido.
- Dia 10, sem resposta: o lead vira perdido, motivo "não respondeu". Nada é apagado: se o cliente escrever depois, o card volta e a IA responde.
- Qualquer resposta do cliente para os toques. Pedido pra parar, também.
- Os toques contam no mesmo teto de mensagens por dia e no mesmo horário do resgate.

A ABA RESGATE NO DESAFIO

- Por faixa da fila: quantos foram chamados, quantos responderam (e em até 7 dias), visitas, orçamentos, contratos, quantos pediram pra parar, quantos viraram perdido e o custo da IA.
- A régua: o que acontecia quando alguém da equipe voltava a chamar um lead parado 7 dias. Na Prime, em setembro: 31% respondiam em até 7 dias.

Fica em Comunicação › Agente › Regras por número › Desafio IA × equipe. Visão de dono e gestor.$txt$,
 timestamptz '2026-09-27 16:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'resgate-toques-e-desafio';
--   alter table public.novidades drop constraint if exists novidades_publico_check;
--   alter table public.novidades add constraint novidades_publico_check
--     check (publico in ('todos','produto','servico','eventos','recorrente',
--                        'canal_proprio','seguros','suplementos','empresa',
--                        'clinica','construcao','mais_de_um_chip','visita_da_ia',
--                        'resgate_ligado'));
