-- 286_apolices_pdf.sql
-- A apólice em PDF, guardada junto do cadastro — e o registro do que o leitor leu.
--
-- O PEDIDO (dono, 18/09/2026): "o ideal seria importar também a apólice em pdf e
-- jogar aqui e já colher os dados". Mockup em
-- docs/mockups/renovacoes_layout_e_import_pdf.html; decisões dele: guardar o PDF
-- junto (sim), "Renovada" NÃO fabrica a apólice do ano seguinte (quem emite é a
-- seguradora — a nova entra como documento importado), leitor para todas as
-- seguradoras à medida que os PDFs chegarem.
--
-- POR QUE GUARDAR O PDF. A medição do mockup: os 25 campos ROTULADOS do documento
-- saem perfeitos (9/9 conferidos), mas a TABELA de coberturas não se extrai —
-- três abordagens, três somas erradas, todas com cara de número. Então as
-- coberturas não vêm pro banco; ficam no PDF, que fica junto. É a mesma decisão
-- de `midia_cofre`: o registro do negócio se guarda, o resto se aponta.
--
-- `pdf_lido` é o que o leitor EXTRAIU, campo a campo, com as checagens. Existe pra
-- auditoria: quando um alerta não disparar, a pergunta é "o que foi lido do
-- papel?" — e a resposta tem que estar gravada, não reconstruída.
--
-- O caminho vai no MESMO bucket privado dos comprovantes (`comprovantes.subir_em`),
-- pela mesma razão daquele ser privado: apólice tem nome, CPF, endereço e placa.
-- O banco guarda o caminho, nunca URL; quem entrega é a rota, que confere conta.
--
-- Aditivo e idempotente.

alter table public.apolices add column if not exists pdf_caminho text;
alter table public.apolices add column if not exists pdf_nome    text;
alter table public.apolices add column if not exists pdf_bytes   integer;
alter table public.apolices add column if not exists pdf_lido    jsonb;
alter table public.apolices add column if not exists pdf_lido_em timestamptz;

-- A MESMA PROPOSTA NÃO ENTRA DUAS VEZES. O índice da 278 protege pelo nº da
-- APÓLICE — e uma proposta ainda não tem esse número (é NULL, e NULL não colide).
-- Com o import por PDF isso vira buraco de verdade: reimportar o mesmo documento
-- cadastraria a mesma proposta de novo, dobrando prêmio e alerta. A Maria de
-- Fátima (apólice 1 da conta 37) é exatamente esse caso hoje.
create unique index if not exists ux_apolices_proposta
    on public.apolices (conta_id, lower(seguradora), numero_proposta)
    where numero_proposta is not null and numero_proposta <> '';

-- rollback:
--   drop index if exists public.ux_apolices_proposta;
--   alter table public.apolices drop column if exists pdf_lido_em;
--   alter table public.apolices drop column if exists pdf_lido;
--   alter table public.apolices drop column if exists pdf_bytes;
--   alter table public.apolices drop column if exists pdf_nome;
--   alter table public.apolices drop column if exists pdf_caminho;
