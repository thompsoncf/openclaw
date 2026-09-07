-- 225_novidade_conversao_com_cadastro_manual.sql
-- Relatórios › a aba passa a contar TODOS os leads, e a mostrar a conversão.
--
-- O QUE MUDOU NA TELA
--   * o rótulo da aba: "Leads do chip" -> "Leads e conversão" (a CHAVE segue
--     `leads_chip`, que está em link salvo e na URL do PDF);
--   * lead cadastrado na mão passa a aparecer, com "Cadastro manual" na coluna
--     Chip e o orçamento dele na coluna Orçamento do lead;
--   * a métrica "Viraram orçamento" passa de "18" pra "20 de 305 · 7%".
--
-- O QUE ESTAVA ERRADO, medido na produção em 07/09/2026
-- A consulta começava em `conversas`, então lead sem conversa nenhuma não existia
-- na tela — e levava o ORÇAMENTO dele junto. Na conta 34: 305 leads, 3 sem
-- conversa; 20 leads com orçamento, dos quais 2 INVISÍVEIS (nº 18 Claudia e nº 19
-- Kleiton, os dois `origem = manual_vendedor`, cadastrados pelo vendedor). A
-- conversão saía sobre 302 leads e 18 orçamentos.
--
-- O CUIDADO QUE O PORTÃO GUARDA (§6: medir em mais de uma conta antes de aprovar).
-- Abrir a tela pra "todo lead sem conversa" quebraria as outras: a lista fria
-- raspada do Google Maps entraria como se fosse lead que chegou.
--     conta 34 (Prime)    305 leads,   3 sem conversa -> os 3 são cadastro de gente
--     conta  3 (ZAQ)      263 leads, 146 sem conversa -> 145 são garimpo
--     conta 21 (Maylson)   60 leads,  60 sem conversa -> 60 de 60 são garimpo
-- Então entra quem tem conversa (como sempre foi) MAIS quem uma pessoa cadastrou;
-- fica de fora só o garimpo que ninguém tocou. Efeito conferido, antes -> depois:
--     conta 34: 302/18 -> 305/20     conta  7: 10/0 -> 10/0
--     conta  3: 117/5  -> 118/5      conta 21:  0/0 ->  0/0
--
-- O PORTÃO: `todos`. A aba é de quem trabalha lead — 5 contas em TRÊS nichos (34 e
-- 35 eventos, 23 e 3 consultoria, 7 sem nicho). Nenhum portão de nicho descreve
-- esse alcance, e `canal_proprio` é estritamente QR enquanto a aba vale igual pra
-- Twilio e Cloud API.
--
-- PRA QUEM: dono e gestor. O vendedor NÃO entra: `/painel/relatorios` pede a
-- capacidade `financeiro` (contas/equipe.py:rotas_do_papel), que ele não tem.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('leads-conversao-conta-cadastro-manual', 'novidade', 'todos', '{dono,gestor}',
 'A aba de leads agora conta todo mundo, e mostra a conversão',
 'O lead que o vendedor cadastra na mão passou a aparecer no relatório de leads — antes ele ficava de fora e levava o orçamento dele junto, então a conversão saía menor do que era.',
 '/painel/relatorios?tipo=leads_chip',
 $txt$A aba "Leads do chip" virou "Leads e conversão", e o motivo é que ela deixou de ser só do chip.

O QUE ESTAVA FALTANDO. O relatório montava a lista a partir das CONVERSAS. Quem chegou por indicação, por visita ou por telefone — e que o vendedor cadastrou na mão — não tinha conversa e simplesmente não aparecia. Junto com ele sumia o orçamento dele.

Na prática isso fazia a conta fechar errada: uma empresa com 305 leads e 20 propostas via 302 e 18. Dois clientes com proposta feita não constavam em lugar nenhum desta tela.

O QUE MUDA. Agora a lista parte dos LEADS. Quem foi cadastrado na mão aparece com "Cadastro manual" na coluna Chip, e a proposta dele aparece na coluna "Orçamento do lead" como a de qualquer outro.

A CONVERSÃO FICOU EXPLÍCITA. A métrica "Viraram orçamento" agora mostra "20 de 305 · 7%" em vez de só "20" — a pergunta que essa tela responde é quantos dos leads que entraram viraram proposta, e antes era preciso pegar o denominador na métrica do lado e fazer a conta na mão.

O QUE NÃO ENTROU, DE PROPÓSITO: a lista de prospecção fria raspada do Google Maps. Quem foi garimpado e nunca foi contatado não é lead que chegou — é matéria-prima, e misturá-lo aqui afundaria a conversão com gente que nunca falou com você. Assim que alguém conversa com um garimpado, ele entra normalmente.

A ESPERA CONTINUA SENDO DO CHIP. "Nunca respondidos" e a mediana de espera seguem contando só quem mandou mensagem e ficou aguardando: cadastro manual não esperou por ninguém, e somá-lo ali diluiria justamente o número que mede o atendimento.$txt$,
 timestamptz '2026-09-07 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'leads-conversao-conta-cadastro-manual';
