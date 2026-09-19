-- 291_novidade_servicos_linha_mais_limpa.sql
-- Três ajustes de layout na linha do funil de Serviços (nicho de eventos).
--
-- É o acerto fino do que subiu na 290 poucas horas antes. Vai como aviso
-- próprio, e não como emenda na 290, porque a 290 já foi publicada (03:00 UTC de
-- 19/09) e migração aplicada não se reescreve. Vai como `mudanca` e não como
-- `novidade` pelo mesmo motivo: nada novo apareceu na tela — o que já estava lá
-- ficou legível.
--
-- O QUE MUDOU NA TELA:
--   * A ABA USA A LARGURA INTEIRA, como o Raio-X. Travava em 1120px, e com o
--     funil na frente sobrava faixa vazia à direita enquanto o texto da linha
--     quebrava em três.
--   * A DATA DA FESTA APARECE UMA VEZ SÓ. A linha abre com o bloco "04 SET 27" e
--     o subtítulo repetia "Casamento · 04/09/2027 · 150 convidados" a meio
--     centímetro dali. Ficou "Casamento · 150 convidados".
--   * QUANDO A PROPOSTA FOI CRIADA virou coluna própria, à direita, com a data
--     completa e o ano. Antes era "gerada 16/09/2026" enterrado entre o nº e o
--     nome do vendedor: o dado certo no lugar errado, porque data no meio de
--     frase não se compara com a da linha de baixo.
--
-- POR QUE O ANO FICA SEMPRE. Pedido do dono, em 19/09: o funil de eventos guarda
-- 2026, 2027 e 2028 ao mesmo tempo, e "16/09" sem ano é ambíguo justamente aqui.
--
-- PRA QUEM: dono, gestor e vendedor — os três leem esta lista todo dia.
--
-- O PORTÃO: `eventos`, o mesmo da 290. A tela do recorrente não foi medida nesta
-- mudança e continua exatamente como estava (seção 6 do CLAUDE.md).
--
-- CONTAS ALCANÇADAS: 34 (MANOEL SOARES) e 35 (Louana vanessa cardoso Santos
-- costa) — as duas de nicho `eventos` em 19/09/2026.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('servicos-linha-mais-limpa', 'mudanca', 'eventos', '{dono,gestor,vendedor}',
 'A linha do funil ficou mais fácil de ler',
 'A aba de Serviços passou a ocupar a tela inteira, a data da festa deixou de aparecer duas vezes na mesma linha e a data em que a proposta foi criada ganhou uma coluna própria, com o dia completo e o ano.',
 '/painel/servicos',
 $txt$Três acertos na lista de propostas, todos de leitura — nenhum valor e nenhum botão mudou de lugar.

A TELA INTEIRA

A aba parava numa faixa de tamanho fixo e sobrava espaço vazio à direita, enquanto o nome do cliente quebrava em três linhas. Agora ela ocupa a largura toda, como o Raio-X.

A DATA DA FESTA, UMA VEZ SÓ

Cada linha começa com o bloco do dia e do mês da festa. O texto ao lado repetia a mesma data por extenso: "Casamento · 04/09/2027 · 150 convidados", com o "04 SET 27" colado do lado. Ficou só "Casamento · 150 convidados" — o bloco já é a data.

QUANDO A PROPOSTA FOI CRIADA, EM COLUNA

Era "gerada 16/09/2026", no meio da frase entre o número e o nome do vendedor. Virou uma coluna à direita, com o dia completo e o ano.

Alinhadas uma embaixo da outra, as datas viram uma leitura só: dá pra ver a idade da carteira de cima pra baixo, coisa que a data no meio da frase não permitia.

O ano aparece sempre, de propósito: o funil tem proposta de 2026, 2027 e 2028 ao mesmo tempo, e "16/09" sozinho não diz de qual ano é.

No celular a coluna entra logo abaixo do nome, numa linha só, e os botões continuam no fim da linha.$txt$,
 timestamptz '2026-09-19 14:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'servicos-linha-mais-limpa';
