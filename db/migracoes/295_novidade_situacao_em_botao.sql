-- 295_novidade_situacao_em_botao.sql
-- A situação na ficha virou botão, e "Abrir ficha" na Comunicação virou janela.
--
-- O PEDIDO (dono, 19/09/2026), olhando o seletor da ficha: "coloca um abrir ficha
-- como pop-up, e é melhor colocar o status com o botão pra alterar, fica mais
-- fácil pro vendedor, igual está no cockpit".
--
-- O QUE MUDOU NA TELA
-- 1) /painel/prospeccao/<id> (a ficha do lead): onde havia um seletor de situação,
--    agora há a mesma fileira de botões da janela do lead e do app do vendedor —
--    a etapa atual acesa, a seguinte destacada, e Ganho/Perdido numa linha
--    própria depois de "Encerrar". Mudar a situação não recarrega mais a página.
-- 2) /painel/prospeccao/comunicacao: "Abrir ficha", no topo da conversa e no
--    painel do lado, abre a janela do lead por cima — em vez de levar a pessoa
--    embora da conversa que ela estava lendo.
--
-- O DEFEITO QUE O PEDIDO DESTAPOU, e que é a parte que importa: o seletor da
-- ficha era o TERCEIRO jeito de mudar a situação no produto, e o único que não
-- aprendeu a perguntar o motivo. O servidor recusa certo desde a migração 235 —
-- devolve `motivo_obrigatorio` COM a lista de motivos pronta —; o quadro e a
-- janela usam essa lista desde o #712, e a ficha imprimia o nome técnico do erro
-- ("Não consegui mudar a situação (motivo_obrigatorio)") e voltava o seletor
-- sozinho. Na prática: QUEM TENTAVA PERDER UM LEAD PELA FICHA COMPLETA NÃO
-- CONSEGUIA. Agora os três caminhos passam pelo mesmo `kbLeadIr`, e não sobra um
-- terceiro lugar pra esquecer de consertar no próximo.
--
-- O PORTÃO: `servico`. É sobre funil, etapa e motivo de perda — existe em
-- qualquer conta que venda serviço e não existe em conta de produto, que não tem
-- funil. Dentro da fileira não há palavra de nicho nenhuma: os rótulos das etapas
-- são os da própria conta (§6).
--
-- PRA QUEM: dono, gestor e vendedor. É a rotina dos três — o vendedor é quem mais
-- mexe na situação, e foi por ele que o dono pediu.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('situacao-em-botao', 'mudanca', 'servico', '{dono,gestor,vendedor}',
 'A situação do lead agora se muda com um toque, na ficha e na conversa',
 'Na ficha do lead o seletor de situação virou uma fileira de botões, igual à do app; e na Comunicação a ficha abre por cima da conversa, sem levar você embora dela.',
 '/painel/prospeccao',
 $txt$Duas telas ficaram com o mesmo jeito que o app do vendedor já tinha.

NA FICHA DO LEAD, a situação deixou de ser uma listinha pra virar uma fileira de botões: a etapa em que o lead está fica acesa, a seguinte aparece destacada — é quase sempre a que você veio dar — e "Ganho" e "Perdido" ficam numa linha separada, embaixo de "Encerrar". São dois toques a menos que abrir a lista, escolher e esperar.

E MUDAR A SITUAÇÃO NÃO RECARREGA MAIS A PÁGINA. Os botões se reorganizam ali mesmo.

UMA COISA QUE ESTAVA QUEBRADA E VOCÊ TALVEZ NÃO SOUBESSE: quando a etapa exige motivo — "Perdido", na maioria das contas —, a ficha completa não conseguia. Ela mostrava um recado técnico e voltava sozinha pro que estava antes, sem nunca perguntar o motivo. Pelo quadro e pela janela do lead funcionava; só pela ficha, não. Agora a ficha abre a mesma lista de motivos que os outros dois.

NA COMUNICAÇÃO, o botão "Abrir ficha" (no topo da conversa e no painel do lado) abre a ficha resumida POR CIMA da conversa, em vez de levar você pra outra tela. Você vê de quem é aquela conversa, muda a situação se precisar, fecha, e a conversa continua exatamente onde estava. A ficha completa segue a um clique, no rodapé da própria janela.

É o mesmo botão e a mesma janela que o Follow-up já usa desde a semana passada.$txt$,
 timestamptz '2026-09-19 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'situacao-em-botao';
