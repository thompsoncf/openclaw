-- 208_novidade_raio_x.sql
-- Os avisos do Raio-X (seção 5 do CLAUDE.md: PR que muda tela leva o aviso).
-- Precisa da 199 (pra_quem, resumo, link).
--
-- QUEM RECEBE
--   raio-x-no-app (todos · vendedor)      todo vendedor com o app: a aba Resultado
--                                         virou Raio-X. Público 'todos' porque o
--                                         app é de qualquer conta, sem nicho.
--   raio-x-de-segunda (todos · dono, gestor)  quem manda na conta: é ele quem
--                                         escolhe o grupo em Equipe. Sem escolher,
--                                         nada é enviado — o aviso é o convite.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('raio-x-no-app', 'novidade', 'todos', '{vendedor}',
 'A aba Resultado virou Raio-X',
 'O app do vendedor ganhou o Raio-X: sua semana em quatro números, quem responder hoje pelo que mais urge, e o que está a um passo do contrato.',
 '/cockpit/raio-x',
 $txt$A aba Resultado virou Raio-X, em três blocos que rolam numa tela só.

Sua semana: tempo da primeira resposta ao lead novo (a meta é 5 minutos), propostas enviadas e as que ficaram em rascunho, toques de retorno feitos, e contratos assinados. Cada número com a cor do dia e o comparativo com a semana anterior.

Responda hoje: a fila do que mais urge, em quatro faixas. Pergunta do cliente sem resposta, festa perto sem proposta, toque da cadência vencendo hoje, e visita amanhã pra confirmar. Cada linha abre a conversa no ponto certo.

Fechamentos: quem assinou, quem disse sim e falta assinar, proposta esperando o cliente, e rascunho que não saiu, cada um com o botão certo.

No pé, a confiança do dado: quantos dias o Zaq mediu, quantas vezes a conexão religou, e quais mensagens podem não ter chegado, pelo nome do cliente. Se aparecer um nome ali, confira no celular.

Seu dinheiro continua lá, no fim da tela: comissão, recebido e o que está no funil.$txt$,
 timestamptz '2026-09-05 18:00:00+00'),

('raio-x-de-segunda', 'novidade', 'todos', '{dono,gestor}',
 'O Raio-X de segunda chega no grupo dos vendedores',
 'Toda segunda, 8h, o Zaq manda no grupo do WhatsApp o placar da semana por vendedor e a lista "responda hoje". Você escolhe o grupo em Equipe.',
 '/painel/equipe',
 $txt$Toda segunda-feira às 8h o Zaq manda no grupo dos vendedores o Raio-X da semana anterior: uma linha por vendedor (leads novos, primeira resposta, propostas enviadas e em rascunho, quantos clientes esperando), a linha da empresa, e a confiança do dado.

Pra ligar: em Equipe, no bloco "Raio-X de segunda", escolha o grupo do WhatsApp da equipe e salve. O número da empresa precisa estar no grupo. Enquanto nenhum grupo estiver escolhido, nada é enviado.

O botão "Mandar agora" manda o Raio-X da semana corrente na hora, pra você ver como fica antes da primeira segunda.

O mesmo Raio-X está no app de cada vendedor, na aba que antes se chamava Resultado.$txt$,
 timestamptz '2026-09-05 18:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave in ('raio-x-no-app', 'raio-x-de-segunda');
