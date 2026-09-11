-- 230_funil_teto_da_etapa.sql
-- O teto de dias numa etapa, as renovações com justificativa, e a história de quem
-- renovou. Regras 1 e 2 do FLUXO_FINAL_FUNIL_PRIME_EVENTOS_DESENVOLVEDOR_V3,
-- desenhadas em docs/mockups/fluxo_funil_prime_v3.html.
--
-- O QUE O DONO PEDIU
--   "O CRM deve impedir que o vendedor use CONTACTADO como estacionamento de lead.
--    O prazo inicial em CONTACTADO é de 7 dias, com possibilidade de duas renovações
--    de 7 dias cada, totalizando no máximo 21 dias. Cada renovação somente poderá
--    ser liberada após o vendedor preencher obrigatoriamente a caixa de
--    justificativa."
--
-- MEDIDO ANTES (conta 34, 11/09/2026), e é o que diz que a regra está certa:
--   275 leads em Contactado — 87% de todo o funil ativo
--    75 já passaram de 21 dias · 89 vencem dentro de uma semana
--     0 desses 75 tiveram mensagem do cliente nos últimos 7 dias
-- O último número é o que importa: NENHUM dos estourados está em conversa viva. O
-- teto não vai expulsar ninguém que esteja sendo trabalhado — eles estão parados
-- de verdade. E o retrato do porquê: 325 movimentos automáticos contra ~50 manuais
-- em 23 dias, com a coluna "Follow-up", que já existe, em ZERO leads. O funil tem
-- entrada automática e saída manual que ninguém usa.
--
-- POR QUE O TETO É DA ETAPA, E NÃO "A REGRA DO CONTACTADO"
-- Pedido do dono em 11/09: "sempre tem que pensar dessa forma", sobre servir a
-- outras empresas. Construir "a regra dos 21 dias do Contactado" resolveria a Prime
-- e mais ninguém. Aqui QUALQUER etapa pode ter teto: Contactado fica 7 × 2 por
-- configuração, e pôr teto na Proposta amanhã é preencher um campo. Quem não quer
-- teto nenhum deixa tudo vazio, que é como todas as contas nascem.
--
-- NASCE DESLIGADO, como tudo nesta régua. `teto_modo` começa 'off' e nenhuma etapa
-- nasce com teto. Em 'observando' o motor calcula e grava o que TERIA avisado, sem
-- mandar push nenhum — foi o ensaio que pegou o defeito do relógio da festa, e é
-- assim que se descobre o volume antes de alguém receber o primeiro aviso.

-- ---------------------------------------------------------------- 1. a etapa
alter table public.funil_etapas
  -- dias que um lead pode ficar nesta etapa antes de vencer. NULL = sem teto.
  -- É o PERÍODO, não o total: com renovacoes_max=2, o total vira 3 × teto_dias.
  add column if not exists teto_dias           integer,
  -- quantas renovações o vendedor pode pedir. 0 = nenhuma (vence e acabou).
  add column if not exists renovacoes_max      integer not null default 0,
  -- a trava que o dono pediu: sem texto, a renovação não é liberada.
  add column if not exists exige_justificativa boolean not null default true,
  -- MELHORIA 1 DO MOCKUP, ainda não aprovada — por isso nasce NULL, que é o
  -- documento como está escrito. Preenchido com 48, renovar é automático e sem
  -- justificativa quando o cliente falou nas últimas 48h, e a justificativa
  -- continua obrigatória pra lead MUDO, que é o problema que a regra ataca.
  add column if not exists renova_sozinho_h    integer;

comment on column public.funil_etapas.teto_dias is
  'dias por período nesta etapa; null = sem teto. Total = teto_dias * (1 + renovacoes_max)';
comment on column public.funil_etapas.renova_sozinho_h is
  'horas de conversa recente que dispensam justificativa na renovação; null = nunca dispensa';

-- ---------------------------------------------------------------- 2. a config
alter table public.funil_regua
  -- off | observando | ligado — o quarto modo, irmão dos três que já existem.
  -- Um modo POR REGRA, e não um interruptor geral: o dono liga uma, olha uma
  -- semana, liga a próxima. Foi assim que os gatilhos entraram.
  add column if not exists teto_modo        text not null default 'off',
  -- "no 6º e no 7º dia de cada período de 7 dias" = avisar nos 2 dias finais.
  -- Herda do nicho quando NULL (migração 228).
  add column if not exists teto_avisar_antes integer;

alter table public.funil_regua drop constraint if exists funil_regua_teto_modo_check;
alter table public.funil_regua
  add constraint funil_regua_teto_modo_check
  check (teto_modo in ('off', 'observando', 'ligado'));

-- ---------------------------------------------------------------- 3. a história
-- O banco guarda o prazo VIGENTE na própria etapa; o que ele nunca teve é quem
-- empurrou a data, quantas vezes e por quê. Sem isso o vendedor renova toda vez que
-- o aviso chega e o painel fica verde sem ninguém ter falado com ninguém — que é
-- exatamente o que a justificativa existe pra impedir. Nada é sobrescrito: cada
-- renovação é uma linha nova.
create table if not exists public.funil_renovacoes (
  id            bigserial primary key,
  conta_id      bigint not null,
  prospeccao_id bigint not null,
  -- a etapa em que estava quando renovou (a chave, não o id: etapa renomeada ou
  -- removida não pode apagar a história de quem renovou)
  etapa         text not null,
  -- qual renovação é esta (1 = a primeira). Guardado, e não contado na hora, pra
  -- resposta não mudar quando o dono aumentar `renovacoes_max` depois.
  ordem         integer not null default 1,
  membro_id     bigint,
  justificativa text not null default '',
  -- true quando foi o sistema que renovou por conversa recente (renova_sozinho_h),
  -- e é o que separa "alguém justificou" de "o cliente respondeu" no relatório.
  automatica    boolean not null default false,
  -- até quando o prazo foi empurrado, pra tela não ter que recalcular pra trás
  vence_em      timestamptz,
  criado_em     timestamptz not null default now()
);
create index if not exists idx_fr_renov_lead  on public.funil_renovacoes (prospeccao_id, criado_em desc);
create index if not exists idx_fr_renov_conta on public.funil_renovacoes (conta_id, criado_em desc);

-- rollback:
--   drop table public.funil_renovacoes;
--   alter table public.funil_regua drop column teto_modo, drop column teto_avisar_antes;
--   alter table public.funil_etapas drop column teto_dias, drop column renovacoes_max,
--     drop column exige_justificativa, drop column renova_sozinho_h;
