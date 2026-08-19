-- 171_prospeccao_endereco_nascimento.sql
-- Endereço e data de nascimento do lead, pro app do vendedor.
--
-- `prospeccao` tinha só cidade/uf: quem vai VISITAR não tinha onde anotar rua e
-- número, e não havia data de aniversário em lugar nenhum. O CEP é o campo que
-- puxa o resto (finance/cep.py -> BrasilAPI), por isso ele também é guardado:
-- sem ele não dá pra reconsultar nem conferir o que foi preenchido sozinho.
--
-- `nascimento` é DATE (não timestamptz): aniversário não tem hora nem fuso, e
-- guardar como timestamp faria a data virar o dia anterior pra quem está a
-- oeste de Greenwich na hora de comparar.

alter table public.prospeccao add column if not exists cep        text;
alter table public.prospeccao add column if not exists endereco   text;
alter table public.prospeccao add column if not exists numero     text;
alter table public.prospeccao add column if not exists bairro     text;
alter table public.prospeccao add column if not exists nascimento date;

-- O aviso de aniversário dedupa em lembretes_enviados com tipo='aniversario', e o
-- CHECK da 101 não conhece esse valor. Sem recriar o check aqui, o insert do
-- _primeira_vez levanta CheckViolation — e, como ele roda numa conexão própria e
-- SEM try/except no chamador, a exceção sobe até lembretes._rodar() e aborta o laço
-- de TODAS as contas daquele tick do ticker, não só o aniversário. É exatamente o
-- incidente que a migração 128 documenta, com o 'aviso_convidado'. O nome do
-- constraint é estável desde a 101.
alter table public.lembretes_enviados drop constraint if exists lembretes_enviados_tipo_check;
alter table public.lembretes_enviados add constraint lembretes_enviados_tipo_check
    check (tipo in ('resumo','aviso','aviso_convidado','aniversario'));

-- Aniversariantes do dia: a varredura filtra por dia/mês, não pela data inteira.
create index if not exists idx_prospeccao_nascimento
    on public.prospeccao (conta_id, (extract(month from nascimento)), (extract(day from nascimento)))
    where nascimento is not null;
