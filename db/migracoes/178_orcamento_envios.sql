-- O REGISTRO DE QUE A PROPOSTA FOI MANDADA.
--
-- O funil sabia gerar o link e abrir o PDF, mas não sabia se alguém tinha
-- mandado — e "será que já mandei pra Carla?" só se respondia abrindo o Gmail.
-- Na dúvida, manda-se duas vezes.
--
-- Guarda o DESTINO junto porque o e-mail do cliente pode mudar entre um envio e
-- outro: "mandei" e "mandei pra onde" são perguntas diferentes.
--
-- `ok` grava também a tentativa que FALHOU. Sem isso, uma caixa com senha errada
-- ficaria silenciosamente sem mandar nada e a tela diria "nunca enviado", que é
-- verdade e é inútil — a informação que resolve é "tentou 3 vezes e falhou".
create table if not exists public.orcamento_envios (
    id            bigserial primary key,
    conta_id      bigint      not null,
    orcamento_id  bigint      not null,
    canal         text        not null default 'email',
    destino       text        not null default '',
    remetente     text        not null default '',   -- por qual caixa saiu
    ok            boolean     not null default true,
    erro          text        not null default '',
    por           text        not null default '',   -- membro_id, ou 'dono'
    criado_em     timestamptz not null default now()
);

-- a tela pergunta sempre a mesma coisa: os envios DESTE orçamento, do mais
-- recente pro mais antigo.
create index if not exists idx_orc_envios_orcamento
    on public.orcamento_envios (orcamento_id, criado_em desc);
