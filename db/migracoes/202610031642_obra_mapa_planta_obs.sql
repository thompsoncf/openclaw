-- 202610031642_obra_mapa_planta_obs.sql
-- O QUE FAZ: o "O que tem no arquivo" da planta da área (a obs de quem anexou)
--   e o nome do arquivo que subiu (com a página, quando é PDF).
-- POR QUÊ: pedido do dono em 03/10/2026: importar a planta, o croqui ou o
--   desenho direto no Mapa das obras, e — quando o arquivo não deixa claro o que
--   tem (quais quadras, quais casas são nossas, o que é vago ou de terceiro) —
--   um campo pra dizer, que quem risca os lotes lê no editor.
--
--   obra_mapas.planta_obs    o que tem no arquivo, nas palavras de quem anexou
--   obra_mapas.planta_nome   o nome do arquivo ("projeto.pdf · página 3")
--
-- Aditiva e idempotente (if not exists). Banco sem o mapa (sem a 482): pula.

do $$
begin
  if to_regclass('public.obra_mapas') is null then
    raise notice 'obs da planta: sem obra_mapas (482) — nada a fazer';
    return;
  end if;
  alter table public.obra_mapas add column if not exists planta_obs text not null default '';
  alter table public.obra_mapas add column if not exists planta_nome text not null default '';
end $$;

-- rollback:
--   alter table public.obra_mapas drop column if exists planta_nome;
--   alter table public.obra_mapas drop column if exists planta_obs;
