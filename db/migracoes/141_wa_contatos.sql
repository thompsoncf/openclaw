-- 141_wa_contatos.sql
-- Agenda do celular conectado por QR, guardada de verdade.
--
-- O WhatsApp despeja os contatos (contacts.upsert / a lista que vem junto de cada
-- bloco do histórico) ANTES — ou no meio — da enxurrada de mensagens. Até agora o
-- webhook só sabia renomear conversa JÁ existente, então na importação inicial os
-- nomes chegavam primeiro, não achavam conversa nenhuma e eram jogados fora: a
-- conversa nascia segundos depois já com o número cru e ficava assim pra sempre.
--
-- Com a agenda guardada aqui a ordem deixa de importar: conversa criada depois
-- nasce com o nome, e conversa que já existia continua sendo renomeada na hora.
-- numero8 = últimos 8 dígitos (mesma chave de casamento usada no resto do módulo,
-- que ignora o 9 extra e variações de DDI). Idempotente.

create table if not exists public.wa_contatos (
  conta_id   bigint not null references public.contas(id) on delete cascade,
  numero8    text   not null,
  nome       text   not null,
  da_agenda  boolean not null default false,
  atualizado timestamptz not null default now(),
  primary key (conta_id, numero8)
);
