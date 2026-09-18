-- 284_aviso_envios_recibo.sql
-- O degrau seguinte do registro de aviso: além de "saiu", saber se CHEGOU e se a
-- pessoa VIU.
--
-- POR QUE. A 276 registrou o envio e no primeiro dia já respondeu a pergunta que
-- existia ("o dono não recebeu nada, em dois canais"). Sobrou a pergunta seguinte,
-- que é a que o dono faz depois: "e eles leem?". Hoje o painel sabe responder isso
-- pra mensagem de CLIENTE — o card de desempenho da campanha mostra entregue e
-- lido, com recibo de verdade vindo do aparelho — e não sabe responder pro aviso
-- interno, que é justamente o que manda o vendedor trabalhar.
--
-- OS TRÊS CANAIS NÃO SABEM A MESMA COISA, e as colunas respeitam isso:
--
--   WhatsApp  `sid` + `entregue_em` + `lido_em`  — recibo real do aparelho (✓✓ e 👀),
--             o mesmo caminho que já alimenta `mensagens` e `campanha_alvos`.
--   Push      `token` + `clicado_em`             — não existe "viu" no navegador;
--             existe o CLIQUE, que é sinal honesto: quem tocou abriu o painel.
--   E-mail    nada                               — abertura só se mede com pixel, e
--             o proxy do Gmail pré-carrega imagem, então o número seria alto e
--             falso. A tela mostra travessão, não zero.
--
-- O `token` é do push e é segredo: é ele que autentica o clique quando o service
-- worker avisa de volta, sem sessão e sem cookie. Por isso é aleatório e longo, e
-- por isso o índice é único — dois avisos com o mesmo token tornariam o clique
-- ambíguo.
--
-- O `sid` NÃO é único de propósito: é o id da mensagem no WhatsApp, e o mesmo
-- recibo pode chegar duas vezes (o provedor repete). A regra de nunca regredir vive
-- no código (`aviso_log.marcar_recibo`), como já vive em `aplicar_status_wa`.
--
-- Aditivo e idempotente.

alter table public.aviso_envios add column if not exists sid         text;
alter table public.aviso_envios add column if not exists token       text;
alter table public.aviso_envios add column if not exists entregue_em timestamptz;
alter table public.aviso_envios add column if not exists lido_em     timestamptz;
alter table public.aviso_envios add column if not exists clicado_em  timestamptz;

-- o recibo chega pelo webhook com o sid na mão: é esta a busca do caminho quente
create index if not exists idx_aviso_envios_sid
    on public.aviso_envios (sid) where sid is not null;

-- único porque o token AUTENTICA o clique; repetido, o clique seria de qualquer um
create unique index if not exists idx_aviso_envios_token
    on public.aviso_envios (token) where token is not null;

-- rollback:
--   drop index if exists public.idx_aviso_envios_token;
--   drop index if exists public.idx_aviso_envios_sid;
--   alter table public.aviso_envios drop column if exists clicado_em;
--   alter table public.aviso_envios drop column if exists lido_em;
--   alter table public.aviso_envios drop column if exists entregue_em;
--   alter table public.aviso_envios drop column if exists token;
--   alter table public.aviso_envios drop column if exists sid;
