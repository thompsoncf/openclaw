-- 405_novidade_tres_trilhas_fila.sql
-- O aviso 'funil-tres-trilhas' (migração 404) saiu hoje dizendo que a coluna Resgate
-- mostra "quem está com o resgate em andamento". No primeiro teste o dono não viu a
-- coluna: com o resgate em Ensaio ninguém está em andamento, e ela sumia. Agora ela
-- aparece sempre que o resgate está em Ensaio ou Ligado, com os próximos da fila — e
-- o "Testar comigo" marca a visita como o atendimento de verdade marca.
--
-- O texto do MESMO aviso é corrigido, em vez de um segundo aviso no mesmo dia: quem
-- ainda não abriu lê o certo, e quem abriu não recebe duas notificações sobre a mesma
-- tela. Só `corpo`; título, público e data ficam.

update public.novidades set corpo = $txt$O quadro continua um só, com as mesmas etapas. Em cima dele:

A BARRA DAS TRILHAS
- Vendedores: os leads da equipe, cobrados pela esteira.
- IA do número: os leads que a IA atende desde o primeiro "oi" — conversando, sumidos, e onde alguém da equipe assumiu.
- Resgate da IA: quantos estão com a IA, quantos na fila, o que saiu hoje e quem respondeu.
- Toque numa trilha e o quadro mostra só os leads dela; toque de novo e volta tudo.

A COLUNA RESGATE
- Na frente do quadro, sempre que o resgate está em Ensaio ou Ligado.
- Em cima, quem está com o resgate em andamento: de onde veio ("veio do follow-up · era do Pedro · 9 dias parado", "veio dos perdidos", "veio da IA do número"), a etapa em que está e o próximo passo.
- Embaixo, os próximos da fila, com a origem de cada um. No Ensaio são os que a IA chamaria — nenhum muda de dono.
- A etapa do lead não muda: a coluna é só uma visão.
- Respondeu, o card volta pra coluna da etapa com o selo "veio do Resgate".

ANTES DE CADA CONVERSA, O RESUMO
- Antes de chamar, a IA faz o ✨ Resumo da conversa inteira e escreve a partir do ponto em que ela parou. O resumo fica na ficha.
- Mensagem com valor fora do orçamento e do catálogo não sai sozinha: vem pra você.

O "TESTAR COMIGO" MARCA A VISITA
- Com "A IA marca a visita" ligada na regra, o teste oferece os horários livres da grade e da agenda de verdade, com as letras, e confirma o que você escolher — sem gravar nada na agenda.

OS PERDIDOS, PELO MOTIVO
- Fechou com concorrente, desistiu ou fora do escopo: não são chamados.
- Não respondeu: uma mensagem só, sem os toques.
- Data indisponível: só com a festa a mais de 30 dias, perguntando se a data é flexível.
- O perdido da IA do número volta uma vez, 30 dias depois.$txt$
 where chave = 'funil-tres-trilhas';

-- rollback: o texto anterior está na migração 404.
