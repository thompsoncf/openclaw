-- 150_funcionario_salario_vigencia.sql
-- Salário com VIGÊNCIA: cada valor passa a ter uma data de início, e a folha de
-- cada mês usa o valor que valia naquela competência.
--
-- POR QUE. Até aqui `funcionarios.salario_centavos` guardava UM valor só. Dar um
-- aumento sobrescrevia o número — e como o holerite é montado a partir da folha
-- (finance/empresa.py: holerite_funcionario -> folha_do_mes), reimprimir o
-- recibo de um mês passado saía com o salário NOVO. Um valor que a pessoa não
-- recebeu naquele mês, num documento que ela guarda, sem nenhum aviso de que
-- estava errado. Era o motivo de não existir edição de salário na tela: o jeito
-- de "dar aumento" era cadastrar outro funcionário.
--
-- O campo antigo CONTINUA existindo e valendo como "salário corrente" (é o que o
-- formulário mostra e o que criar_funcionario grava). Esta tabela é a linha do
-- tempo; quem precisa do valor de uma competência pergunta aqui.
create table if not exists funcionario_salarios (
  id bigserial primary key,
  conta_id bigint not null,
  funcionario_id bigint not null references funcionarios(id) on delete cascade,
  salario_centavos int not null,
  vigencia_de date not null,
  criado_em timestamptz default now(),
  unique (funcionario_id, vigencia_de)
);

create index if not exists idx_func_sal_busca
  on funcionario_salarios (funcionario_id, vigencia_de desc);

-- BACKFILL — a parte que não pode faltar. Sem uma linha inicial por funcionário,
-- todo mundo que já existe ficaria sem salário na folha assim que a resolução por
-- competência entrasse no ar. A data é a admissão (ou 1900 pra quem nunca teve
-- admissão preenchida), pra cobrir qualquer competência que alguém for consultar.
-- `on conflict do nothing` mantém a migração idempotente, igual às outras.
insert into funcionario_salarios (conta_id, funcionario_id, salario_centavos, vigencia_de)
select conta_id, id, coalesce(salario_centavos, 0),
       coalesce(admitido_em, date '1900-01-01')
  from funcionarios
on conflict (funcionario_id, vigencia_de) do nothing;
