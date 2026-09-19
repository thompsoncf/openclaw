-- 299_novidade_agenda_responder_a_conferir.sql
-- Agora dá pra RESPONDER as datas que o calendário marcou como "a conferir".
--
-- O par da 297. Lá o calendário aprendeu a admitir que não sabe se um
-- compromisso ocupa o espaço; aqui a pessoa responde, e a marca coral some.
--
-- O QUE MUDOU NA TELA:
--   * Tocando no dia, o compromisso "a conferir" abre com dois botões: "Ocupa —
--     não pode vender" e "Não ocupa — o dia segue à venda".
--   * Respondido, o bloco vira cinza e ganha "Desfazer" — clicou errado, volta.
--   * O card de avisos no topo ganhou a quarta linha, "N datas a conferir", com
--     o botão "Responder" que abre o dia certo.
--
-- ONDE ELA MORA, E POR QUÊ. Na caixa do dia que já existe, não em tela nova: a
-- pergunta é sobre UM DIA, e a caixa do dia é o que já abre ao tocar nele. Tela
-- separada seria um segundo lugar mostrando os mesmos compromissos, com duas
-- chances de discordarem.
--
-- POR QUE PERGUNTAR EM VEZ DE CHUTAR. Medido na Prime (conta 34): cinco dos 84
-- compromissos ativos não têm sinal nenhum que os classifique, e dois deles têm
-- a mesma palavra no título com respostas opostas — "REUNIÃO COM ENGENHEIRA"
-- não ocupa o espaço, "Reunião Política - Bianca - Pedro" ocupa (teve sinal de
-- R$ 750). Marcar a primeira como vendida tiraria um sábado do ar.
--
-- PRA QUEM: dono, gestor e vendedor. Quem abre o dia é quem responde.
--
-- O PORTÃO: `eventos`, o mesmo da 297.
--
-- CONTAS ALCANÇADAS: 34 (MANOEL SOARES) e 35 (Louana vanessa cardoso Santos
-- costa).
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('agenda-responder-a-conferir', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'As datas "a conferir" agora podem ser respondidas',
 'Os compromissos que o sistema não conseguia classificar sozinho passaram a mostrar dois botões na tela do dia — "ocupa o espaço" ou "não ocupa" — e a resposta some do aviso na hora, liberando ou reservando aquela data no calendário.',
 '/painel/agenda',
 $txt$Semana passada o calendário passou a dizer se cada dia está livre, ocupado ou segurado. Alguns compromissos antigos ficaram marcados em vermelho, como "a conferir": são aqueles que o sistema não conseguiu classificar sozinho.

AGORA VOCÊ RESPONDE

Toque no dia. O compromisso aparece com a pergunta e dois botões:

• Ocupa — não pode vender
• Não ocupa — o dia segue à venda

Respondeu, a marca vermelha some e o calendário passa a tratar aquele dia como você disse.

CLICOU ERRADO? DESFAZ

Depois de respondido, o bloco mostra um "Desfazer". O compromisso volta pro "a conferir" e você responde de novo. Nada de resposta virar pedra.

O AVISO DO TOPO MOSTRA QUANTAS FALTAM

O card de avisos ganhou mais uma linha: "N datas a conferir", com o botão Responder que já abre o dia certo. Quando acabar, a linha some sozinha.

POR QUE O SISTEMA PERGUNTA EM VEZ DE ADIVINHAR

Porque adivinhar erraria. Na sua agenda existem "REUNIÃO COM ENGENHEIRA", que não ocupa o espaço, e "Reunião Política", que ocupou e teve sinal pago. A mesma palavra, respostas contrárias.

Se o sistema chutasse pelo nome, marcaria a reunião com a engenheira como data vendida — e um sábado sairia do ar sem ninguém perceber. Preferimos perguntar uma vez a errar para sempre.

São poucas: cinco na sua conta hoje, e elas não voltam. Compromisso novo já nasce classificado, porque você escolhe o tipo de festa quando marca.$txt$,
 timestamptz '2026-09-20 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'agenda-responder-a-conferir';
