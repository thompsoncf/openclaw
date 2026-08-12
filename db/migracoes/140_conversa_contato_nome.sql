-- 140_conversa_contato_nome.sql
-- Nome do contato no WhatsApp (o "pushName" do perfil, que vem junto de cada
-- mensagem) guardado na própria conversa. Sem isso, conversa que ainda não virou
-- lead aparecia no painel como o número cru ("558694867388"), mesmo o WhatsApp
-- tendo mandado o nome do perfil junto com a mensagem — a informação chegava e
-- era jogada fora. Só serve pra EXIBIÇÃO: quem manda no título continua sendo a
-- empresa do lead quando a conversa já está ligada a um. Idempotente.

alter table public.conversas
  add column if not exists contato_nome text;
