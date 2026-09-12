-- 247_temperatura_por_fato.sql
-- A temperatura do lead sugerida pelos fatos da conversa. § 3 e § 5 do Projeto
-- Adaptado da Prime (12/09/2026).
--
-- O QUE MEDIU ESTA MIGRAÇÃO
-- Na conta 34, 281 dos 284 leads em "Contatado" estavam QUENTE. Três frios, zero
-- mornos — e 15 dos 21 PERDIDOS também quentes. A causa está no código, não no
-- vendedor: todo lead é carimbado 'quente' na promoção (quatro caminhos fazem
-- isso), e o único que esfria é o agente de IA, que na Prime está desligado. O
-- campo satura e para de informar.
--
-- OS LIMIARES SÃO DO RAMO (migração 228): coluna vazia herda de
-- `raio_x_perfil._FUNIL_POR_PERFIL`. Eventos decide rápido — 48h sem o cliente
-- voltar já esfria; serviço recorrente respira mais devagar — 72h e 14 dias.
--
-- O MODO NÃO SE HERDA, e tem três valores de propósito:
--   off         nada roda. É como toda conta nasce.
--   observando  o motor calcula e a tela mostra o que MUDARIA. Não escreve.
--   ligado      escreve, com uma linha em funil_movimentos por lead.
-- O 'observando' existe porque ligar isto reescreve a temperatura de 281 leads da
-- Prime de uma vez. Ver a contagem antes é o que separa uma decisão de um susto.
--
-- Aditiva e idempotente.

alter table public.funil_regua
  add column if not exists temperatura_modo    text,
  -- vazias = herdam do ramo. NÃO levam NOT NULL DEFAULT: foi exatamente assim que
  -- as seis contas ficaram com 17 números idênticos que ninguém tinha escolhido.
  add column if not exists temp_quente_h       integer,
  add column if not exists temp_morno_dias     integer,
  add column if not exists temp_frio_tentativas integer;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'funil_regua_temperatura_modo_check') then
    alter table public.funil_regua
      add constraint funil_regua_temperatura_modo_check
      check (temperatura_modo is null or temperatura_modo in ('off','observando','ligado'));
  end if;
end $$;

comment on column public.funil_regua.temperatura_modo is
  'off | observando (calcula e mostra, não grava) | ligado (grava, com histórico)';
comment on column public.funil_regua.temp_quente_h is
  'horas desde a última fala do CLIENTE que ainda contam como quente; vazio herda do ramo';
comment on column public.funil_regua.temp_morno_dias is
  'dias sem o cliente falar até esfriar de vez; vazio herda do ramo';
comment on column public.funil_regua.temp_frio_tentativas is
  'tentativas nossas sem resposta que esfriam o lead; vazio herda do ramo';

-- rollback:
--   alter table public.funil_regua drop constraint funil_regua_temperatura_modo_check;
--   alter table public.funil_regua drop column temperatura_modo, drop column temp_quente_h,
--     drop column temp_morno_dias, drop column temp_frio_tentativas;
