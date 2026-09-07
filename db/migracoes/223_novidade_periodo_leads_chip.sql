-- 223_novidade_periodo_leads_chip.sql
-- Relatórios › Leads do chip: o filtro e a coluna passam a dizer o que medem.
--
-- O QUE MUDOU NA TELA
-- Nada de número. Dois rótulos:
--   * "período: Este mês"        -> "leads que entraram em: Este mês"
--   * coluna "Orçamento"         -> coluna "Orçamento do lead"
-- O filtro sempre recortou `prospeccao.criado_em` — a ENTRADA DO LEAD — e a coluna
-- sempre disse se AQUELE lead tem proposta. Os rótulos curtos prometiam outra
-- coisa: "orçamentos do período".
--
-- A MEDIÇÃO QUE MOTIVOU, conferida na produção em 07/09/2026 (conta 34):
-- o dono abriu a aba com "Este mês", viu a coluna inteira em "—" e o funil com
-- quatro orçamentos feitos no mês. Nenhum vínculo quebrado — os quatro estavam
-- ligados ao lead. Duas causas somadas:
--   nº 20 (03/09) e nº 21 (05/09) -> leads que entraram em 15/08, fora do período;
--   nº 18 e nº 19 (02/09)         -> leads com `origem = manual_vendedor` e ZERO
--                                    conversa, que não entram nesta aba em período
--                                    nenhum: ela é de quem chegou POR UM CHIP.
-- No mês, 48 leads entraram por chip e nenhum tinha orçamento. A coluna em "—"
-- estava certa; o que faltava era a tela dizer o que estava contando.
--
-- O PORTÃO: `todos`. A aba é de quem recebe lead por conversa de WhatsApp —
-- 5 contas em produção, em TRÊS nichos (34 e 35 eventos, 23 e 3 consultoria, 7 sem
-- nicho). Nenhum portão de nicho descreve esse alcance, e `canal_proprio` é
-- estritamente QR, enquanto a aba vale igual pra Twilio e Cloud API (o lead entra
-- por conversa nos três). Criar um portão novo pra uma troca de rótulo custaria
-- mais do que vale — §5 manda cair em 'todos' na dúvida.
--
-- PRA QUEM: dono e gestor. O vendedor NÃO entra: `/painel/relatorios` pede a
-- capacidade `financeiro` (contas/equipe.py:rotas_do_papel), que ele não tem — a
-- tela não existe pra ele.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('leads-chip-periodo-diz-o-que-filtra', 'novidade', 'todos', '{dono,gestor}',
 'Em Leads do chip, o filtro agora diz que é a data de entrada do lead',
 'O período dessa aba sempre recortou pela data em que o lead entrou, não pela data do orçamento — agora a tela escreve isso, e a coluna passou a se chamar "Orçamento do lead".',
 '/painel/relatorios?tipo=leads_chip',
 $txt$Nenhum número mudou nesta aba. O que mudou é o que ela escreve.

O QUE VOCÊ VAI VER. No topo, onde dizia "período: Este mês", agora diz "leads que entraram em: Este mês". E a coluna "Orçamento" virou "Orçamento do lead".

POR QUE. O filtro de período desta aba sempre recortou pela data em que o LEAD ENTROU, não pela data do orçamento. Então uma proposta feita hoje, para um cliente que chamou no mês passado, não aparece aqui com "Este mês" marcado — o lead dele é do mês passado. Do mesmo jeito, a coluna sempre respondeu "este lead tem proposta?", e não "quais propostas saíram no período".

Com os rótulos curtos, a coluna inteira em "—" parecia proposta sumida. Não era.

E TEM UM SEGUNDO MOTIVO PRA COLUNA VIR VAZIA, que vale conhecer: esta aba lista quem chegou POR UM CHIP de WhatsApp. Lead que o vendedor cadastrou na mão não entrou por chip nenhum e não aparece aqui, com ou sem proposta — em período nenhum. Pra ver todas as propostas, o lugar é a aba Serviços.

PRA VER AS PROPOSTAS DE LEADS MAIS ANTIGOS, troque o período pra "Últimos 90 dias" ou "Todo o período".$txt$,
 timestamptz '2026-09-07 18:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'leads-chip-periodo-diz-o-que-filtra';
