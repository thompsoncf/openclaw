-- 202610031642_novidade_mapa_importar_planta.sql
-- O QUE FAZ: o aviso do "importar a planta, o croqui ou o desenho" no Mapa das
--   obras (web/painel_obras_mapa.py), pela seção 5 do CLAUDE.md. Portão
--   `construcao` (350); dono e gestor (199).
-- POR QUÊ: pedido do dono em 03/10/2026.
--
-- Aditiva e idempotente (on conflict do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-mapa-importar-planta', 'novidade', 'construcao', '{dono,gestor}',
 'Mapa das obras: importe a planta, o croqui ou o desenho',
 'O arquivo da área entra direto no Mapa das obras — PDF, foto ou croqui à mão —, com o passo a passo pra o mapa ficar bem feito e um campo pra dizer o que tem no arquivo.',
 '/painel/obras/mapa',
 $txt$O mapa das obras começa pelo desenho da área: a planta do loteamento, o croqui da quadra, o projeto do engenheiro.

IMPORTAR

Em Obras › Mapa das obras, toque em "📎 Importar planta". Serve PDF, JPG, PNG ou WEBP, até 10 MB. Projeto com várias páginas? Diga qual é a página da planta geral. Ao criar uma área nova, o arquivo já vai junto.

PRA FICAR BEM FEITO

Ao anexar, o Zaq mostra o passo a passo: a planta de implantação em PDF é o melhor; foto, tirada de cima e reta; croqui, com a rua, as quadras e os lotes numerados; DWG do AutoCAD, exportado em PDF antes.

O QUE TEM NO ARQUIVO

Se o desenho não deixa claro, escreva: quais quadras e lotes aparecem, quais casas são suas, o que é vago ou de terceiro, onde fica a entrada. Quem for riscar os lotes lê isso no editor.$txt$,
 timestamptz '2026-10-04 02:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-mapa-importar-planta';
