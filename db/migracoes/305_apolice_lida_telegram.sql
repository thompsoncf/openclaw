-- 305_apolice_lida_telegram.sql
-- O pré-cadastro passa a aceitar a apólice que chega pelo Telegram.
--
-- O PEDIDO (dono, 21/09/2026), depois de eu levantar as duas portas que tinham
-- ficado de fora do leitor automático: "pode fazer os 2".
--
-- POR QUE O TELEGRAM. O bot já é o assistente do MEMBRO — identifica pelo
-- telegram_id de quem está cadastrado e já aceita PDF, que hoje vira comprovante
-- no caixa. Pro corretor que prefere mandar por lá, é a mesma leitura e o mesmo
-- salvar, só outra porta. E o portão dali é MAIS forte que o do WhatsApp: no
-- WhatsApp o remetente é um número que alguém liberou; no Telegram é um membro
-- autenticado da conta.
--
-- O QUE MUDA NA TABELA. `mensagem_id` deixa de ser obrigatório: a apólice do
-- Telegram não tem linha em `mensagens`. O índice único vira PARCIAL, pra
-- continuar impedindo que a mesma mensagem do WhatsApp seja lida duas vezes sem
-- transformar "sem mensagem" num valor que colide consigo mesmo.
--
-- `origem` e `de` existem pra tela saber o que mostrar: pelo WhatsApp o rótulo é
-- quem mandou; pelo Telegram, o nome do membro que subiu.
--
-- Aditivo e idempotente.

alter table public.apolice_lida alter column mensagem_id drop not null;

alter table public.apolice_lida add column if not exists origem text not null default 'whatsapp';
alter table public.apolice_lida add column if not exists de     text;

alter table public.apolice_lida drop constraint if exists apolice_lida_origem_check;
alter table public.apolice_lida add constraint apolice_lida_origem_check
    check (origem in ('whatsapp', 'telegram'));

-- PARCIAL: duas apólices do Telegram não têm mensagem, e sem o `where` elas
-- brigariam pelo mesmo NULL em bancos que tratam NULL como valor no índice.
drop index if exists ux_apolice_lida_msg;
create unique index if not exists ux_apolice_lida_msg
    on public.apolice_lida (mensagem_id) where mensagem_id is not null;

-- a tela lista por conta e origem, do mais novo pro mais velho
create index if not exists ix_apolice_lida_conta_origem
    on public.apolice_lida (conta_id, origem, criado_em desc);

-- rollback:
--   drop index if exists ix_apolice_lida_conta_origem;
--   drop index if exists ux_apolice_lida_msg;
--   create unique index ux_apolice_lida_msg on public.apolice_lida (mensagem_id);
--   alter table public.apolice_lida drop constraint if exists apolice_lida_origem_check;
--   alter table public.apolice_lida drop column if exists de;
--   alter table public.apolice_lida drop column if exists origem;
