-- 402_novidade_cada_trilha_sua_regra.sql
-- Os avisos da migração 401 (finance/esteira.py, finance/follow_up.py,
-- finance/ia_insiste.py), seguindo a seção 5 do CLAUDE.md. São dois, porque mudam a
-- rotina de gente diferente:
--
-- 1. 'esteira-historico-e-resgate' — PÚBLICO `esteira_ligada` (portão novo de CONTA:
--    a esteira da cobrança ligada, o mesmo `esteira.config` do motor). PRA QUEM: o
--    vendedor (a cobrança dele muda: o histórico conta, e o lead que foi pro resgate
--    sai da esteira sem virar crédito) e dono/gestor (o fecho do dia muda junto).
-- 2. 'ia-insiste-quando-some' — PÚBLICO `mais_de_um_chip` (a chave mora no cartão
--    Regras por número, que só existe com dois chips). PRA QUEM: dono e gestor.
--
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura): o 1º, a Prime (34),
-- a única com a esteira ligada; o 2º, a Prime (34) e a conta 37, as duas com dois chips.
--
-- Aditiva e idempotente.

alter table public.novidades drop constraint if exists novidades_publico_check;
alter table public.novidades add constraint novidades_publico_check
  check (publico in ('todos','produto','servico','eventos','recorrente',
                     'canal_proprio','seguros','suplementos','empresa',
                     'clinica','construcao','mais_de_um_chip','visita_da_ia',
                     'resgate_ligado','resgate_eventos','esteira_ligada'));

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('esteira-historico-e-resgate', 'novidade', 'esteira_ligada', '{dono,gestor,vendedor}',
 'Cobrança: o histórico conta, e o lead da IA sai da esteira',
 'Na esteira da cobrança, o que o vendedor escreve no histórico do lead passa a contar como tratado, e o lead que a IA atende não é mais cobrado de ninguém.',
 '/painel/follow-up',
 $txt$Três mudanças na esteira da cobrança:

- O que você escreve no histórico do lead conta como tratado. Resolveu por telefone ou pessoalmente? Escreva no histórico: o lead sai da cobrança, entra no seu placar e não volta pra esteira no dia seguinte. É a mesma anotação que segura o lead contra o resgate da IA.
- Se um lead seu foi pro resgate da IA, ele sai da sua esteira. No fecho do dia ele aparece como "foi pro resgate", separado: não conta como tratado por você, nem como falta sua.
- O lead que a IA atende (o do número dela, e o do resgate) não entra mais na esteira nem no follow-up. Quem acompanha esse lead é a própria IA.$txt$,
 timestamptz '2026-09-27 18:00:00+00'),
('ia-insiste-quando-some', 'novidade', 'mais_de_um_chip', '{dono,gestor}',
 'A IA do número insiste quando o cliente some',
 'Nova chave na regra por número: quando o cliente para de responder, a IA manda um lembrete no 3º dia, a última chamada no 7º e marca como perdido no 10º.',
 '/painel/prospeccao/comunicacao',
 $txt$A IA do número agora pode fazer o follow-up dela mesma.

- Em Comunicação › Agente › Regras por número, ligue "A IA insiste quando o cliente some" no chip da IA.
- Se o cliente para de responder, a IA manda um lembrete curto no 3º dia (com uma coisa nova, nunca a mesma mensagem), a última chamada no 7º e, sem resposta, marca como perdido no 10º, com o motivo "não respondeu". Se ele escrever depois, o card volta e a IA responde.
- Ela não insiste com quem pediu pra parar, com quem tem visita ou reunião marcada, nem na conversa que alguém da equipe assumiu.
- Das 9h às 19h, de segunda a sábado, no máximo 20 por dia.
- Os leads da IA saíram da esteira de cobrança e do follow-up dos vendedores: ninguém mais é cobrado pelo lead que a IA atende.$txt$,
 timestamptz '2026-09-27 18:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave in ('esteira-historico-e-resgate','ia-insiste-quando-some');
--   alter table public.novidades drop constraint if exists novidades_publico_check;
--   alter table public.novidades add constraint novidades_publico_check
--     check (publico in ('todos','produto','servico','eventos','recorrente',
--                        'canal_proprio','seguros','suplementos','empresa',
--                        'clinica','construcao','mais_de_um_chip','visita_da_ia',
--                        'resgate_ligado','resgate_eventos'));
