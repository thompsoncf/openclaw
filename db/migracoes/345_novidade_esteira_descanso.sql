-- 345_novidade_esteira_descanso.sql
-- O aviso do descanso da esteira, seguindo a seção 5 do CLAUDE.md. Nasceu da
-- queixa da JACQUELINE (Prime) em 25/09/2026, e o dono decidiu no mesmo dia:
-- "falar recomeça o relógio" e a fila suja é limpa agora.
--
-- PÚBLICO: `servico` — a esteira cobra quem está parado no FUNIL, e quem só vende
-- produto não tem funil nem vendedor. Nunca `todos` (regra 6.3).
--
-- TIPO `mudanca`, e não `novidade`: o hábito muda dos dois lados. A fila do dia
-- encolhe (quem não ler vai achar que sumiu lead) e a orientação se inverte —
-- adiantar follow-up passou a contar a favor, quando antes só rendia cobrança
-- repetida. É o caso que a 174 descreve como "o jeito de trabalhar mudou", e por
-- isso exige o "Entendi" explícito de cada um.
--
-- PRA QUEM: dono, gestor e vendedor. É o vendedor que sente na fila dele — e é ele
-- que estava sendo cobrado duas vezes pelo mesmo lead; dono e gestor leem o fecho
-- do dia, onde o número de tratados também estava errado.
--
-- QUEM RECEBE, conferido na produção em 25/09/2026 (só leitura, contas × nichos
-- pelo `vende_servico`):
--   3 Thompson Cavalcante Fernandes · 16 Danilo · 21 Maylson.ofc ·
--   23 Rawilson Osternes · 30 Paulo Costa · 33 Pablo Thyago G. Dias ·
--   34 MANOEL SOARES (Prime) · 35 Louana V. C. S. Costa · 37 Liberal Neto ·
--   39 Espaço Pelle Clínica Dermatológica
-- (a 9, hortifruti, fica de fora: não vende serviço.)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('esteira-descanso', 'mudanca', 'servico', '{dono,gestor,vendedor}',
 'Follow-up: falar com o lead recomeça o prazo',
 'A lista de cobrança do dia parou de repetir leads com quem o vendedor acabou de falar: mandar mensagem agora recomeça o prazo da etapa.',
 '/painel/follow-up',
 $txt$Quem adiantava follow-up estava sendo cobrado de novo pelos mesmos leads. Isso acabou.

O QUE ESTAVA ERRADO

A lista do dia escolhia os leads pelo tempo parado na etapa. Mandar mensagem não move a etapa — então, se você falava com o cliente e ele não respondia, o lead continuava "parado" e voltava na lista no dia seguinte. Pior: como o sistema só contava o que acontecia DEPOIS de o lead entrar na lista, o trabalho feito antes não aparecia, e o resumo da manhã dizia "você tratou 0" num dia em que você tratou dezenas.

Na Prime, quem fez um mutirão de follow-up numa quarta recebeu, na quinta e na sexta, os mesmos 10 leads que já tinha tratado — e zero no placar nos dois dias.

O QUE MUDOU

Falar com o lead recomeça o relógio. Se a etapa dá 7 dias, mandar mensagem hoje tira aquele lead da lista por 7 dias. Ele só volta se continuar sem resposta depois disso.

Adiantar trabalho agora conta a seu favor: quanto mais você fala hoje, menos a lista te cobra amanhã.

A SUA FILA DE HOJE JÁ FOI LIMPA

Os leads que estavam na lista e que você já tinha tratado saíram dela, com a data em que você de fato falou com eles — não a data de hoje, pra não inventar um dia de trabalho que não existiu. Quem continuar na sua lista é quem está mesmo sem contato.

O QUE NÃO MUDOU

Lead que nunca recebeu mensagem continua sendo cobrado, e o prazo final de 7 dias na esteira segue igual. Quem resolveu por telefone ou pessoalmente ainda precisa escrever no histórico do lead — o que não está escrito continua não contando.$txt$,
 timestamptz '2026-09-25 19:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'esteira-descanso';
