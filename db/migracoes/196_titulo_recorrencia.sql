-- 196_titulo_recorrencia.sql
-- O RITMO da conta que repete, e o VALOR que ela não sabe.
--
-- POR QUE AGORA. O motor de repetição existe desde a 053: `titulos.recorrente` é
-- lido pelo `dar_baixa_titulo`, que já cria sozinho o título do mês seguinte
-- herdando a aprovação. O que nunca existiu foi a PORTA — nem o formulário de
-- criar, nem a linha, nem o editar tinham como ligar o campo; a única coisa que
-- o gravava era a ferramenta do agente do WhatsApp. Medido na Prime em
-- 04/09/2026: 0 de 39 títulos marcados, e o dono redigitando as mesmas contas
-- todo mês — ENERGIA SOLAR, BANCO DO NORDESTE, ZARB e IPTU estão cadastrados
-- DUAS vezes (agosto e setembro), com o mesmo valor.
--
-- POR QUE DUAS COLUNAS, E NÃO SÓ O BOOLEANO QUE JÁ EXISTIA. Porque as 33 contas
-- a pagar abertas da Prime não repetem todas do mesmo jeito:
--
--   quinzenal              12   as quinzenas do time (BETO, JAQUELINE, INSIGHT…)
--   mensal, valor fixo     11   ZARB, banco, energia solar, contabilidade…
--   mensal, valor que muda  6   água, luz, cartão, DAS, FGTS, INSS
--   parcelado               2   IPTU 3/6 e 4/6 — repete, mas ACABA na 6/6
--   avulsa                  2   diária do pedreiro, comissão
--
-- Um booleano "mensal" deixaria de fora o MAIOR bloco (as 12 quinzenas), e é por
-- isso que `periodicidade` entra. E `valor_variavel` entra porque quatro dessas
-- contas estão cadastradas com **R$ 0,01**: é o marcador que o dono digita
-- enquanto o boleto da água não chega. Sem esta coluna, a repetição espalharia
-- aquele centavo por todos os meses seguintes — a conta apareceria "paga" por um
-- valor que nunca foi o dela.
--
-- `recorrente` CONTINUA sendo o interruptor, e não vira coluna gerada: ele é lido
-- em outros lugares (o MRR do cockpit filtra `where recorrente`), e trocar o tipo
-- de uma coluna viva por uma gerada exigiria dropar e recriar — o que esta base
-- não faz com dado de cliente. Quem garante que os dois campos não divergem é a
-- constraint abaixo (`periodicidade` só existe em título recorrente) mais o
-- `criar_titulo`/`definir_recorrencia`, que gravam os dois no mesmo update.
--
-- O CHECK DO VALOR AFROUXA, MAS SÓ ONDE PRECISA. `valor_centavos > 0` vira
-- "> 0, ou zero num título de valor variável". A garantia continua valendo pra
-- todo o resto da base: título comum com valor zero segue impossível.
--
-- Aditiva e idempotente. Nenhuma linha existente muda de comportamento: sem
-- `periodicidade` a repetição continua mensal, que era a única regra que havia.

alter table public.titulos
    add column if not exists periodicidade  text,
    add column if not exists valor_variavel boolean not null default false;

-- Os três ritmos. Quinzenal é 15 dias corridos (não "duas vezes por mês"):
-- é como a folha da Prime anda, 15/09 → 30/09 → 15/10.
do $$
begin
    alter table public.titulos
        add constraint titulos_periodicidade_ck
        check (periodicidade is null
               or periodicidade in ('quinzenal','mensal','anual'));
exception when duplicate_object then null;
end $$;

-- Uma verdade só: ritmo sem interruptor não existe.
do $$
begin
    alter table public.titulos
        add constraint titulos_periodicidade_exige_recorrente_ck
        check (periodicidade is null or recorrente);
exception when duplicate_object then null;
end $$;

-- Quem já era recorrente era mensal — era a única regra que o código tinha.
-- Roda ANTES da constraint do valor porque não depende dela, e depois das duas
-- de cima porque preenche justamente o que elas checam.
update public.titulos
   set periodicidade = 'mensal'
 where recorrente and periodicidade is null;

-- O valor: zero passa a ser legítimo, e SÓ, em título de valor variável.
-- O nome antigo é o que o Postgres deu ao check embutido na coluna (053).
alter table public.titulos drop constraint if exists titulos_valor_centavos_check;
do $$
begin
    alter table public.titulos
        add constraint titulos_valor_ck
        check (valor_centavos > 0
               or (valor_variavel and valor_centavos = 0));
exception when duplicate_object then null;
end $$;

comment on column public.titulos.periodicidade is
  'De quanto em quanto tempo a conta repete: quinzenal | mensal | anual. Só faz '
  'sentido com recorrente=true; nulo em título recorrente antigo lê-se mensal.';
comment on column public.titulos.valor_variavel is
  'A conta repete a DATA, não o valor (água, luz, cartão, impostos). A próxima '
  'nasce com valor zero, esperando o boleto — ver a migração 196.';
