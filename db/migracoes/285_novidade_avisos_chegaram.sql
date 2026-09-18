-- 285_novidade_avisos_chegaram.sql
-- O card "Como os avisos chegaram", na aba Follow-up.
--
-- O QUE MUDOU NA TELA (/painel/follow-up, com o follow-up LIGADO): abaixo dos
-- interruptores aparece um card com os últimos 30 dias, uma linha por canal —
-- WhatsApp, push e e-mail — e, no WhatsApp, a leitura por vendedor.
--
-- POR QUE 30 DIAS: escolha do dono em 18/09/2026. Sete dias responde "esta semana
-- funcionou"; trinta responde "dá pra confiar nisso", que é a pergunta dele.
--
-- POR QUE SÓ DONO E GESTOR: o vendedor ver a própria taxa de leitura é justo; ver
-- a dos colegas vira placar, e a régua toda tem o cuidado de não virar fofoca
-- sobre ninguém (é por isso que o gestor entra JUNTO no aviso, e não no lugar do
-- vendedor).
--
-- O PORTÃO: `servico`, o mesmo da 281. Quem vende produto não tem funil nem
-- follow-up, logo não tem aviso pra medir.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('como-os-avisos-chegaram', 'novidade', 'servico', '{dono,gestor}',
 'Agora dá pra ver se o aviso de follow-up chegou e se foi lido',
 'A aba Follow-up passou a mostrar, nos últimos 30 dias, quantos avisos chegaram e quantos foram lidos em cada canal — com recibo de verdade no WhatsApp e o toque na notificação no push.',
 '/painel/follow-up',
 $txt$Saber que o aviso saiu era metade da resposta. A outra metade chegou.

O QUE O CARD MOSTRA

Na aba Follow-up, logo abaixo dos interruptores: uma linha por canal, últimos 30 dias.

No WHATSAPP, recibo de verdade, vindo do aparelho: quantos foram entregues (✓✓) e quantos foram lidos (👀) — o mesmo sinal que o card de desempenho da campanha já usava pra mensagem de cliente, agora também pro aviso interno. E embaixo, a leitura por vendedor, porque "16 de 21 lidos" não diz quem não está lendo, que é justamente a pergunta de quem cobra.

No PUSH, quantos aparelhos aceitaram e quantos vendedores ABRIRAM. Abrir aqui é o toque na notificação: quem toca abriu o painel. O navegador não sabe dizer quem só viu na tela de bloqueio, então o card diz "abriram" e não "leram" — a palavra diz exatamente o que foi medido.

NO E-MAIL, TRAVESSÃO — E ISSO É DE PROPÓSITO

O e-mail mostra quantos saíram, e travessão no resto. Medir abertura de e-mail exige um pixel escondido na mensagem, e hoje o Gmail pré-carrega imagem no proxy dele e o Apple Mail faz o mesmo por padrão: o número mediria quantos provedores pré-carregaram, não quantas pessoas abriram. Ficaria perto de 100% e seria falso.

Um número alto e falso é pior que número nenhum. Por isso travessão, e não zero — zero se leria como "ninguém abriu".

“SEM RECIBO” NÃO É “NÃO RECEBEU”

O WhatsApp deixa a pessoa desligar a confirmação de leitura. Quem desliga nunca gera o 👀, só o ✓✓ — o aviso chegou e foi lido, o recibo é que não existe. Por isso "sem recibo" é uma coluna própria no card, e não some dentro de "não entregue".

QUEM VÊ

Dono e gestor. O card não aparece pro vendedor.$txt$,
 timestamptz '2026-09-18 15:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'como-os-avisos-chegaram';
