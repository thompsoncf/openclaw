-- 221_novidade_raio_x_pendencias_balao.sql
-- O aviso da 220 (a lista por trás de cada número pendente do Raio-X) descrevia
-- o 💬 levando "pra conversa". Horas depois, o dono usou a tela e pediu três
-- coisas: a espera em DIAS (a lista dizia "há 476h"), o telefone ao lado do
-- nome (metade dos leads tem nome de WhatsApp que não identifica ninguém) e a
-- conversa abrindo NA PRÓPRIA PÁGINA, como no funil — sair do Raio-X e voltar
-- recarregava tudo e fechava a lista.
--
-- POR QUE ATUALIZA EM VEZ DE CRIAR OUTRO. É o mesmo recurso, do mesmo dia: dois
-- avisos seguidos sobre a mesma tela viram ruído, e quem abrir as Novidades vai
-- ler o que a tela FAZ, não o histórico de como ela chegou lá. O aviso da 220
-- está no ar há menos de um dia.
--
-- `titulo`, `resumo`, `link` e `publicado_em` não mudam: a novidade é a mesma, e
-- mexer no `publicado_em` a jogaria de novo pro topo como se fosse outra.
--
-- Idempotente por natureza (update com texto fixo) e sem efeito se a 220 ainda
-- não rodou (0 linhas).

update public.novidades set corpo = $txt$Antes, "3 em rascunho" ou "2 parou na 1ª" na tabela por vendedor eram só contagens — pra descobrir de quem eram, era abrir Serviços e Prospecção e procurar um por um.

Agora esses números são clicáveis. Toque e a lista abre na própria linha do vendedor, com o nome do cliente, o telefone ao lado, há quanto tempo está parado e dois atalhos: 📄 abre a proposta ou o contrato na tela de Serviços, e 💬 abre a conversa **ali mesmo**, num balão sobre a tabela — você lê o que foi dito e pode até responder sem perder a lista de vista.

Vale pros três: propostas em rascunho que nunca saíram, leads que pararam na primeira tentativa (o cliente respondeu e ninguém voltou) e aprovados que ainda não assinaram.

E uma correção junto: quem fechou contrato no período escondia, na mesma coluna, os aprovados esperando assinatura. Agora os dois aparecem lado a lado.$txt$
 where chave = 'raio-x-pendencias';

-- rollback: (volta o corpo da 220)
--   update public.novidades set corpo = '...o texto original da 220...'
--    where chave = 'raio-x-pendencias';
