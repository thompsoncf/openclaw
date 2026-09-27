-- 397_novidade_resgate_ia.sql
-- O aviso do RESGATE DA IA (migração 396, finance/resgate.py), seguindo a seção 5 do
-- CLAUDE.md.
--
-- PÚBLICO `resgate_ligado` (novo portão de CONTA): a empresa com o resgate LIGADO. É
-- só aí que a rotina do vendedor muda — no Ensaio nenhum lead muda de dono, e avisar
-- de uma regra que não está valendo seria ensinar errado.
-- PRA QUEM: dois avisos. O da gerência (o cartão, o Ensaio, o supervisor) e o do
-- vendedor (o lead parado vai pra IA no 8º dia, e como segurar). O do vendedor não
-- tem resumo: é rotina interna, não sai no site.
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura): nenhuma empresa ainda
-- — o resgate nasce desligado. A Prime (34) passa a receber quando ligar.
--
-- Aditiva e idempotente.

alter table public.novidades drop constraint if exists novidades_publico_check;
alter table public.novidades add constraint novidades_publico_check
  check (publico in ('todos','produto','servico','eventos','recorrente',
                     'canal_proprio','seguros','suplementos','empresa',
                     'clinica','construcao','mais_de_um_chip','visita_da_ia',
                     'resgate_ligado'));

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('resgate-da-ia', 'novidade', 'resgate_ligado', '{dono,gestor}',
 'Resgate da IA: o lead parado volta a ser chamado',
 'O lead que fica 7 dias sem mensagem da equipe passa pra IA no 8º dia, e ela volta a chamar o cliente — com teto por dia, horário comercial e um supervisor acompanhando pelo WhatsApp.',
 '/painel/prospeccao/comunicacao?aba=agente',
 $txt$O lead que ninguém chama há 7 dias agora tem quem chame.

COMO FUNCIONA

- No 8º dia sem mensagem nossa, o lead passa pra IA, que lê a conversa e retoma de onde parou: primeiro quem fez uma pergunta e ficou sem resposta, depois quem tem data marcada chegando, depois os abertos e, por último, os perdidos.
- O vendedor recebe um aviso 2 dias antes. Pra ficar com o lead, basta mandar uma mensagem ou escrever o motivo em "Segurar este lead", que vai pro histórico da ficha.
- Visita ou reunião marcada pra frente, a IA não se mete. Lead que já teve visita ou reunião, ou que recebeu orçamento, tem prazo maior (14 dias, ajustável).
- No máximo 20 mensagens por dia, uma a cada 20 a 30 minutos, só no horário do cartão e sempre pelo número onde a conversa já está. Se 3 clientes pedirem pra parar ou 3 envios falharem no mesmo dia, o resgate pausa sozinho.

O SUPERVISOR

- Recebe no WhatsApp o resumo das 19h e os avisos de quando a IA precisa de gente.
- No Ensaio, recebe a prévia de cada mensagem antes de qualquer cliente receber, e pode usar o "Testar comigo" pra conversar com a IA como se fosse o cliente.

Fica em Comunicação › Agente › Regras por número, no cartão "Resgate da IA". Visão de dono e gestor.$txt$,
 timestamptz '2026-09-27 15:00:00+00'),
('resgate-segurar-lead', 'novidade', 'resgate_ligado', '{vendedor}',
 'Lead parado 7 dias vai pra IA — e como segurar o seu',
 null,
 '/cockpit',
 $txt$Lead que fica 7 dias sem mensagem sua passa pra IA no 8º dia.

- 2 dias antes, você recebe um aviso com a lista.
- Pra ficar com o lead: mande uma mensagem pro cliente, ou abra o lead no app e escreva o motivo em "Segurar este lead" (ele vai pro histórico da ficha). O prazo recomeça dali.
- Lead com visita ou reunião marcada pra frente nunca vai pra IA.$txt$,
 timestamptz '2026-09-27 15:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave in ('resgate-da-ia','resgate-segurar-lead');
--   alter table public.novidades drop constraint if exists novidades_publico_check;
--   alter table public.novidades add constraint novidades_publico_check
--     check (publico in ('todos','produto','servico','eventos','recorrente',
--                        'canal_proprio','seguros','suplementos','empresa',
--                        'clinica','construcao','mais_de_um_chip','visita_da_ia'));
