-- 257_trava_insistencia.sql
-- A TRAVA DA INSISTÊNCIA: o modo e a tabela do ensaio.
--
-- Pedido do dono em 14/09/2026: "colocar uma trava no chat até 7 dias ... eles só
-- podem falar com o cliente se der uma justificativa". O mockup que ele aprovou é
-- docs/mockups/trava_justificativa.html, e a aprovação veio com uma condição, dita
-- com estas palavras: "sim, poupa quem está esperando. faz o ensaio primeiro".
--
-- POR QUE A TRAVA NÃO É POR CALENDÁRIO
-- Medido na conta 34 em 14/09: dos 236 leads que passaram do teto de 7 dias em
-- Contatado, 210 são casos em que NÓS falamos por último — e 26 são casos em que o
-- CLIENTE escreveu e ninguém respondeu ainda. Travar por calendário calaria a
-- empresa justamente com esses 26. Então o gatilho é quem falou por último, nunca
-- o relógio sozinho.
--
-- `trava_modo` NASCE 'off', e por enquanto só existem dois estados de verdade:
--   off          não olha nada (padrão de toda conta)
--   observando   conta o que TERIA travado, sem travar nada
-- 'ligado' ainda não é aceito pelo código: travar de verdade depende da tela de
-- justificativa, que só será construída depois que o dono escolher os motivos.
-- Um modo que a tela não sabe cumprir seria uma chave que mente.
--
-- A tabela registra TENTATIVA, não lead parado. É a diferença entre responder
-- "quantos leads estão velhos" (já sabíamos: 236) e "quantas justificativas por dia
-- isso vira para cada vendedor", que é a pergunta que decide se 7 dias é o número
-- certo. Só entra linha quando a regra ENGATA — tentativa dentro do prazo não vira
-- registro, senão a tabela viraria uma cópia de `mensagens`.
--
-- Aditiva e idempotente.

alter table public.funil_regua
  add column if not exists trava_modo text not null default 'off';

create table if not exists public.funil_trava_tentativa (
  id             bigserial primary key,
  conta_id       bigint      not null,
  prospeccao_id  bigint      not null,
  membro_id      bigint,
  etapa          text        not null default '',
  -- o que a regra viu no momento da tentativa
  decisao        text        not null,
  bola           text        not null default '',
  dias_na_etapa  numeric(8,2),
  renovacoes     int         not null default 0,
  tentativas     int         not null default 0,
  -- true enquanto o modo for 'observando': a mensagem SAIU, só foi contada
  simulado       boolean     not null default true,
  criado_em      timestamptz not null default now()
);

-- as três decisões que engatam. 'no_prazo' não é gravada — ver o comentário acima.
alter table public.funil_trava_tentativa
  drop constraint if exists funil_trava_tentativa_decisao_ck;
alter table public.funil_trava_tentativa
  add constraint funil_trava_tentativa_decisao_ck
  check (decisao in ('pediria_justificativa', 'parede', 'poupou_cliente_esperando'));

-- o relatório do ensaio é sempre "esta conta, nesta semana, por vendedor"
create index if not exists ix_trava_tentativa_conta_dia
  on public.funil_trava_tentativa (conta_id, criado_em desc);

-- rollback:
--   drop table if exists public.funil_trava_tentativa;
--   alter table public.funil_regua drop column if exists trava_modo;
