-- 256_novidade_nome_no_raio_x.sql
-- Raio-X: o nome de cada linha passa a sair do orçamento e do cadastro, nunca de
-- um telefone digitado no lugar errado.
--
-- O QUE MUDOU NA TELA
-- Em todas as listas que o Raio-X abre ao clicar num número — "Rascunho",
-- "Parou na 1ª resposta", "Aprovado, esperando assinatura" e os contratos
-- assinados — o nome do cliente passa a vir do CADASTRO, depois de
-- `orcamentos.empresa`, depois de `orcamentos.cliente`, e só então dos campos do
-- lead. Telefone nunca vira nome; quando nada serve, sai "Orçamento nº N".
--
-- O QUE ESTAVA ERRADO, visto na produção em 14/09/2026 (conta 34)
-- Cada uma das cinco consultas resolvia o nome sozinha, em SQL, com
-- `coalesce(nullif(o.cliente,''), p.contato, p.empresa, 'cliente')`. Duas coisas
-- erradas no mesmo trecho: começava pelo campo em que menos se pode confiar
-- (`cliente` é o "contato", onde se digita qualquer coisa) e não tinha guarda
-- nenhuma contra telefone — guarda que o Python já tinha em `vendas._nome_util`
-- e que o funil já usava havia semanas.
--
-- As duas linhas do bloco "Aprovado, esperando assinatura" estavam erradas:
--   nº 8  · cliente='86998192489'  -> a tela mostrava O TELEFONE, com o nome
--           "Josiany Rayra Soares dos Santos" em `empresa` e no cadastro
--   nº 23 · cliente vazio          -> caía no apelido do lead ("Carolina Costa")
--           em vez de "Maria Carolina da Silva Costa", que é o nome do contrato
--
-- O CONSERTO: `vendas.nome_do_orcamento`, uma regra só, que `titulo_do_funil`
-- passa a usar também — funil e Raio-X não podem chamar a mesma pessoa por dois
-- nomes na mesma tela. O SQL devolve os campos crus; qual deles vale é decisão
-- de negócio e mora no Python, onde já está testada.
--
-- O PORTÃO: `servico`. O Raio-X por vendedor é do módulo Serviços — as listas que
-- mudaram são de orçamento e contrato. Não é por nicho: o nome do cliente sai dos
-- mesmos campos em evento e em mensalidade.
--
-- PRA QUEM: dono, gestor e VENDEDOR. O vendedor tem o Raio-X dele no app, com as
-- mesmas listas saindo da mesma função (`raio_x.sua_semana`) — ele vê a correção
-- na Fila e no Raio-X tanto quanto o dono vê no painel.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
-- `mudanca` e não `novidade`: o check da 174 só aceita esses dois, e dos dois é
-- `mudanca` que descreve isto — não é recurso novo, é a mesma tela passando a
-- mostrar o nome certo.
('raio-x-nome-do-cliente-correto', 'mudanca', 'servico', '{dono,gestor,vendedor}',
 'O Raio-X passou a mostrar o nome certo do cliente',
 'Nas listas do Raio-X, o nome do cliente às vezes saía como um telefone ou como um apelido do lead; agora vem do cadastro e do orçamento, na mesma ordem que o funil já usava.',
 '/painel/raio-x',
 $txt$Nas listas que abrem ao clicar num número do Raio-X — rascunhos, quem parou na primeira resposta, aprovados esperando assinatura e contratos assinados — o nome do cliente nem sempre era o nome do cliente.

O QUE ACONTECIA. A tela pegava primeiro o campo "contato" do orçamento. Esse campo é livre, e quando alguém digitava o telefone ali, era o telefone que aparecia na lista — mesmo com o nome completo salvo no cadastro e no próprio orçamento, dois campos ao lado. Quando o "contato" estava vazio, a tela caía no apelido do lead (o nome que veio do WhatsApp), que raramente é o nome completo.

Na prática: uma proposta aprovada aparecia como "86998192489", e outra como "Carolina Costa" em vez de "Maria Carolina da Silva Costa" — que é o nome que está no contrato que ela vai assinar.

O QUE MUDA. O nome agora vem do CADASTRO do cliente primeiro; não havendo, do nome do orçamento; depois do contato; e só então dos dados do lead. Telefone nunca vira nome — em nenhum dos degraus. Se mesmo assim não houver nada que sirva, a linha mostra "Orçamento nº N", que ao menos se acha na busca.

É A MESMA REGRA DO FUNIL, agora num lugar só. Antes o funil de Serviços e o Raio-X decidiam o nome cada um do seu jeito, e dava pra ver a mesma pessoa com dois nomes em duas telas.

NADA FOI ALTERADO NO SEU CADASTRO. A correção é só de leitura: os nomes sempre estiveram salvos: era a tela que escolhia mal qual mostrar.$txt$,
 timestamptz '2026-09-14 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'raio-x-nome-do-cliente-correto';
