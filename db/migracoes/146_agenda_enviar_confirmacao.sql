-- 146_agenda_enviar_confirmacao.sql
-- Liga/desliga a RESPOSTA AUTOMÁTICA que o Zaq manda pro convidado quando ele
-- responde ao convite pelo WhatsApp (o "✅ presença confirmada" com calendário e
-- mapa — ver convites.confirmacao_texto). Até agora essa era a única das quatro
-- mensagens da agenda sem controle nenhum: sempre saía.
--
-- default true preserva o comportamento de quem já usa (mesmo raciocínio da 126).
-- Desligar afeta SÓ o convidado: o aviso pro dono de que alguém respondeu
-- continua saindo de qualquer jeito. Idempotente.

alter table public.agenda_config
  add column if not exists enviar_confirmacao boolean not null default true;
