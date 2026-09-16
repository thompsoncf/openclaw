-- 265_novidade_funil_ordem_festa.sql
-- O funil de eventos: a festa mais próxima primeiro, e o follow-up no card.
--
-- O QUE MUDOU NA TELA (/painel/prospeccao)
-- 1) O grupo "🟢 Esperando resposta" — que já abre toda coluna — passa a ordenar
--    pela FESTA MAIS PRÓXIMA, e não mais pela mensagem mais nova. Festa que já
--    passou vai pro fim do grupo; com a mesma data, quem espera há mais tempo vem
--    primeiro.
-- 2) O card ganha o SELO DO FOLLOW-UP: 🚨 Crítico, 🔴 Atrasado, 🟡 Follow-up hoje,
--    🔵 Agendado, 🟢 Em andamento, ⚠️ Sem próxima ação — com o atraso do lado
--    ("· 16h", "· 3d").
--
-- O CASO QUE DECIDIU, conta 34, coluna Proposta, 16/09/2026:
--     Josiany Rayra Santos   festa em 15 dias   esperando resposta há 2 dias
--     Renata Costa           festa em 11 dias   esperando resposta há 7 dias
-- Pela mensagem mais nova a Renata ficava EMBAIXO — com a festa mais perto E
-- esperando há mais tempo. Em quem vende data é a data que tranca a venda.
--
-- O QUE NÃO MUDOU, e vale dizer porque parece que mudaria: o grupo "esperando
-- resposta" JÁ existia (migração 197 / #622), já vinha primeiro em toda coluna, e
-- o resto da coluna já era agrupado por mês da festa. Esta entrega mexe na ordem
-- DENTRO de um grupo e acrescenta um selo. Nada sai de lugar, nada some.
--
-- O PORTÃO: `eventos`. A ordem por festa só existe onde há festa — o mesmo portão
-- (`vendas.vende_data`) que já decide o trilho de meses e o "sem data" no card. E
-- o selo, hoje, só alcança quem tem a tela de Follow-up, que é `eventos`
-- (follow_up.PERFIS_COM_TELA). Quem vende mensalidade não vê nada diferente.
--
-- O SELO APARECE EM 'observando', decisão do dono no mesmo dia: dá pra ver a régua
-- rodando com lead de verdade antes de ligar a cobrança. Em 'off' não aparece —
-- aí o dono não optou por nada.
--
-- PRA QUEM: dono, gestor e vendedor. O funil é a tela que o vendedor abre todo
-- dia, e isto muda o que ele vê primeiro.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('funil-ordem-festa-e-follow-up', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'No funil, a festa mais próxima vem primeiro',
 'No funil de eventos, os leads que estão esperando resposta agora aparecem na ordem da festa mais próxima, e cada card mostra a situação do follow-up.',
 '/painel/prospeccao',
 $txt$Duas mudanças no funil, e as duas vieram de olhar a coluna Proposta de uma conta de verdade.

A FESTA MAIS PRÓXIMA VEM PRIMEIRO

O grupo "🟢 Esperando resposta" já abria toda coluna — é o que o cliente falou e ninguém respondeu. O que mudou é a ordem dentro dele: antes era a mensagem mais nova no topo, agora é a festa mais próxima.

O caso que decidiu isso: um lead com festa em 15 dias esperando resposta há 2, e outro com festa em 11 dias esperando há 7. Pela ordem antiga o segundo ficava embaixo — com a festa mais perto E esperando há mais tempo. É ele que está mais perto de desistir e de contratar outro espaço.

Detalhes da ordem, pra não ter surpresa: festa que já passou vai pro fim do grupo (não some, só sai do topo); dois leads com a mesma data desempatam pela espera mais longa; e quem ainda não tem data marcada aparece depois de quem tem.

O CARD DIZ COMO ESTÁ O FOLLOW-UP

Cada card agora mostra o estado do acompanhamento, com o atraso do lado:

🚨 Crítico · 🔴 Atrasado · 🟡 Follow-up hoje · 🔵 Agendado · 🟢 Em andamento · ⚠️ Sem próxima ação

É exatamente o mesmo cálculo da tela de Follow-up — não é um segundo placar. A diferença é que agora você vê isso sem sair do quadro, no mesmo lugar em que já está arrastando o lead.

Se o seu follow-up está em "observando", o selo aparece do mesmo jeito. Foi de propósito: dá pra acompanhar a régua com os seus leads reais antes de ligar a cobrança automática.

O QUE NÃO MUDOU

As colunas são as mesmas, os grupos são os mesmos, a dobra dos parados continua no pé. Nenhum lead saiu de lugar e nenhum sumiu — o que mudou foi a ordem dentro de um grupo e o que o card conta.$txt$,
 timestamptz '2026-09-16 15:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'funil-ordem-festa-e-follow-up';
