-- 144_prospeccao_pessoa_fisica.sql
-- Lead pode ser PESSOA FÍSICA. A prospecção nasceu (migração 075) só com `cnpj` e
-- `empresa not null` — quem vende pra pessoa não tinha onde botar o cliente: punha o
-- nome dela no campo Empresa e deixava o documento vazio. Sem validação, sem
-- deduplicação e fora dos relatórios que separam PF de PJ.
--
-- Mesmo desenho da 131 (pessoas): um `tipo` pf/pj e o CPF como chave forte da conta,
-- espelhando `uq_prospeccao_conta_cnpj`. `empresa` continua sendo a coluna do nome
-- nos dois casos (em PF guarda o nome da pessoa) — só o rótulo na tela muda, então
-- funil, kanban, campanhas e relatórios seguem funcionando sem reescrita.
-- Idempotente e não-destrutivo: quem já existe é marcado 'pj', que é o que sempre foi.

alter table public.prospeccao add column if not exists tipo text;
alter table public.prospeccao add column if not exists cpf  text;

-- CPF é chave forte dentro da conta (funde na igualdade), igual o CNPJ. Guardado
-- só em dígitos — a máscara é aplicada na exibição.
create unique index if not exists uq_prospeccao_conta_cpf
  on public.prospeccao (conta_id, cpf) where cpf is not null;
create index if not exists ix_prospeccao_tipo
  on public.prospeccao (conta_id, tipo) where tipo is not null;

-- backfill: quem já tem CPF vira PF; todo o resto é PJ (era o único tipo possível).
update public.prospeccao set tipo = case when cpf is not null then 'pf' else 'pj' end
 where tipo is null;

alter table public.prospeccao alter column tipo set default 'pj';

-- rollback (manual):
--   drop index if exists uq_prospeccao_conta_cpf;
--   drop index if exists ix_prospeccao_tipo;
--   alter table public.prospeccao drop column if exists cpf;
--   alter table public.prospeccao drop column if exists tipo;
