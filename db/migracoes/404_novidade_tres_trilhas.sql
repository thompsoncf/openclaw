-- 404_novidade_tres_trilhas.sql
-- Os avisos do funil em três trilhas (docs/mockups/funil_tres_trilhas.html, versão 2,
-- aprovado pelo dono em 27/09/2026 "com as recomendações"), seguindo a seção 5 do
-- CLAUDE.md.
--
-- 1. 'funil-tres-trilhas' — PÚBLICO `mais_de_um_chip`: a barra só aparece em conta com
--    a IA do número ou o resgate, e os dois moram na regra por número, que só existe
--    com dois chips. PRA QUEM: dono e gestor (decisão 2: o vendedor não vê a barra).
-- 2. 'espelho-do-vendedor' — PÚBLICO `esteira_ligada`: o espelho copia a cobrança da
--    esteira, e mora no cartão dela, na Régua. PRA QUEM: dono e gestor.
--
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura): o 1º, a Prime (34) e
-- a conta 37 (as duas com dois chips); o 2º, a Prime (34), a única com a esteira ligada.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('funil-tres-trilhas', 'novidade', 'mais_de_um_chip', '{dono,gestor}',
 'O funil em três trilhas: vendedores, a IA do número e o resgate',
 'O funil ganhou uma barra com as três trilhas (vendedores, a IA do número e o resgate da IA), cada uma com a sua regra, e uma coluna Resgate que diz de onde veio cada lead.',
 '/painel/prospeccao',
 $txt$O quadro continua um só, com as mesmas etapas. Em cima dele:

A BARRA DAS TRILHAS
- Vendedores: os leads da equipe, cobrados pela esteira.
- IA do número: os leads que a IA atende desde o primeiro "oi" — conversando, sumidos, e onde alguém da equipe assumiu.
- Resgate da IA: quantos estão com a IA, quantos na fila, o que saiu hoje e quem respondeu.
- Toque numa trilha e o quadro mostra só os leads dela; toque de novo e volta tudo.

A COLUNA RESGATE
- Na frente do quadro, quem está com o resgate em andamento: de onde veio ("veio do follow-up · era do Pedro · 9 dias parado", "veio dos perdidos", "veio da IA do número"), a etapa em que está e o próximo passo.
- A etapa do lead não muda: a coluna é só uma visão.
- Respondeu, o card volta pra coluna da etapa com o selo "veio do Resgate".

ANTES DE CADA CONVERSA, O RESUMO
- Antes de chamar, a IA faz o ✨ Resumo da conversa inteira e escreve a partir do ponto em que ela parou. O resumo fica na ficha.
- Mensagem com valor fora do orçamento e do catálogo não sai sozinha: vem pra você.

OS PERDIDOS, PELO MOTIVO
- Fechou com concorrente, desistiu ou fora do escopo: não são chamados.
- Não respondeu: uma mensagem só, sem os toques.
- Data indisponível: só com a festa a mais de 30 dias, perguntando se a data é flexível.
- O perdido da IA do número volta uma vez, 30 dias depois.$txt$,
 timestamptz '2026-09-27 20:00:00+00'),
('espelho-do-vendedor', 'novidade', 'esteira_ligada', '{dono,gestor}',
 'Espelho do vendedor: a cobrança do jeito que ela chega na equipe',
 'Escolha um vendedor e receba no seu WhatsApp uma cópia do que ele recebe da esteira da cobrança e do resgate, marcada como cópia.',
 '/painel/prospeccao/regua#espelho',
 $txt$Na Régua do funil, no cartão novo "Espelho do vendedor":

- Escolha um vendedor e quem recebe a cópia (precisa ter WhatsApp no cadastro).
- A cobrança da manhã, o aviso do último dia e o aviso do resgate chegam também pra você, com "🪞 Cópia do que o Pedro recebeu" no começo.
- Não conta no teto do vendedor, não vira cobrança de ninguém, e ele não fica sabendo.
- Um vendedor por vez. Pra desligar, escolha "ninguém".$txt$,
 timestamptz '2026-09-27 20:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave in ('funil-tres-trilhas','espelho-do-vendedor');
