-- 162_titulo_parcela_do_orcamento.sql
-- Amarra o título a receber à PARCELA que o gerou.
--
-- Fechar um orçamento de evento cria um título por parcela (vendas.fechar_orcamento).
-- Voltar do título pra parcela, porém, só dava pelo TEXTO da descrição
-- ("Evento — Fulano · Sinal — confirma a reserva da data") — e casar dinheiro por
-- string é frágil: o dono edita a observação da parcela, dois eventos do mesmo
-- cliente colidem, e a descrição do título é editável na tela.
--
-- Isso passou a doer com a pré-reserva por sinal (160/161): quando o dono confirma
-- "Sinal recebido", o título daquela parcela precisa receber baixa — e é preciso
-- saber QUAL título é, sem adivinhar.
--
-- `parcela_idx` é a posição na lista `orcamentos.parcelas` (0 = a primeira, que é
-- onde o gerador escreve o sinal). Nulo em tudo que não veio de parcela de
-- orçamento — que é a esmagadora maioria dos títulos.
--
-- Aditivo e idempotente.
alter table public.titulos add column if not exists orcamento_id bigint;
alter table public.titulos add column if not exists parcela_idx  int;

-- sem FK pro orçamento: título a receber é registro financeiro e não pode ser
-- impedido de existir (nem cair junto) por causa do documento comercial que o
-- originou. O vínculo é pra achar, não pra mandar.
create index if not exists idx_titulos_orcamento
    on public.titulos (orcamento_id, parcela_idx)
 where orcamento_id is not null;

-- rollback:
--   drop index if exists idx_titulos_orcamento;
--   alter table public.titulos drop column if exists orcamento_id;
--   alter table public.titulos drop column if exists parcela_idx;
