-- 377_sinapi_referencia.sql
-- O custo médio do m² de construção por estado, do SINAPI (IBGE, tabela 2296 do
-- SIDRA), pra ficha da obra comparar (finance/sinapi.py). Desenho aprovado pelo
-- dono em 25/09/2026: docs/mockups/nicho_construcao.html, seção 06 — "Referência
-- SINAPI-MA ago/2026: R$ 1.969/m² de obra, pra comparar e não pra cobrar".
--
-- A tabela é um CACHE da API pública do IBGE (apisidra.ibge.gov.br): o ticker do
-- web busca uma vez por dia, só pros estados das contas de construção. A semente
-- abaixo é o Maranhão de agosto/2026, conferido na API em 26/09/2026:
--   variável 48   custo médio m² ............ R$ 1.969,07
--   variável 2119 componente material ........ R$ 1.197,36
--   variável 2120 componente mão de obra ..... R$   771,71
--
-- Tabela global (não é de conta): é dado público, igual pra todo mundo do estado.
-- Aditiva e idempotente.

create table if not exists public.sinapi_referencia (
  uf                   varchar(2) not null,
  mes                  date       not null,          -- o primeiro dia do mês de referência
  total_centavos       int        not null,
  material_centavos    int,
  mao_de_obra_centavos int,
  buscado_em           timestamptz not null default now(),
  primary key (uf, mes)
);

insert into public.sinapi_referencia (uf, mes, total_centavos, material_centavos, mao_de_obra_centavos)
values ('MA', date '2026-08-01', 196907, 119736, 77171)
on conflict (uf, mes) do nothing;

-- rollback:
--   drop table if exists public.sinapi_referencia;
