-- 126_agenda_avisar_convidados.sql
-- Controle explícito (switch no painel, igual ao "Aviso antes do compromisso")
-- pra ligar/desligar o aviso de "compromisso chegando" também pros convidados
-- que confirmaram presença — só vale quando aviso_antes_min também está ligado.
-- Default true: preserva o comportamento que já estava no ar (convidado
-- confirmado recebia o aviso sem opção de desligar).

alter table public.agenda_config
  add column if not exists avisar_convidados boolean not null default true;
