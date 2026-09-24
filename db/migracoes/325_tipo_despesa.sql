-- 325_tipo_despesa.sql
-- TIPO DE DESPESA — fixa, eventual, investimento — SEPARADO do centro de custo.
--
-- A CORREÇÃO. Pedido 2 do dono (23/09/2026): "um card com 3 tipos de despesas —
-- fixa, eventual, investimento". A entrega 4 (#820) leu isso como centro de
-- custo, porque a Prime tinha cadastrado DESPESA FIXA, DESPESA EVENTUAL e
-- INVESTIMENTO na lista de centros. O dono corrigiu em 24/09/2026:
--
--   "fixa, eventual, investimento não é centro de custo, tem que ser separado
--    para ter maior clareza no relatório do gestor"
--
-- Medido na Prime no mesmo dia: das 129 despesas da empresa, 51 estavam com um
-- desses três "centros" e SÓ 5 com um centro de área de verdade (Despesa com
-- Evento) — nenhuma com Buffet, Locação Espaço, Suítes ou Mobiliário. Um campo só
-- fazia a pessoa escolher entre dizer o TIPO e dizer a ÁREA. E o tipo não sai do
-- plano de contas: "Diaristas" aparece como fixa, eventual e investimento.
--
-- Então toda despesa passa a ter três respostas independentes:
--   plano de contas -> o que é o gasto;
--   centro de custo -> de qual área;
--   tipo_despesa    -> fixa | eventual | investimento (esta coluna).
--
-- A CÓPIA (opção A, escolhida pelo dono em 24/09/2026): quem já estava num dos
-- três centros ganha o tipo correspondente. O CENTRO NÃO MUDA — nada é apagado
-- nem trocado ("não mexer em centro de custos"); desativar aqueles três centros,
-- se um dia quiser, é decisão dele, na tela de centros. Só preenche o vazio, então
-- rodar de novo não muda nada.
--
-- Aditiva e idempotente.

alter table public.lancamentos add column if not exists tipo_despesa text;
alter table public.titulos     add column if not exists tipo_despesa text;

do $$
begin
    alter table public.lancamentos add constraint lancamentos_tipo_despesa_ck
        check (tipo_despesa is null or tipo_despesa in ('fixa', 'eventual', 'investimento'));
exception when duplicate_object then null;
end $$;

do $$
begin
    alter table public.titulos add constraint titulos_tipo_despesa_ck
        check (tipo_despesa is null or tipo_despesa in ('fixa', 'eventual', 'investimento'));
exception when duplicate_object then null;
end $$;

-- a cópia: pelo NOME do centro, na mesma conta, só onde o tipo está vazio
update public.lancamentos l
   set tipo_despesa = case upper(btrim(cc.nome))
                        when 'DESPESA FIXA'     then 'fixa'
                        when 'DESPESAS FIXAS'   then 'fixa'
                        when 'DESPESA EVENTUAL' then 'eventual'
                        when 'DESPESAS EVENTUAIS' then 'eventual'
                        when 'INVESTIMENTO'     then 'investimento'
                        when 'INVESTIMENTOS'    then 'investimento'
                      end
  from public.centros_custo cc
 where cc.id = l.centro_custo_id and cc.conta_id = l.conta_id
   and l.tipo = 'despesa'          -- tipo de despesa é de dinheiro que SAI
   and l.tipo_despesa is null
   and upper(btrim(cc.nome)) in ('DESPESA FIXA', 'DESPESAS FIXAS', 'DESPESA EVENTUAL',
                                 'DESPESAS EVENTUAIS', 'INVESTIMENTO', 'INVESTIMENTOS');

update public.titulos t
   set tipo_despesa = case upper(btrim(cc.nome))
                        when 'DESPESA FIXA'     then 'fixa'
                        when 'DESPESAS FIXAS'   then 'fixa'
                        when 'DESPESA EVENTUAL' then 'eventual'
                        when 'DESPESAS EVENTUAIS' then 'eventual'
                        when 'INVESTIMENTO'     then 'investimento'
                        when 'INVESTIMENTOS'    then 'investimento'
                      end
  from public.centros_custo cc
 where cc.id = t.centro_custo_id and cc.conta_id = t.conta_id
   and t.tipo = 'pagar'
   and t.tipo_despesa is null
   and upper(btrim(cc.nome)) in ('DESPESA FIXA', 'DESPESAS FIXAS', 'DESPESA EVENTUAL',
                                 'DESPESAS EVENTUAIS', 'INVESTIMENTO', 'INVESTIMENTOS');

comment on column public.lancamentos.tipo_despesa is
  'fixa | eventual | investimento — o TIPO do gasto, separado do centro de custo '
  '(a área) e do plano de contas (o que é). Ver a migração 325.';
comment on column public.titulos.tipo_despesa is
  'fixa | eventual | investimento — vai pro lançamento na baixa. Ver a migração 325.';

-- rollback:
--   alter table public.lancamentos drop column if exists tipo_despesa;
--   alter table public.titulos     drop column if exists tipo_despesa;
