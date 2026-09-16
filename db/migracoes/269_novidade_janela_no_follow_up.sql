-- 269_novidade_janela_no_follow_up.sql
-- No Follow-up, "Abrir ficha" passa a abrir a janela do lead — com a situação.
--
-- O PEDIDO (dono, 16/09/2026), depois de trabalhar a fila:
--
--   "na parte prospecacao em follow-up deixar o vendedor analisar as conversas e
--    mudar o status do funil igual o app pois tem cliente que ja fechou mais esta
--    como contato"
--
-- e, ao dizer como: "dentro do follow tem um botao abrir ficha e mudar o status
-- pode ser por la e so deixar abrir uma janela igual tem no funil".
--
-- O QUE MUDOU NA TELA (/painel/follow-up)
-- 1) "Abrir ficha", no canto direito do card, ABRE A JANELA em vez de navegar. É a
--    mesma janela do funil: dados, "✎ Editar", histórico e o seletor de situação.
-- 2) A situação do funil se muda ali — que é o pedido: cliente que já fechou e
--    continuou em "contato" sai pra "ganho" sem sair da fila.
-- 3) A ficha completa continua a um clique, no rodapé da própria janela.
-- 4) Ler a conversa já era possível pelo balão do 💬 na prévia da mensagem; agora
--    as duas coisas que ele precisa — ler e mudar — cabem na mesma tela.
--
-- POR QUE NÃO ERA SÓ UM LINK. Navegar custava a fila inteira de volta e o lugar
-- onde a pessoa estava: quem trabalha o Follow-up percorre uma lista, e sair dela
-- pra corrigir uma situação é o tipo de atrito que faz a correção não acontecer —
-- que é exatamente o estado que o dono descreveu.
--
-- POR BAIXO: a janela saiu do funil pra `web/janela_lead.py` e as duas telas abrem
-- a MESMA. O balão de conversa já tinha feito esse caminho em 07/09 pelo mesmo
-- motivo; duas cópias divergem, e a que fica pra trás é sempre a que ninguém olha.
--
-- E UM VAZAMENTO DO §6 APARECEU NO CAMINHO, dentro do "✎ Editar": os campos do
-- evento (o tipo, o dia e o número de pessoas) eram fixos no código e apareciam em
-- TODA conta — inclusive na de mensalidade, que não tem nada disso. Ficava
-- escondido atrás de um botão, que é por onde ninguém olhou. Agora os rótulos são
-- declarados pela tela e só existem em conta que vende data.
--
-- O PORTÃO: `servico`. O Follow-up só abre pros perfis de `follow_up.PERFIS_COM_TELA`,
-- e a janela segue o nicho da conta por dentro (acima). Medido nas duas pontas (§6):
-- na 34 (eventos) o card mostra a data e os campos do evento existem; na 3
-- (consultoria) a mesma janela abre sem nenhuma palavra de festa.
--
-- PRA QUEM: vendedor (é a fila dele), gestor e dono (que olham a fila da equipe).
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('janela-do-lead-no-follow-up', 'mudanca', 'servico', '{dono,gestor,vendedor}',
 'No Follow-up, "Abrir ficha" agora abre uma janela — e dá pra mudar a situação ali',
 'A lista de follow-up deixou de mandar você pra outra tela: o lead abre numa janela, com os dados, o histórico e a situação do funil pra trocar na hora.',
 '/painel/follow-up',
 $txt$Tinha cliente que já fechou e continuava como "contato" na lista. Pra arrumar, você saía do Follow-up, abria a ficha, mudava, voltava — e a fila recomeçava do zero, sem o lugar onde você estava.

AGORA "ABRIR FICHA" ABRE UMA JANELA, ali mesmo. É a mesma janela do funil: os dados do cliente, o "✎ Editar" pra corrigir o que estiver errado, o histórico do que já aconteceu — e o seletor de situação no alto.

MUDAR A SITUAÇÃO É UM TOQUE. Escolheu "Ganho", acabou. A lista se atualiza e você continua de onde estava.

A FICHA COMPLETA continua a um clique, no rodapé da janela ("Ver ficha completa ↗"), pra quando você precisar do resto — orçamento, documentos, o cadastro inteiro.

E A CONVERSA você já lia sem sair da tela, tocando na prévia da última mensagem. Agora as duas coisas que o dia pede — ler o que o cliente disse e dizer em que pé está — cabem na mesma tela.$txt$,
 timestamptz '2026-09-16 22:30:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'janela-do-lead-no-follow-up';
