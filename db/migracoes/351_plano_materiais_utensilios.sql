-- 351_plano_materiais_utensilios.sql
-- 5.1.12 Materiais e Utensílios, em Despesas Operacionais.
--
-- Pedido do dono em 25/09/2026: "cria no plano de contas no grupo de Despesas
-- operacionais essa conta: MATERIAIS E UTENSÍLIOS". O nome entra em caixa de
-- título ("Materiais e Utensílios") porque é assim que a árvore inteira é escrita;
-- o plano não tem nenhuma conta em maiúsculas.
--
-- POR QUE ENTRA PRA TODO MUNDO
-- O plano de contas é GLOBAL (finance/plano_contas.py): uma árvore só, e cada
-- empresa liga ou desliga o que usa. Não existe conta de uma empresa só. Como a
-- ausência de linha em `plano_conta_habilitada` quer dizer habilitada, a 5.1.12
-- aparece ligada em toda empresa — mesmo caminho da 143 (Diaristas), da 186
-- (Aporte de Sócios) e da 336 (Fardamentos). Utensílio e material de operação é
-- gasto de qualquer negócio que atende alguém num lugar físico; quem não usa,
-- desliga no plano de contas (4 contas já estão desligadas em produção, então o
-- botão é usado de verdade).
--
-- A FRONTEIRA, QUE É O RISCO DESTA CONTA
-- Ela nasce cercada por três vizinhas, e sem uma regra clara o gasto se espalha
-- entre as quatro e nenhum relatório fecha:
--
--   3.1.03 Insumos e Materiais (CUSTO) — o que é consumido NO produto vendido, e
--          sai junto com ele: comida, embalagem de delivery, saco, pote.
--   5.1.04 Materiais de Escritório .... papel, caneta, tinta de impressora.
--   5.1.05 Material de Limpeza ........ detergente, pano, desinfetante.
--   5.1.12 Materiais e Utensílios ..... o que a casa USA e REUSA pra operar:
--          talher, prato, taça, bandeja, toalha de mesa, utensílio de cozinha,
--          instrumento, ferramenta pequena.
--
-- O corte é "sai com o produto" (custo) × "fica na casa" (despesa operacional).
-- Conferido em produção antes (só leitura): não existia conta com "utensí" no
-- nome. E há gasto dos dois tipos já lançado — a conta 34 (Prime) tem "Bandeja
-- Centro Mesa Decorativa" e "Toalhas para mesas", que são desta conta nova; a
-- conta 35 tem "Sacos delivery" e "Bandejas laminadas", que são embalagem e
-- continuam em 3.1.03. Por isso o aviso da 352 explica a fronteira em vez de só
-- anunciar o nome: a confusão aqui não é hipótese, já está nos lançamentos.
--
-- Nada é reclassificado por esta migração. Lançamento de cliente não se mexe
-- (regra 0); quem quiser mudar a classificação de um gasto antigo faz na tela.
--
-- Aditiva e idempotente. Nada existente muda de valor, só a `ordem`, que é
-- recalculada pelo código (mesma regra da 143 e da 336).

insert into public.plano_contas (codigo, nome, grupo, natureza, ordem) values
  ('5.1.12', 'Materiais e Utensílios', 5, 'despesa', 33)
on conflict (codigo) do nothing;

with seq as (
    select id, row_number() over (order by codigo) as n from public.plano_contas
)
update public.plano_contas p
   set ordem = seq.n::smallint
  from seq
 where seq.id = p.id and p.ordem <> seq.n;

-- rollback (manual):
--   delete from public.plano_contas where codigo = '5.1.12';
--   (antes, conferir que nenhum lançamento/título aponta pra ela; a ordem volta
--    sozinha no próximo recálculo)
