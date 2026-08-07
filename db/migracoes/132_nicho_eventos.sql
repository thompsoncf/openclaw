-- 132_nicho_eventos.sql
-- Nicho 'eventos' (organização de festas/eventos, buffet, cerimonial): MISTO —
-- vende produto (decoração, aluguel de estrutura) e serviço (organização,
-- buffet, cerimonial). Casa o slug com finance/nichos.py e com o ramo Eventos
-- de finance/cnpj_info.py. contas.nicho_id é FK -> nichos, então precisa
-- existir na tabela. Idempotente e aditivo.

insert into nichos (nome, slug, tipo)
select 'Eventos / Festas', 'eventos', 'produto'   -- 'tipo' na tabela e' legado; a verdade e' o par de flags no codigo
where not exists (select 1 from nichos n where n.slug = 'eventos');

-- rollback:
--   delete from nichos where slug = 'eventos';
