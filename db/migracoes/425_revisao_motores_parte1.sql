-- Revisão dos motores do funil, parte 1 (27/09/2026).
--
-- 1. `envios_automaticos`: o TETO POR CHIP (finance/teto_chip.py). Cada mensagem que
--    o SISTEMA inicia ao cliente (confirmação e véspera da visita, pós-festa, aviso da
--    lista de espera, toque da IA) deixa uma linha aqui, com o chip por onde saiu.
--    Somada ao que o resgate já registra em `resgate_envios`, dá a conta de quantas
--    saíram por chip na última hora e no dia — e segura quando passa do teto, pra
--    nenhum chip de QR disparar em rajada.
-- 2. `pos_festa_envios`: as tentativas do pós-festa da IA passam a ser contadas
--    (no máximo 3, com 30 min entre elas). Antes, uma falha apagava a linha e o
--    relógio tentava de novo a cada ~2 min, sem fim. As linhas que já existem são
--    envios que JÁ SAÍRAM (a falha apagava a linha): ficam marcadas como enviadas.
--
-- Não toca conversa, mensagem, conexão, chip nem `canais_config` (CLAUDE.md §0/§1),
-- nem card nenhum. Aditiva e idempotente.

create table if not exists public.envios_automaticos (
  id           bigserial primary key,
  conta_id     bigint not null,
  chip_id      bigint,
  conversa_id  bigint,
  origem       text not null,
  criado_em    timestamptz not null default now()
);

create index if not exists envios_automaticos_conta_chip_idx
  on public.envios_automaticos (conta_id, chip_id, criado_em desc);

alter table if exists public.pos_festa_envios
  add column if not exists envio_falhas    integer not null default 0,
  add column if not exists envio_falhou_em timestamptz,
  add column if not exists enviado_em      timestamptz;

do $$
begin
  if to_regclass('public.pos_festa_envios') is not null then
    update public.pos_festa_envios
       set enviado_em = criado_em
     where quem = 'ia' and enviado_em is null and envio_falhou_em is null;
  end if;
end $$;
