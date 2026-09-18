-- 287_apolice_perdida.sql
-- A renovação PERDIDA, com motivo — e a ficha do segurado na janela.
--
-- O PEDIDO (dono, 18/09/2026), aprovando o mockup clicável
-- docs/mockups/ficha_segurado_janela.html: "aprovado, pode fazer com conversa e
-- perdi com motivo".
--
-- POR QUE UMA SITUAÇÃO NOVA E NÃO 'vencida' OU 'cancelada'. 'vencida' é o tempo
-- passando sem ninguém marcar nada (é o que `marcar_vencidas` grava sozinho);
-- 'cancelada' é a apólice encerrada no meio da vigência. Nenhuma das duas diz o
-- que a corretora precisa saber: "o cliente NÃO renovou comigo, e foi por isto".
-- Sem esse estado, o Raio-X de "por que perdemos renovação" não tem de onde
-- contar — o número existiria e não ensinaria nada.
--
-- O MOTIVO VEM DA MESMA LISTA DO FUNIL (`funil_motivos_perda`, semeada por
-- perfil — no `seguros`: renovou direto, achou caro, cobertura não atendeu…).
-- Uma segunda lista de motivos seria uma segunda verdade sobre a mesma perda.
--
-- Aditivo e idempotente. `perdida` NÃO entra em `apolices.VIVAS`: sai da fila e
-- do alerta, como renovada e cancelada.

alter table public.apolices drop constraint if exists apolices_situacao_check;
alter table public.apolices add constraint apolices_situacao_check
    check (situacao in ('proposta','vigente','renovada','vencida','cancelada','perdida'));

alter table public.apolices add column if not exists perda_motivo    text;
alter table public.apolices add column if not exists perda_descricao text;
alter table public.apolices add column if not exists perdida_em      timestamptz;

-- rollback:
--   alter table public.apolices drop column if exists perdida_em;
--   alter table public.apolices drop column if exists perda_descricao;
--   alter table public.apolices drop column if exists perda_motivo;
--   alter table public.apolices drop constraint if exists apolices_situacao_check;
--   alter table public.apolices add constraint apolices_situacao_check
--       check (situacao in ('proposta','vigente','renovada','vencida','cancelada'));
