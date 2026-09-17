-- 271_novidade_motivo_no_quadro.sql
-- Arrastar o card pra Perdido passa a perguntar por quê, no painel.
--
-- ACHADO PELO DONO em 17/09/2026, olhando o bloco "Por que perdeu" do Raio-X:
-- "3 de 5 sem motivo". Ele mandou olhar o card; o card estava certo — quem estava
-- errada era a tela onde o lead se perde.
--
-- O DEFEITO. O servidor SEMPRE soube recusar: quando a etapa exige motivo
-- (migração 235), `POST /painel/prospeccao/<id>/status` devolve
-- `{ok:false, erro:'motivo_obrigatorio', motivos:[...]}` — com a lista pronta pra
-- tela montar. O painel JOGAVA ISSO FORA, nos dois caminhos que levam a Perdido:
--
-- * ARRASTANDO O CARD no quadro, a recusa caía no `_kbAposMoverStatus`, cujo
--   `!d.ok` RECARREGA a página. Na tela isso é o card voltando sozinho pra coluna
--   de origem, sem uma palavra. Quem arrastou não tinha como saber que faltava
--   dizer por quê — e a leitura natural é "o sistema travou".
-- * NO SELETOR DE SITUAÇÃO da janela do lead, a resposta era um alerta de uma
--   linha: "Não consegui mudar a situação". Não diz o que fazer; é um beco.
--
-- E o próprio bloco do Raio-X dizia que o motivo é um toque numa lista de seis "no
-- app e na ficha". A frase era, sem querer, a confissão: no QUADRO, que é onde o
-- gestor move card, lista nenhuma.
--
-- O QUE MUDOU NA TELA (/painel/prospeccao)
-- 1) Arrastar o card pra Perdido abre a folha "Por que perdeu?", com os motivos DA
--    CONTA. Escolheu, confirmou, o card fica onde foi solto.
-- 2) O motivo que pede uma linha de explicação abre o campo na hora — e só ele.
-- 3) "Deixar como estava" devolve o card, agora porque a pessoa quis.
-- 4) O mesmo vale pro seletor de situação da janela do lead, no Funil e no
--    Follow-up: é a MESMA folha (web/janela_lead.py), não uma segunda cópia.
--
-- O QUE NÃO MUDOU: a regra. Quem decide se o motivo é obrigatório continua sendo
-- `funil_etapas.exige_motivo`, por etapa, como desde a 235. Conta que não exige
-- nada não vê folha nenhuma.
--
-- O QUE ISTO NÃO CONSERTA, e é honesto dizer: os leads que já estão sem motivo
-- continuam sem. O motivo deles pode ser preenchido na ficha do lead, que sempre
-- teve o campo.
--
-- O PORTÃO: `servico`. É o quadro do funil — existe em qualquer nicho que venda
-- serviço, e não existe em conta de produto. Medido nas duas pontas (§6): a 34
-- (eventos) exige motivo hoje e é onde a queixa nasceu; a 3 (consultoria) não
-- exige, e nela nada muda — que é o comportamento certo.
--
-- PRA QUEM: dono e gestor (quem move card no painel) e vendedor (que também abre
-- a janela do lead pelo Follow-up).
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('motivo-da-perda-no-quadro', 'mudanca', 'servico', '{dono,gestor,vendedor}',
 'Arrastar para Perdido agora pergunta por quê',
 'No painel, mover um lead para Perdido passou a abrir a lista de motivos da empresa em vez de recusar em silêncio.',
 '/painel/prospeccao',
 $txt$No Raio-X, o bloco "Por que perdeu" mostrava 3 de 5 leads sem motivo. O card estava certo — quem estava errada era a tela onde o lead se perde.

O QUE ACONTECIA: quando a sua empresa exige o motivo da perda, o sistema recusava a mudança e mandava a lista de motivos junto. O painel descartava a lista. Arrastando o card para Perdido, a página simplesmente recarregava e o card voltava sozinho para a coluna de origem, sem nenhuma mensagem — parecia que o sistema tinha travado. Pelo seletor de situação, vinha só um "não consegui mudar a situação", que não diz o que fazer.

A lista de seis existia no app do vendedor e na ficha do lead. No quadro, que é onde se move card, não existia.

AGORA, ao arrastar um lead para Perdido, abre a pergunta: "Por que perdeu?", com os motivos da sua empresa. Um toque, confirmar, pronto — o card fica onde você soltou. O motivo que pede uma linha de explicação abre o campo na hora. E "Deixar como estava" devolve o card, agora porque você quis.

Vale também para o seletor de situação da janela do lead, no Funil e no Follow-up.

OS LEADS QUE JÁ ESTÃO SEM MOTIVO continuam sem — isto não reescreve o passado. Se quiser completar algum, o campo "Por que perdeu" está na ficha do lead.$txt$,
 timestamptz '2026-09-17 13:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'motivo-da-perda-no-quadro';
