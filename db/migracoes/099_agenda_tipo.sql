-- 099_agenda_tipo.sql
-- Categoria do compromisso, pra colorir na aba Agenda do portal e separar o que é
-- pessoal do que é da empresa/fornecedor. Default 'pessoal' (o que o chat marca).
-- Idempotente.

alter table public.eventos_agenda
  add column if not exists tipo text not null default 'pessoal'
  check (tipo in ('pessoal','empresa','fornecedor'));
