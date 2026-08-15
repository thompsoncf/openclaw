-- 157_wa_qr_sessao_lock.sql
-- Quem está com a sessão do WhatsApp de cada conta, para não haver dois.
--
-- O Render faz deploy sem downtime: sobe a instância nova antes de matar a
-- velha. As duas rodam o mesmo restaurarSessoes() e abrem socket com a MESMA
-- credencial — e o WhatsApp derruba uma delas com 440 (connectionReplaced). Em
-- 15/08/2026 a conta 35 abriu 7 sessões entre 13:53 e 20:00 e todas morreram
-- assim; cada morte reinicia o ciclo (reconecta, rebaixa a agenda inteira,
-- redespeja no webhook). O serviço tem numInstances:1 — nunca foi escala
-- horizontal, é a janela de sobreposição do deploy.
--
-- A linha é um ALUGUEL, não uma trava eterna: vale até `expira_em` e a instância
-- dona renova de 20 em 20s. Se o processo morrer de qualquer jeito (SIGKILL,
-- OOM, máquina caindo), ninguém renova, o prazo vence e a próxima instância
-- assume em no máximo um TTL. É de propósito que não exista destravamento
-- manual: trava sem prazo é trava que um dia fica presa.
--
-- Por que tabela e não pg_advisory_lock: advisory lock de sessão mora na
-- CONEXÃO, e aqui a conexão passa pelo pooler do Supabase. Em modo transaction o
-- pooler multiplexa conexão entre clientes, então um lock tomado fora de
-- transação não tem dono estável. Segurar uma transação aberta pra sempre
-- resolveria, mas idle-in-transaction eterno é o que os poolers matam. Linha com
-- prazo funciona igual em qualquer pooler — e ainda deixa CONSULTAR quem segura
-- o quê, coisa que advisory lock não mostra.
--
-- Quem escreve aqui é o serviço Node (services/wa-qr/sessao-lock.js). Esta
-- migração roda pelo web, que é quem aplica migração; nos minutos entre um
-- deploy e outro o wa-qr tolera a tabela não existir e segue sem trava, avisando
-- no log.
--
-- Diagnóstico do dia a dia:
--     select * from wa_qr_sessao_lock order by conta_id;
--   expira_em no passado = ninguém segurando (a próxima instância assume).
--
-- Aditivo e idempotente.

create table if not exists public.wa_qr_sessao_lock (
    conta_id   bigint primary key references public.contas(id) on delete cascade,
    -- identidade da instância: host:pid:boot. O boot entra porque o Render reusa
    -- nome de host entre deploys — sem ele uma instância nova poderia renovar a
    -- trava da antiga, que é justamente o que isto existe pra impedir.
    dono       text not null,
    expira_em  timestamptz not null,
    criado_em  timestamptz not null default now(),
    atualizado timestamptz not null default now()
);

-- Só pra varrer aluguel vencido em consulta de diagnóstico; a tomada da trava
-- vai pela primary key.
create index if not exists idx_wa_qr_sessao_lock_expira
    on public.wa_qr_sessao_lock (expira_em);

-- rollback:
--   drop table if exists public.wa_qr_sessao_lock;
