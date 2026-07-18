-- 072_membro_login_web.sql
-- Login por usuário (membro) no painel web + papéis de equipe.
-- Antes o membro só entrava por chat (telegram/whatsapp). Agora ganha e-mail +
-- senha próprios e um papel (gestor/vendedor/financeiro). Convite por link:
-- convite_token + convite_expira. Espelhado em runtime por
-- contas.equipe.garantir_tabela (o deploy não roda migração sozinho).
alter table membros add column if not exists email          text;
alter table membros add column if not exists senha_hash     text;
alter table membros add column if not exists convite_token  text;
alter table membros add column if not exists convite_expira timestamptz;

-- relaxa o check antigo (só 'dono'/'membro') pra aceitar os papéis de equipe.
alter table membros drop constraint if exists membros_papel_check;

create unique index if not exists idx_membros_email
    on membros (lower(email)) where email is not null;
create unique index if not exists idx_membros_convite
    on membros (convite_token) where convite_token is not null;
