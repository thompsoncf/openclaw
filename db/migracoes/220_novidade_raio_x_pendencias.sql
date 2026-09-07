-- 220_novidade_raio_x_pendencias.sql
-- Aviso da lista por trás de cada número pendente do Raio-X. O dono pediu em
-- 07/09/2026: "não consigo saber qual contrato ou proposta está pendente e a
-- conversa pra analisar do vendedor".
--
-- QUEM RECEBE: 'servico' com pra_quem {dono,gestor} — a mesma mira que a 214
-- deu aos outros avisos do Raio-X. A tela é de quem VENDE SERVIÇO: conta só de
-- produto nem abre (web.painel_raio_x redireciona pelo `perfil_da_conta`), e
-- anunciar pra ela seria prometer tela que não existe. Dono e gestor porque o
-- gate da tela é por papel; o vendedor tem o Raio-X dele no app, sem esta tabela.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('raio-x-pendencias', 'novidade', 'servico', '{dono,gestor}',
 'No Raio-X, cada número pendente abre a lista de quem é',
 'Rascunho, parou na 1ª e sem assinar deixam de ser só contagem: clique e veja os nomes, com atalho pra proposta e pra conversa.',
 '/painel/raio-x',
 $txt$Antes, "3 em rascunho" ou "2 parou na 1ª" na tabela por vendedor eram só contagens — pra descobrir de quem eram, era abrir Serviços e Prospecção e procurar um por um.

Agora esses números são clicáveis. Toque e a lista abre na própria linha do vendedor, com o nome do cliente, há quanto tempo está parado e dois atalhos: 📄 abre a proposta ou o contrato direto na tela de Serviços, e 💬 abre a conversa daquele cliente, pra você ler o que foi dito.

Vale pros três: propostas em rascunho que nunca saíram, leads que pararam na primeira tentativa (o cliente respondeu e ninguém voltou) e aprovados que ainda não assinaram.

E uma correção junto: quem fechou contrato no período escondia, na mesma coluna, os aprovados esperando assinatura. Agora os dois aparecem lado a lado.$txt$,
 timestamptz '2026-09-07 12:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'raio-x-pendencias';
