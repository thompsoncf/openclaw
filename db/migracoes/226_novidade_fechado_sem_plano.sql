-- 226_novidade_fechado_sem_plano.sql
-- Serviços › funil: selo âmbar quando o negócio fecha sem plano de pagamento.
--
-- O QUE MUDOU NA TELA
-- Linha de proposta FECHADA e sem plano de pagamento ganha o selo âmbar
-- "Fechado sem plano de pagamento". Nada mais muda: o selo não vira ação e não
-- desloca o botão verde que a linha já mostrava (SELO É PENDÊNCIA, AÇÃO É UMA SÓ).
--
-- O BURACO QUE ELE DENUNCIA. `vendas.fechar_orcamento` tem uma saída de
-- emergência: orçamento de evento sem plano vira UM título com o valor total.
-- Isso salva o recebível — e cria um estado mudo, porque a tela de Pagamentos
-- monta as linhas a partir de `orcamentos.parcelas` (vazio) e o comprovante é
-- indexado por `parcela_idx` (nulo naquele título). O dinheiro fica no contas a
-- receber e não há onde anexar o comprovante nem como acompanhar as parcelas.
--
-- POR QUE SELO E NÃO CONFIRMAÇÃO NO BOTÃO. No nicho de eventos ninguém está na
-- tela na hora de fechar: quem fecha é a ASSINATURA do cliente (`contrato.assinar`
-- chama `fechar_orcamento(por_assinatura=True)`). Um `confirm()` avisaria o
-- vendedor sobre um clique que ele não deu. O selo aparece depois do fato e fica
-- até alguém montar o plano.
--
-- O CASO, conferido na produção em 08/09/2026 (conta 34):
--   orçamento nº 20 (Kelma Costa da Silva) · contrato nº 6 assinado 18:37:22
--   título nº 61 "Evento — Kelma Costa da Silva" R$ 6.500,00 criado 18:37:23,
--   com `parcela_idx` NULO — e o dono procurando o botão de anexar comprovante.
-- Outras duas da mesma conta foram MANDADAS ao cliente sem plano (nº 19 Kleiton,
-- nº 22 Renata Tatiana); essas não recebem o selo, que é só do que fechou.
--
-- O PORTÃO: `servico`. O funil é do módulo Serviços; conta que não vende serviço
-- não tem essa tela. Não é por nicho de eventos: quem vende por mensalidade fecha
-- pelo botão e também pode fechar sem plano.
--
-- PRA QUEM: dono e gestor. O VENDEDOR NÃO: quem monta plano de pagamento e mexe
-- em contas a receber é quem manda na conta — oferecer a ele um selo que aponta
-- pra uma tela que ele não opera seria aviso sem saída.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('funil-avisa-fechado-sem-plano', 'novidade', 'servico', '{dono,gestor}',
 'O funil avisa quando um negócio fecha sem plano de pagamento',
 'Proposta que fecha sem plano de pagamento passa a mostrar um selo no funil — antes o negócio fechava, o recebível entrava como um título único e não havia onde anexar o comprovante.',
 '/painel/servicos',
 $txt$Quando uma proposta fecha sem plano de pagamento montado, o sistema não perde o dinheiro: ele cria um título único, com o valor total, no contas a receber.

O QUE ELE NÃO CONSEGUE FAZER é acompanhar esse dinheiro em partes. A tela de Pagamentos monta a lista a partir das parcelas do plano, e o comprovante é guardado por parcela. Sem plano não há parcela, sem parcela não há linha na tela — e sem linha não há onde anexar o comprovante. O negócio fica fechado, o valor fica lançado, e não dá pra registrar que o cliente pagou.

AGORA A LINHA AVISA. Proposta fechada sem plano ganha o selo âmbar "Fechado sem plano de pagamento". Abra a proposta, monte o plano (a entrada e as parcelas) e salve: a tela de Pagamentos passa a ter as linhas, e o botão de anexar comprovante aparece em cada uma.

POR QUE UM SELO, E NÃO UM AVISO NA HORA DE FECHAR. Em locação de espaço e eventos, quem fecha o negócio é a assinatura do cliente no contrato — não um clique de vocês. Não há ninguém na tela naquele momento pra avisar. Por isso o aviso aparece depois, na linha, e fica lá até alguém resolver.

O SELO NÃO ATRAPALHA O RESTO: ele não vira botão e não toma o lugar da ação que a linha já pedia. Se a proposta tinha "Anexar comprovante" como próximo passo, continua tendo.$txt$,
 timestamptz '2026-09-08 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'funil-avisa-fechado-sem-plano';
