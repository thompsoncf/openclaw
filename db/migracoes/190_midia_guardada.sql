-- O arquivo que a empresa decidiu GUARDAR (passo 5 da mídia).
--
-- O PROBLEMA QUE ISTO RESOLVE
-- Os passos 1 a 4 nunca guardam arquivo: `mensagens.midia_ref` tem só o endereço
-- no CDN do WhatsApp e a chave, e quem abre a conversa busca lá na hora. Foi a
-- escolha certa e continua sendo — medido em 28/08/2026, guardar tudo daria ~110 GB
-- por ano numa conta só, contra 22 MB de ponteiro, e a maioria das fotos ninguém
-- abre duas vezes.
--
-- Só que o CDN do WhatsApp EXPIRA. A rota de mídia já trata o 404/410 e diz "não
-- está mais no servidor do WhatsApp" — e para uma foto de referência de decoração
-- isso é aceitável: pede de novo ao cliente. Para o COMPROVANTE DO SINAL e o
-- CONTRATO ASSINADO não é: são o registro do negócio, e o dia em que se precisa
-- deles é justamente o dia da discussão, meses depois.
--
-- POR QUE SELETIVO, E NÃO AUTOMÁTICO
-- Guardar tudo é a decisão que o dono já recusou uma vez, com razão. Quem sabe o
-- que vira registro do negócio é o vendedor, no momento em que recebe: ele olha o
-- comprovante e sabe que aquilo é o sinal do evento. Um botão que ele aperta custa
-- um toque e guarda o que importa; uma regra automática ou guarda demais (o custo
-- que se recusou) ou guarda de menos (e aí não dá pra confiar nela).
--
-- ONDE O ARQUIVO FICA
-- No mesmo bucket PRIVADO dos comprovantes de pagamento (`SUPABASE_BUCKET_DOCS`,
-- default `documentos`), pela mesma razão que aquele é privado: comprovante tem
-- nome, banco, valor e às vezes CPF. O banco guarda o CAMINHO, nunca uma URL — a
-- entrega é sempre por rota nossa, que confere sessão e conta antes de ler.
--
-- POR QUE COLUNAS EM `mensagens` E NÃO TABELA NOVA
-- A pergunta "esta mídia está guardada?" é feita ao desenhar CADA bolha de CADA
-- conversa. Numa tabela à parte isso vira join em toda leitura de thread — o
-- caminho mais quente do produto. Como é no máximo um arquivo por mensagem, as
-- três colunas dizem a mesma coisa sem custo: o caminho, quando, e quem mandou
-- guardar (que é o que se quer saber meses depois, na discussão).
--
-- Nulo = não guardado, que é o estado de todas as mensagens que existem. A rota de
-- mídia serve do bucket quando há caminho e cai no CDN quando não há — então esta
-- migração não muda o comportamento de nada que já está lá.
alter table mensagens add column if not exists midia_arquivo text;
alter table mensagens add column if not exists midia_guardada_em timestamptz;
alter table mensagens add column if not exists midia_guardada_por bigint;

comment on column mensagens.midia_arquivo is
  'caminho no bucket PRIVADO de quem foi guardado pelo botão (passo 5). '
  'Nulo = só o ponteiro do CDN. Nunca uma URL: a entrega é por rota nossa.';

-- Parcial porque é a minoria das linhas de propósito — o índice existe pra listar
-- "o que esta conta guardou" sem varrer a tabela de mensagens inteira.
create index if not exists idx_mensagens_guardada
  on mensagens (conversa_id, midia_guardada_em desc)
  where midia_arquivo is not null;
