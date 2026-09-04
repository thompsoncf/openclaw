-- 196_wa_decifra_diario.sql
-- Quanto o WhatsApp deixa de decifrar, por dia, por conta e por direção.
--
-- POR QUE UMA TABELA, se o dado já está no `wa_qr_log`: porque ele guarda 48h de
-- propósito (migração 158 — "ferramenta de diagnóstico, não arquivo histórico").
-- Toda pergunta que começa com "isso piorou?" morre nessa janela. Aqui fica só o
-- resumo, que é minúsculo e responde a pergunta.
--
-- O QUE SE MEDE, e por que separado por direção:
--
--   fromMe=false  a mensagem do CLIENTE não decifrou. Se ela não chegar depois,
--                 é perda de verdade — a regra 0 do CLAUDE.md.
--   fromMe=true   o ECO da mensagem que a própria empresa mandou pelo celular. O
--                 CLAUDE.md classifica como ruído, e está certo quanto ao que
--                 aquele texto trata: não se perde mensagem de cliente, e não
--                 justifica re-parear. Mas o eco perdido some do PAINEL, e aí o
--                 inbox mostra o cliente perguntando e ninguém respondendo,
--                 enquanto no celular a resposta está lá. Medido em 04/09/2026 na
--                 conta 34: 8 respostas assim em dois dias.
--
-- `ids_distintos` e não `ocorrencias`: o retry do Baileys repete o MESMO id várias
-- vezes (110 linhas de log viraram 19 mensagens na medição de 04/09). Contar linha
-- multiplica o problema por 5 e faz qualquer série temporal mentir.
--
-- `chegaram` / `nunca_chegaram` ficam NULOS até a correlação rodar, e ela roda uma
-- vez por dia, só pro dia que já fechou. O motivo é de custo: cruzar id com
-- `mensagens.provider_sid` não tem índice que sirva (o único é
-- (conversa_id, provider_sid), que não atende busca só pelo sid), então é
-- varredura — barata uma vez por dia, proibida no ciclo de 2 min. Varredura de
-- `mensagens` por evento é exatamente o que derrubou o app em 15/08.
--
-- Aditivo e idempotente. Só leitura de log vira linha aqui; nada de cliente é
-- tocado.

create table if not exists public.wa_decifra_diario (
    dia               date    not null,
    conta_id          bigint  not null,
    from_me           boolean not null,
    -- linhas de log (com o retry); serve pra enxergar o barulho, não o estrago
    ocorrencias       int     not null default 0,
    -- mensagens de verdade por trás dessas linhas
    ids_distintos     int     not null default 0,
    -- preenchidos pela correlação, um dia depois (null = ainda não apurado)
    chegaram          int,
    nunca_chegaram    int,
    correlacionado_em timestamptz,
    apurado_em        timestamptz not null default now(),
    primary key (dia, conta_id, from_me)
);

-- a correlação procura exatamente por isto: dia fechado e ainda sem apurar
create index if not exists idx_wa_decifra_pendente
    on public.wa_decifra_diario (dia) where correlacionado_em is null;
