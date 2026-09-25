-- 341_novidade_funil_por_nicho.sql
-- O aviso do funil enxuto, parte 3 (os ajustes por nicho), seguindo a seção 5 do
-- CLAUDE.md. Mockup aprovado em 24/09/2026 (docs/mockups/prospeccao_layout.html,
-- "Na ZAQ, que não vende festa"); partes 1 e 2 na 339 e na 340.
--
-- Medido na produção em 25/09/2026, só leitura:
--   * Prime (34): 472 das 485 conversas de lead no chip principal — o selo "📱 CP
--     Zarb" estava em quase todo card;
--   * ZAQ (3): 33 leads com conversa de WhatsApp e o campo "whatsapp" vazio — sem
--     o 💬 no card.
--
-- DOIS AVISOS, dois alcances (§6):
--
--   * `funil-canal-pela-conversa` — público `servico` (todo mundo que tem funil):
--     o 💬/✉️ acende pela conversa, e o selo do número só aparece quando a
--     conversa é no OUTRO chip. Nenhuma palavra de festa.
--     QUEM RECEBE (produção, 25/09, contas × nichos pelo `vende_servico`):
--       3 Thompson Cavalcante Fernandes · 16 Danilo · 21 Maylson.ofc ·
--       23 Rawilson Osternes · 30 Paulo Costa · 33 Pablo Thyago G. Dias ·
--       34 MANOEL SOARES (Prime) · 35 Louana V. C. S. Costa · 37 Liberal Neto ·
--       39 Espaço Pelle Clínica Dermatológica
--
--   * `funil-grupos-por-entrada` — público `recorrente` (serviço que não vende
--     data): os grupos da coluna deixam de dizer "Sem data".
--     QUEM RECEBE: 3 · 16 · 21 · 23 · 30 · 33 · 37 · 39 (as de cima, menos 34 e 35)
--
-- PRA QUEM: dono, gestor e vendedor — é o card que todo mundo usa.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('funil-canal-pela-conversa', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'Funil: o 💬 do cartão acende quando existe conversa',
 'No funil, o botão de conversa do cartão passa a aparecer sempre que existe uma conversa com o lead, e o selo do número só quando a conversa é num número que não é o principal.',
 '/painel/prospeccao',
 $txt$Dois ajustes no cartão do funil.

O 💬 ACENDE PELA CONVERSA

Antes, o botão de conversa só aparecia quando o campo WhatsApp do cadastro estava preenchido. Lead que chegou por conversa, mas sem o número no cadastro, ficava sem o botão, e a conversa só se achava pela Comunicação. Agora, se existe conversa (WhatsApp, e-mail ou Instagram), o botão está no cartão.

O SELO DO NÚMERO SÓ QUANDO É O OUTRO NÚMERO

Em quem tem dois números de WhatsApp, o selo 📱 aparecia em quase todo cartão, com o nome do número principal. Agora ele só aparece quando a conversa está no outro número, que é justamente o caso que pede atenção.$txt$,
 timestamptz '2026-09-25 02:30:00+00'),
('funil-grupos-por-entrada', 'novidade', 'recorrente', '{dono,gestor,vendedor}',
 'Funil: os grupos da coluna dizem quando o lead entrou',
 'No funil, os leads de cada coluna aparecem agrupados pela semana ou pelo mês em que entraram, sem a marcação "Sem data".',
 '/painel/prospeccao',
 $txt$Dentro de cada coluna do funil, os leads continuam separados pela semana ou pelo mês em que entraram, na mesma ordem. O que mudou é o nome do grupo: era "Sem data · semana de 14/09" e agora é "Entrou na semana de 14/09".

O "Sem data" cobrava uma data que não faz parte do seu tipo de venda.$txt$,
 timestamptz '2026-09-25 02:30:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave in ('funil-canal-pela-conversa', 'funil-grupos-por-entrada');
