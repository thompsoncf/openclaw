-- 495_clinica_recebimentos.sql
-- CRM da clínica, entrega 2a (docs/mockups/clinica_crm_telas.html, seção 04, aprovado em
-- 01/10/2026; decisão B do dono em 02/10/2026). finance/clinica_recebimentos.py.
--
-- O PAGAMENTO DO ATENDIMENTO NA AGENDA. Cada linha da agenda do dia diz se o atendimento
-- está pago ou a receber, e o botão Receber lança a receita no Financeiro (a mesma do
-- balcão) ou, em "fica a receber", um título a receber do cliente. A consulta se recebe a
-- qualquer hora do dia: do Chegou até depois do Finalizar. Sem sinal por Pix por enquanto.
--
-- Um recebimento por atendimento (unique evento_id): é a trava do clique duplo. A sessão
-- do pacote, a sessão inclusa da assinatura e o atendimento sem preço não passam por aqui.
--
-- Número 495 com folga: no dia 02/10 várias sessões mesclaram migrações em sequência
-- (478 a 481) e a entrega 1d teve de ser renumerada duas vezes.
--
-- Aditiva e idempotente; tabela nova, nenhuma linha existente muda.

create table if not exists public.clinica_recebimentos (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  evento_id bigint not null unique,
  prospeccao_id bigint references public.prospeccao(id) on delete set null,
  valor_centavos bigint not null check (valor_centavos > 0),
  forma text not null check (forma in ('pix','credito','debito','especie','fiado')),
  lancamento_id bigint,                    -- a receita no livro-caixa (forma paga)
  titulo_id bigint,                        -- o título a receber (forma 'fiado')
  criado_por bigint,
  criado_em timestamptz not null default now());
create index if not exists idx_clinica_recebimentos_conta on public.clinica_recebimentos (conta_id, criado_em);

-- rollback:
--   drop table if exists public.clinica_recebimentos;
