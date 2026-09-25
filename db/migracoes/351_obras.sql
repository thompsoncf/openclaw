-- 351_obras.sql
-- AS OBRAS da construtora: cada casa pra vender e cada reforma de cliente. PR 2
-- de 4 do desenho aprovado pelo dono em 25/09/2026 (docs/mockups/nicho_construcao.html,
-- seções 05 a 07). Primeira conta: PX2 Empreendimentos (conta 33).
--
-- POR QUE EXISTE. Medido na produção em 25/09 (só leitura): a PX2 tinha 13
-- despesas de obra (R$ 21.980,48) — material, "mão de obra primeira etapa",
-- cerca elétrica em "3 casas", 6 kits de bacia — e nenhuma dizia de qual casa
-- era. Casa popular dá lucro ou não pelo custo de cada casa contra o que a Caixa
-- paga, e esse número não existia.
--
-- TRÊS TABELAS:
--
-- * `obras`: a casa ou a reforma, com o que só obra tem (lote, m², previsto, valor
--   de venda ou de contrato, situação). CADA OBRA TEM UM CENTRO DE CUSTO, e é isso
--   que faz o desenho barato: DRE por centro, conta a pagar com centro e o agente
--   que já conhece os centros passam a falar de obra sem código novo. A obra NASCE
--   NO PAINEL (decisão 1 do dono, 25/09): o agente não cria centro de custo (regra
--   do dono de 23/09), então também não cria obra.
--
-- * `obra_etapas`: as etapas com peso. Os pesos da casa ficam dentro das faixas
--   da planilha de construção individual da Caixa (PCI); a obra nova copia as
--   etapas da última do mesmo tipo, então o ajuste que a PX2 fizer vale pras
--   próximas.
--
-- * `lancamento_rateio`: a nota de material que é das três casas. O LANÇAMENTO NÃO
--   É QUEBRADO em três: o pagamento continua um só, com o valor do comprovante, e
--   o rateio diz quanto dele é de cada obra. Quebrar faria a nota mandada de novo
--   não ser reconhecida como repetida (checar_duplicata procura o valor inteiro) e
--   faria o comprovante deixar de bater com a conta a pagar que ele quita.
--
-- Nada é apagado: obra encerrada é ARQUIVADA (regra 0 do CLAUDE.md). Por isso as
-- FKs pra `obras` não têm cascade. A única cascade é a do rateio pro lançamento:
-- o rateio é parte do lançamento, e lançamento apagado leva a divisão junto.
--
-- Aditiva e idempotente.

create table if not exists public.obras (
    id                       bigserial primary key,
    conta_id                 bigint not null references public.contas(id),
    centro_custo_id          bigint not null references public.centros_custo(id),
    tipo                     text   not null default 'casa'
                             check (tipo in ('casa', 'reforma')),
    nome                     text   not null,
    endereco                 text   not null default '',
    area_m2                  numeric(10,2)
                             check (area_m2 is null or area_m2 > 0),
    custo_previsto_centavos  bigint
                             check (custo_previsto_centavos is null or custo_previsto_centavos >= 0),
    -- casa: o preço de venda previsto; reforma: o valor do contrato
    valor_centavos           bigint
                             check (valor_centavos is null or valor_centavos >= 0),
    status                   text   not null default 'em_obra'
                             check (status in ('em_obra', 'pronta', 'vendida',
                                               'entregue', 'arquivada')),
    inicio_em                date,
    previsao_em              date,
    concluida_em             date,
    obs                      text   not null default '',
    criado_por               bigint,
    criado_em                timestamptz not null default now(),
    atualizado_em            timestamptz not null default now()
);
-- uma obra, um centro; e o nome é como o Pablo chama a casa no WhatsApp, então
-- não pode repetir dentro da conta
create unique index if not exists ux_obras_centro on public.obras (centro_custo_id);
create unique index if not exists ux_obras_conta_nome on public.obras (conta_id, lower(nome));
create index if not exists idx_obras_conta_status on public.obras (conta_id, status);

create table if not exists public.obra_etapas (
    id           bigserial primary key,
    conta_id     bigint   not null references public.contas(id),
    obra_id      bigint   not null references public.obras(id),
    chave        text     not null,
    nome         text     not null,
    peso         numeric(6,2) not null default 0 check (peso >= 0),
    ordem        smallint not null default 0,
    concluida_em date,
    unique (obra_id, chave)
);
create index if not exists idx_obra_etapas_obra on public.obra_etapas (obra_id, ordem);

create table if not exists public.lancamento_rateio (
    id               bigserial primary key,
    conta_id         bigint not null references public.contas(id),
    lancamento_id    bigint not null references public.lancamentos(id) on delete cascade,
    centro_custo_id  bigint not null references public.centros_custo(id),
    valor_centavos   bigint not null check (valor_centavos >= 0),
    criado_em        timestamptz not null default now(),
    unique (lancamento_id, centro_custo_id)
);
create index if not exists idx_lancamento_rateio_centro
    on public.lancamento_rateio (conta_id, centro_custo_id);

-- rollback (manual, e só se nenhuma conta tiver obra cadastrada):
--   drop table if exists public.lancamento_rateio;
--   drop table if exists public.obra_etapas;
--   drop table if exists public.obras;
