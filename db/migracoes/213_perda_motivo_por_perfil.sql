-- 213_perda_motivo_por_perfil.sql
-- O Raio-X por nicho (docs/mockups/raio_x_por_nicho.html, aprovado em
-- 05/09/2026): cada perfil oferece a sua lista de seis motivos de perda.
--
--   eventos      sumiu depois da proposta · DATA INDISPONÍVEL · achou caro ·
--                fora do escopo · sem interesse · outro
--   recorrente   sumiu depois da proposta · FICOU COM O FORNECEDOR ATUAL ·
--                achou caro · fora do escopo · sem interesse · outro
--
-- "Data indisponível" não existe pra quem vende mensalidade; o motivo que mais
-- perde lá é o cliente ficar com o fornecedor que já tem. O check passa a
-- aceitar a união das duas listas (finance/raio_x_perfil.MOTIVOS_TODOS); qual
-- lista cada tela oferece é o perfil que decide. Nenhuma linha muda.
--
-- Aditiva e idempotente (check recriado).

alter table public.prospeccao drop constraint if exists prospeccao_perda_motivo_check;
alter table public.prospeccao add constraint prospeccao_perda_motivo_check
    check (perda_motivo is null or perda_motivo in
           ('sumiu_apos_proposta', 'data_indisponivel', 'ficou_com_atual', 'achou_caro',
            'fora_do_escopo', 'sem_interesse', 'outro'));

-- rollback:
--   (voltar o check da 209, sem 'ficou_com_atual' — só se nenhuma linha o usar)
