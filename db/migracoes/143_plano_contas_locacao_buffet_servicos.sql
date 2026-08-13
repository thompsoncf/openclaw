-- 143_plano_contas_locacao_buffet_servicos.sql
-- Amplia o plano de contas GLOBAL (o mesmo pra todas as empresas):
--   • 3 receitas de eventos/buffet  → 1.2.04, 1.2.05, 1.2.06
--   • 3 despesas de operação        → 5.1.09, 5.1.10, 5.1.11
--   • separa limpeza de manutenção  → 5.1.05 passa a ser só 'Material de
--     Limpeza' (a manutenção ganha a conta própria 5.1.11).
--
-- Aditivo e idempotente. NÃO move nenhum lançamento: a classificação aponta
-- pra conta por plano_conta_id (FK), então renomear não desclassifica nada e
-- o total do grupo 5 na DRE não muda (as duas contas vivem no mesmo grupo).
-- Se alguma empresa tiver lançado manutenção na 5.1.05, o remanejo é feito
-- pelo dono dela na tela (filtro + "aplicar a todos iguais") — nunca daqui.

-- ── 1) contas novas (o liga/desliga por empresa é default-on) ────────────────
insert into plano_contas (codigo, nome, grupo, natureza, ordem) values
    -- 1 · RECEITA OPERACIONAL BRUTA
    ('1.2.04', 'Receita de Locação de Espaço',                1, 'receita', 32),
    ('1.2.05', 'Receita de Buffet',                           1, 'receita', 33),
    ('1.2.06', 'Receita de Locação de Móveis e Utensílios',   1, 'receita', 34),
    -- 5 · DESPESAS OPERACIONAIS
    ('5.1.09', 'Diaristas',                                   5, 'despesa', 35),
    ('5.1.10', 'Serviços Terceirizados',                      5, 'despesa', 36),
    ('5.1.11', 'Manutenção e Conservação',                    5, 'despesa', 37)
on conflict (codigo) do nothing;

-- ── 2) 5.1.05 deixa de acumular manutenção (agora é a 5.1.11) ───────────────
-- Guardado pelo nome antigo: se já foi renomeada, não mexe (idempotente).
update plano_contas
   set nome = 'Material de Limpeza'
 where codigo = '5.1.05' and nome = 'Manutenção e Limpeza';

-- ── 3) ordem = sequência natural do código (auto-ajustável) ─────────────────
-- As 31 contas antigas já estavam nessa ordem, então aqui isso é no-op nelas e
-- só encaixa as novas no lugar certo. Vale pra qualquer adição futura também.
with seq as (
    select id, row_number() over (order by codigo) as n from plano_contas
)
update plano_contas p
   set ordem = seq.n::smallint
  from seq
 where seq.id = p.id and p.ordem <> seq.n;

-- rollback (manual):
--   delete from plano_contas where codigo in
--     ('1.2.04','1.2.05','1.2.06','5.1.09','5.1.10','5.1.11');
--   update plano_contas set nome = 'Manutenção e Limpeza'
--    where codigo = '5.1.05' and nome = 'Material de Limpeza';
--   (a ordem volta sozinha no próximo recálculo)
