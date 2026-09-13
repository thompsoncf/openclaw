-- 254_suporte_acessos.sql
-- A trilha do "Entrar como": quem do suporte entrou na conta de qual cliente,
-- quando, até quando ficou, e se chegou a sair do modo leitura.
--
-- POR QUE EXISTE: até hoje, ver a tela de um cliente era pedir a senha dele ou
-- virar membro da equipe dele (o que aparece na tela de Equipe e precisa ser
-- criado e removido conta a conta). A alternativa cogitada — uma senha mestra
-- pra todas as empresas — não responde "quem entrou nessa conta?", não dá pra
-- revogar de uma conta só e cai tudo junto se vazar. Esta tabela é o que faz o
-- acesso de suporte ser auditável em vez de anônimo.
--
-- MODO LEITURA e 60 MINUTOS são as duas decisões do dono (13/09/2026) e moram no
-- código (contas/suporte.py); aqui fica só o registro do que aconteceu.
--
-- `destravado_em` já nasce na tabela e continua NULO na fase 1, que não tem
-- destravamento: a coluna existir agora evita uma migração de ALTER na fase 2
-- pra uma tabela que ninguém ainda consulta. Nulo quer dizer "só olhou", e é
-- essa a leitura que interessa.
--
-- A ENTRADA TAMBÉM VIRA EVENTO em `eventos_conta` (contas.contas.registrar_evento),
-- que é a auditoria administrativa que já existe e que a tela do admin já lista —
-- assim a trilha aparece onde alguém já olha, sem tela nova.
--
-- Aditiva e idempotente.

create table if not exists suporte_acessos (
    id             bigserial primary key,
    admin_conta_id bigint not null references contas(id),
    conta_id       bigint not null references contas(id),
    iniciado_em    timestamptz not null default now(),
    encerrado_em   timestamptz,
    encerrado_por  text check (encerrado_por in ('voltou','expirou')),
    destravado_em  timestamptz,
    motivo         text
);

-- "quem entrou nesta conta?" é a pergunta do cliente; "onde eu entrei hoje?" é a
-- do suporte. Um índice pra cada, porque as duas são feitas por data decrescente.
create index if not exists ix_suporte_acessos_conta
    on suporte_acessos (conta_id, iniciado_em desc);
create index if not exists ix_suporte_acessos_admin
    on suporte_acessos (admin_conta_id, iniciado_em desc);

-- rollback:
--   drop table if exists suporte_acessos;
