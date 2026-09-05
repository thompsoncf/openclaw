-- 214_novidade_raio_x_por_nicho.sql
-- Os avisos do Raio-X, corrigidos pelo perfil (seção 5 do CLAUDE.md).
--
-- Os três avisos das Peças 1 a 3 (208 e 210) saíram com público 'todos' e com
-- o corpo falando de festa. Foram pro dono da ZAQ e dos escritórios de
-- consultoria, que não vendem festa. Aqui:
--
--   * o PÚBLICO vira 'servico' (= vende serviço: eventos E recorrente). Conta
--     só de produto não tem funil nem vendedor — o Raio-X não se aplica a ela,
--     e o aviso não sai.
--   * o CORPO fica neutro: fala de "compromisso" e "o que o cliente pediu",
--     e diz que a tela fala a língua do nicho.
--
-- E um aviso novo, pra quem já tinha lido os anteriores:
--   raio-x-por-nicho (servico · dono, gestor, vendedor)  o Raio-X passou a
--     perguntar primeiro o que a empresa vende. Na conta de festa nada muda;
--     na de serviço recorrente os filtros e os blocos viram segmento, porte,
--     serviço e mensalidade; "visita" vira "reunião".
--
-- QUEM RECEBE o novo: todo dono/gestor/vendedor de conta que vende serviço
-- (ZAQ, Prime Eventos, Doce Mell, Ramo Capital, MGB, PC Contabilidade).
-- Idempotente: updates por chave; insert com on conflict do nothing.

update public.novidades set publico = 'servico',
  resumo = 'O app do vendedor ganhou o Raio-X: sua semana em quatro números, quem responder hoje pelo que mais urge, e o que está a um passo do contrato.',
  corpo = $txt$A aba Resultado virou Raio-X, em três blocos que rolam numa tela só.

Sua semana: tempo da primeira resposta ao lead novo (a meta é 5 minutos), propostas enviadas e as que ficaram em rascunho, toques de retorno feitos, e contratos assinados. Cada número com a cor do dia e o comparativo com a semana anterior.

Responda hoje: a fila do que mais urge. Pergunta do cliente sem resposta, proposta parada, toque da cadência vencendo hoje, e o compromisso de amanhã pra confirmar. Quem vende festa ainda tem a festa perto sem proposta. Cada linha abre a conversa no ponto certo.

Fechamentos: quem assinou, quem disse sim e falta assinar, proposta esperando o cliente, e rascunho que não saiu, cada um com o botão certo.

No pé, a confiança do dado: quantos dias o Zaq mediu, quantas vezes a conexão religou, e quais mensagens podem não ter chegado, pelo nome do cliente. Se aparecer um nome ali, confira no celular.

Seu dinheiro continua lá, no fim da tela: comissão, recebido e o que está no funil.$txt$
 where chave = 'raio-x-no-app';

update public.novidades set publico = 'servico' where chave = 'raio-x-de-segunda';

update public.novidades set publico = 'servico',
  resumo = 'O painel ganhou o Raio-X: leads, primeira resposta, propostas, contratos e compromissos do período, com filtros na língua do seu nicho.',
  corpo = $txt$O mesmo Raio-X que o vendedor tem no app, agora na sua tela, com os cortes que fazem diferença.

Em cima, o placar do período: leads (com o dia e a hora de pico), primeira resposta (mediana, separando horário comercial de noite e fim de semana), propostas enviadas e em rascunho, contratos assinados e aprovados sem assinatura, e quantos compromissos aconteceram. Cada número comparado com o período anterior.

A barra de filtros fala a língua do seu nicho. Quem vende festa corta por tipo, mês e dia da festa e convidados. Quem vende serviço por mensalidade corta por segmento, porte, cidade e serviço proposto. Os dois cortam por período, vendedor, de onde veio o cliente e a hora que chegou.

Embaixo, o que o Zaq enriquece sozinho, também pelo nicho: demanda contra agenda e ticket por tipo de festa, ou mensalidade proposta contra fechada, segmento que chega e que fecha e serviço mais proposto. Pros dois: quantos dias do lead à proposta e da proposta ao contrato, por que perdeu, e a confiança do dado no pé.

Pro "por que perdeu" funcionar, a ficha do lead ganhou dois campos: "de onde veio o cliente" e "por que perdeu" (lista de seis, na língua do nicho). O vendedor marca no app ao dar o lead como perdido.

Uma linha por vendedor, como no grupo de segunda, fecha a tela.$txt$
 where chave = 'raio-x-do-dono';

update public.novidades set publico = 'servico',
  corpo = $txt$Ao marcar um lead como perdido, o motivo deixou de ser texto livre: é uma lista de seis, na língua do que a empresa vende. Quem vende festa tem "data indisponível"; quem vende serviço por mensalidade tem "ficou com o fornecedor atual". Um toque.

Por que isso importa: o motivo é o que deixa o Zaq ajudar. "Data indisponível" vai virar lista de espera por data; "achou caro" alimenta a tabela; "ficou com o fornecedor atual" diz contra quem a proposta perdeu.

Na ficha do cliente entrou "de onde veio o cliente": WhatsApp, indicação, Instagram, manual ou outro. É a segunda pergunta da primeira resposta.$txt$
 where chave = 'motivo-de-perda-no-app';

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('raio-x-por-nicho', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'O Raio-X fala a língua do seu nicho',
 'O Raio-X passou a perguntar primeiro o que a empresa vende: quem vende festa mede festa; quem vende serviço por mensalidade mede segmento, serviço e mensalidade.',
 '/painel/raio-x',
 $txt$O Raio-X nasceu olhando uma empresa de festas e falava de festa pra todo mundo. Agora ele pergunta primeiro o que a sua empresa vende, pelo nicho escolhido em Empresa.

Quem vende festa: nada muda. Tipo, mês e dia da festa, convidados, agenda de festas, visita.

Quem vende serviço por mensalidade: os filtros viram segmento do cliente, porte, cidade e serviço proposto; os blocos viram mensalidade proposta contra fechada, segmento que chega e que fecha, e serviço mais proposto. "Visita" vira "reunião", e no "responda hoje" entra a proposta parada. O motivo de perda ganha "ficou com o fornecedor atual".

Se a sua conta ainda não escolheu o nicho, o Raio-X usa o perfil de serviço e avisa na tela: escolha o nicho em Empresa pra ele acertar o vocabulário.$txt$,
 timestamptz '2026-09-05 23:30:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'raio-x-por-nicho';
--   (os updates acima não têm volta automática: o texto anterior está na 208 e na 210)
