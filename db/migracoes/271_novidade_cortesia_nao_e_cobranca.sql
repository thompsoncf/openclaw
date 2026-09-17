-- 271_novidade_cortesia_nao_e_cobranca.sql
-- "Ok, obrigada" deixa de contar como cliente esperando resposta.
--
-- O QUE MUDOU NA TELA (/painel/prospeccao e /painel/follow-up)
-- 1) No funil, o grupo "🟢 Esperando resposta" para de incluir quem só agradeceu.
--    O lead não some: desce pro grupo de baixo, que é a verdade — estamos
--    esperando ELE.
-- 2) O selo do follow-up para de marcar esses leads como 🚨 Crítico / 🔴 Atrasado
--    por causa de uma cortesia. Eles voltam pra régua normal (a da proposta ou a
--    dos toques).
--
-- A MEDIÇÃO QUE MOTIVOU, conta 34, 16/09/2026. O dono olhou o quadro e disse:
-- "o cliente dá ok obrigado sobre uma resposta — vale analisar todas as conversas
-- pra ver o sentido de tá crítico ou não". Li as 57 conversas uma a uma:
--
--     30 de 57 (53%)   última mensagem era cortesia: "Ok", "Obrigada", "Tá certo"
--      3 de 57         tinham ponto de interrogação
--
-- E os dois primeiros da coluna Proposta — os que o quadro punha no topo por causa
-- da festa próxima — eram "Obrigada!" (Renata Costa, festa em 11 dias) e "Bgd".
--
-- POR QUE É SEGURO REBAIXAR, medido antes de escrever: dos 189 encerramentos de
-- cortesia da história da conta, em 161 (85%) O CLIENTE VOLTOU A FALAR SOZINHO.
-- "Ok, obrigada" não é fim de relação — é fim de turno.
--
-- AS TRÊS TRAVAS, porque esconder um pedido é pior que cobrar à toa: pergunta com
-- '?' nunca é cortesia; frase longa nunca é; áudio, foto e documento nunca são —
-- não dá pra saber o que tem dentro. E a regra só REBAIXA, nunca promove.
--
-- O PORTÃO: `servico`. Vale em qualquer nicho que tenha funil e conversa — a
-- regra lê a CONVERSA, que toda conta tem, e não depende do follow-up estar
-- ligado. O selo é que depende, e ele já tinha o portão dele.
--
-- PRA QUEM: dono, gestor e vendedor. Muda o que o vendedor vê primeiro todo dia.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('cortesia-nao-e-cobranca', 'mudanca', 'servico', '{dono,gestor,vendedor}',
 'Quem só agradeceu não aparece mais como esperando resposta',
 'O funil parava de distinguir quem fez uma pergunta de quem respondeu "Ok, obrigada" — e cobrava os dois igual. Agora só quem pediu alguma coisa conta como esperando você.',
 '/painel/prospeccao',
 $txt$Se você abriu o funil e achou estranho ver "🚨 Crítico" num lead cuja última mensagem era "Obrigada!", você estava certo. A gente arrumou.

O QUE ESTAVA ACONTECENDO

O sistema decidia "o cliente está esperando" olhando o relógio: se a última mensagem da conversa era dele, a bola era sua. Só que isso trata igual duas coisas opostas — quem PERGUNTOU e quem AGRADECEU.

Fomos ler as conversas de verdade antes de mexer. De 57 leads que apareciam como "esperando resposta", 30 tinham como última mensagem só uma cortesia: "Ok", "Obrigada", "Tá certo", "Adorei". Mais da metade. E os dois primeiros da coluna Proposta, que abriam o quadro, eram "Obrigada!" e "Bgd".

O QUE MUDA

Quem só agradeceu sai do grupo "Esperando resposta" e para de virar Crítico por isso. O lead NÃO some: ele desce pro grupo de baixo, que é a verdade — vocês estão esperando ELE, e a régua de toques continua cobrando no prazo dela.

E isso não quer dizer que o lead morreu. Medimos: dos 189 "ok, obrigada" da história da conta, em 161 o cliente voltou a falar sozinho. 85%. Agradecimento é fim de assunto, não fim de negócio.

O QUE CONTINUA APARECENDO NO TOPO

Tudo que pede alguma coisa. E, de propósito, três casos continuam contando como espera mesmo parecendo cortesia:

• qualquer mensagem com pergunta — "Ok?" é pergunta;
• mensagem longa, mesmo começando com "ok" — quem escreveu bastante disse algo;
• áudio, foto e documento — não dá pra saber o que tem dentro, e supor que um áudio é "obrigada" esconderia um pedido seu cliente fez.

Preferimos errar cobrando a mais do que escondendo uma venda.

SE VOCÊ VIR UMA FRASE QUE ESCAPOU

A lista de expressões de cortesia foi montada com as mensagens reais dos seus clientes, e ela é ajustável. Se aparecer alguma que devia entrar (ou uma que não devia estar lá), é só falar.$txt$,
 timestamptz '2026-09-17 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'cortesia-nao-e-cobranca';
