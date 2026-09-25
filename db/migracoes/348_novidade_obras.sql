-- 348_novidade_obras.sql
-- O aviso da aba Obras, seguindo a seção 5 do CLAUDE.md: PR que muda tela leva o
-- aviso, no mesmo PR. Precisa da 347 (as obras) e da 346 (o portão
-- `construcao`). Desenho aprovado pelo dono em 25/09/2026:
-- docs/mockups/nicho_construcao.html, seções 05 a 07.
--
-- PORTÃO `construcao`, o mesmo da 346: a aba só abre pro perfil `obras`
-- (web/painel_obras._acesso), e avisar qualquer outro ramo seria prometer uma
-- tela que não abre.
--
-- QUEM RECEBE, conferido na produção em 25/09/2026 (contas × nichos, só leitura):
--   conta 33 · Pablo Thyago G. Dias / PX2 Empreendimentos · Lago da Pedra-MA
--
-- PRA QUEM: dono e gestor. O vendedor não tem a aba.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-custo-por-casa', 'novidade', 'construcao', '{dono,gestor}',
 'A aba Obras: quanto custou cada casa',
 'Construtora e reforma ganharam a aba Obras: cada casa com o que já custou contra o previsto, as etapas marcadas pelo WhatsApp e os gastos que ainda não dizem de qual obra são.',
 '/painel/obras',
 $txt$Casa popular dá lucro ou não pelo custo de cada casa contra o que a Caixa paga — e até hoje o sistema não sabia de qual casa era cada nota. Agora sabe.

CADASTRE AS SUAS OBRAS

Em Obras, "Nova obra": o nome (Casa 1, Casa 2, a reforma da Dona Maria), o lote, a área, o custo previsto e o valor de venda ou do contrato. Cada obra ganha as etapas — preliminares e fundação, estrutura, alvenaria, cobertura, instalações, reboco, pisos, esquadrias, louças, pintura e limpeza —, cada uma com um peso no andamento. Os pesos vêm da planilha de medição da Caixa; ajuste na primeira casa e as próximas já nascem com o seu ajuste.

O WHATSAPP LANÇA NA CASA CERTA

Mande a foto da nota como sempre. O assistente pergunta de qual obra foi — e, se for das três casas, divide em partes iguais sem mexer no valor do comprovante. Pergunte "quanto já gastei na casa 2?" e ele responde com material, mão de obra e o quanto isso é do previsto. Diga "terminou o telhado da casa 3" e ele marca a etapa; quando todas estiverem feitas, a casa passa a pronta.

OS GASTOS SEM OBRA

O que você já lançou antes das obras existirem aparece em "Sem obra", no fim da aba: ponha cada um na sua casa, ou divida entre as obras em andamento. O assistente também pode fazer isso com você, um por um, pelo WhatsApp.

A FICHA DE CADA OBRA

Mostra o gasto separado em material, mão de obra e outros, o custo por m², as etapas, e cada lançamento — inclusive a parte de uma nota dividida.

O que vem depois: os documentos de cada casa (alvará, habite-se, CND, averbação) e o caminho até o dinheiro da Caixa cair na conta.$txt$,
 timestamptz '2026-09-25 22:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-custo-por-casa';
