-- 209_raio_x_dono.sql
-- O Raio-X do dono (Peça 3 do mockup docs/mockups/raio_x_como_fica.html):
-- a tela /painel/raio-x lê o que já existe. O que ela precisa que ainda não
-- existia são duas respostas que hoje ficam na cabeça do vendedor:
--
--   prospeccao.perda_motivo    por que o lead foi perdido, numa lista FIXA
--                              (um toque, não texto livre). Alimenta o bloco
--                              "Por que perdeu" e, adiante, a lista de espera
--                              por data (data_indisponivel) e a tabela de
--                              sábado (achou_caro). Hoje 6 de 13 perdidos da
--                              Prime estão sem motivo, e o resto está solto no
--                              texto da timeline.
--
--   prospeccao.origem_cliente  de onde o cliente veio, na palavra do próprio
--                              cliente (whatsapp, indicação, instagram, manual,
--                              outro). É diferente de `origem`, que é o CANAL por
--                              onde o lead entrou no sistema (279 dos 283 da Prime
--                              são 'whatsapp_inbound' — não diz nada de onde a
--                              pessoa ouviu falar da empresa).
--
-- A lista fixa fica no check pra ninguém gravar variação ('Achou caro', 'caro',
-- 'preço'); o rótulo pra tela mora em finance/raio_x_dono.MOTIVOS_PERDA.
-- Aditiva e idempotente: colunas com add-if-not-exists, check recriado.

alter table public.prospeccao add column if not exists perda_motivo   text;
alter table public.prospeccao add column if not exists origem_cliente text;

alter table public.prospeccao drop constraint if exists prospeccao_perda_motivo_check;
alter table public.prospeccao add constraint prospeccao_perda_motivo_check
    check (perda_motivo is null or perda_motivo in
           ('sumiu_apos_proposta', 'data_indisponivel', 'achou_caro',
            'fora_do_escopo', 'sem_interesse', 'outro'));

alter table public.prospeccao drop constraint if exists prospeccao_origem_cliente_check;
alter table public.prospeccao add constraint prospeccao_origem_cliente_check
    check (origem_cliente is null or origem_cliente in
           ('whatsapp', 'indicacao', 'instagram', 'manual', 'outro'));

-- rollback:
--   alter table public.prospeccao drop constraint if exists prospeccao_perda_motivo_check;
--   alter table public.prospeccao drop constraint if exists prospeccao_origem_cliente_check;
--   alter table public.prospeccao drop column if exists perda_motivo;
--   alter table public.prospeccao drop column if exists origem_cliente;
