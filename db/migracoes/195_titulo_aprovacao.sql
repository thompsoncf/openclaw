-- 195_titulo_aprovacao.sql
-- A liberação do dono numa conta a pagar — a terceira pergunta, que faltava.
--
-- POR QUE UMA COLUNA NOVA, E NÃO MAIS UM VALOR EM `status`. O pedido do dono
-- (03/09/2026) veio como uma lista só: "PAGO / PENDENTE / ATENÇÃO ATRASADO /
-- AUTORIZADO A PAGAR". São três perguntas diferentes, e um título tem resposta
-- pras três ao mesmo tempo:
--
--   o dinheiro saiu?   aberto · pago · cancelado     -> `status`, já existe
--   está no prazo?     no prazo · atrasado           -> conta de `vencimento`
--   o dono liberou?    aguardando · autorizado · recusado  -> É ESTA COLUNA
--
-- Num campo só, a combinação mais urgente do financeiro não teria como ser
-- escrita: conta AUTORIZADA que VENCEU sem ninguém pagar. Seria preciso escolher
-- entre chamá-la de "autorizado" ou de "atrasado", e some justamente a metade que
-- faria alguém agir.
--
-- NASCE 'autorizado', E ISSO É O PONTO. A Prime tem 30 títulos a pagar em aberto
-- (R$ 28.090,42) lançados antes de existir aprovação. Se a coluna nascesse
-- 'aguardando', ligar o controle transformaria 30 contas pagas-e-combinadas em
-- pendência do dono no primeiro carregamento da tela — e o primeiro efeito de um
-- controle novo não pode ser um mutirão. Quem decide o que é 'aguardando' é o
-- `criar_titulo` daqui pra frente, explicitamente.
--
-- O default também é a rede de segurança do caminho contrário: qualquer código
-- que insira título sem saber desta coluna continua produzindo título liberado,
-- nunca título preso. Falha para o lado de deixar passar, que é a decisão do dono
-- pra esta funcionalidade inteira (ver `pago_sem_autorizacao`).
--
-- PAGO_SEM_AUTORIZACAO. O dono escolheu "só avisa, não trava": dar baixa num
-- título não liberado continua funcionando, com aviso. Sem esta marca a escolha
-- viraria nada — o aviso seria um clique a mais e ninguém saberia depois o que
-- passou por fora. Com ela, a permissividade fica auditável.
--
-- Aditiva e idempotente. Nenhuma linha existente muda de comportamento.
alter table public.titulos
    add column if not exists aprovacao text not null default 'autorizado',
    add column if not exists aprovado_por bigint references public.membros(id) on delete set null,
    add column if not exists aprovado_em timestamptz,
    add column if not exists aprovacao_motivo text,
    add column if not exists pago_sem_autorizacao boolean not null default false;

-- O check entra separado e tolerante: `add column ... check` não é idempotente
-- entre replays, e a constraint nomeada com `not valid` + `validate` seria uma
-- cerimônia sem ganho aqui (a tabela nasce coerente pelo default).
do $$
begin
    alter table public.titulos
        add constraint titulos_aprovacao_ck
        check (aprovacao in ('aguardando','autorizado','recusado'));
exception when duplicate_object then null;
end $$;

-- A fila do dono: "o que está esperando minha liberação", por conta.
create index if not exists idx_titulos_aprovacao
    on public.titulos (conta_id, aprovacao) where aprovacao = 'aguardando';

comment on column public.titulos.aprovacao is
  'Liberação do dono: aguardando | autorizado | recusado. Independente de status '
  '(dinheiro) e de vencimento (prazo) — ver o cabeçalho da migração 195.';
comment on column public.titulos.pago_sem_autorizacao is
  'Baixa dada sem liberação do dono. A aprovação avisa mas não trava (decisão do '
  'dono em 03/09/2026); esta marca é o que torna a escolha auditável.';
