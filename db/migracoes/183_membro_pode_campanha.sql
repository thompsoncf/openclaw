-- 183_membro_pode_campanha.sql
-- O vendedor pode criar campanha — quando o dono liberar, um a um.
--
-- Campanha era coisa de dono/gestor: o gate `gerencia` (web/painel_prospeccao.py)
-- barrava criar, e o vendedor só via as campanhas em que já era o responsável.
-- Na prática ele fica com o cliente na mão, sabe qual recorte quer trabalhar, e
-- dependia de alguém montar a campanha pra ele.
--
-- POR QUE UMA FLAG POR MEMBRO, e não um papel novo ou o `vendas` do CAPS:
-- campanha DISPARA mensagem em massa pelo número da empresa. O risco não é o
-- vendedor ser vendedor — é quantas pessoas podem disparar. Papel novo obrigaria
-- a escolher entre "todo vendedor pode" e "nenhum pode"; a flag deixa o dono
-- liberar pro vendedor que ele confia e manter os outros de fora, sem trocar o
-- papel de ninguém (e sem mexer nas outras permissões que o papel carrega).
--
-- Nasce FALSE de propósito: quem já está na equipe hoje não ganha poder novo
-- num deploy. Cada liberação é um clique consciente do dono.
--
-- Não vale pro dono nem pro gestor: esses já passam pelo `gerencia`, que continua
-- sendo o caminho deles. A flag só ADICIONA, nunca tira.
--
-- Aditivo e idempotente.

alter table public.membros
    add column if not exists pode_campanha boolean not null default false;

-- rollback:
--   alter table public.membros drop column if exists pode_campanha;
