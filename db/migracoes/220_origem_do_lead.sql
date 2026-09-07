-- De qual anúncio veio o lead.
--
-- A Prime atende num número só, por QR: anúncio, orgânico, bio do Instagram e
-- indicação caem todos no mesmo WhatsApp. Medido em 07/09/2026, `prospeccao.origem`
-- tinha quatro valores grossos na base inteira ('whatsapp_inbound' 420,
-- 'google_places' 314, 'manual' 6, 'proposta' 1) e `chip_id` estava nulo em 383 das
-- 429 conversas da conta 34 — ou seja, não havia como dizer qual lead veio de
-- campanha paga. A agência media até "conversas iniciadas" e o resto era digitado à
-- mão numa planilha, uma vez por mês.
--
-- O código chega no texto da primeira mensagem (ver finance/origem_anuncio.py) e é
-- carimbado aqui. NÃO é uma chave estrangeira pra tabela de campanhas, de propósito:
-- o Zaq não guarda quais códigos existem, senão todo criativo novo nasceria sem
-- origem até alguém cadastrar — falha silenciosa, e a culpa cairia no sistema.
--
-- PRIMEIRO TOQUE, E SÓ ELE. Se a mesma pessoa voltar meses depois por outro anúncio,
-- este campo não muda: ela já era lead de quem a trouxe, e dois criativos
-- reivindicando a mesma venda fariam o total do painel ficar maior que a realidade.
-- O retorno é registrado em `prospeccao_atividades`, que já é o histórico do lead.
alter table public.prospeccao
  add column if not exists origem_codigo text;

-- O índice é parcial porque a coluna é rala: só lead que veio com código a preenche,
-- e o painel sempre filtra por "não nulo" dentro de uma conta.
create index if not exists idx_prospeccao_origem_codigo
    on public.prospeccao (conta_id, origem_codigo)
 where origem_codigo is not null;
