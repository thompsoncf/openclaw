-- 221_novidade_origens.sql
-- O aviso da tela de Origens (CLAUDE.md §5: PR que muda tela leva o aviso).
--
-- PÚBLICO 'canal_proprio', e não um nicho (§6). A tela mede o que chega pelo
-- WhatsApp da própria empresa, conectado por QR — é onde o código do anúncio
-- viaja no texto da mensagem pronta. Quem está na API oficial (Twilio, Cloud)
-- recebe o anúncio por outro caminho e não usa este; mandar por nicho erraria
-- dos dois lados, prometendo a tela a quem não pode usá-la e calando pra quem
-- pode. Mesmo portão que os avisos do microfone e do atalho de WhatsApp usaram.
--
-- PRA QUEM: dono e gestor. O vendedor não recebe — a tela não muda a rotina
-- dele, e quem decide verba de anúncio não é ele.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('origens-do-lead', 'novidade', 'canal_proprio', '{dono,gestor}',
 'Origens: de qual anúncio veio o lead, e o que ele virou',
 'Uma tela nova mostra de onde vieram as conversas e o que aconteceu com cada uma: quantas foram atendidas, em quanto tempo, quantas marcaram visita, quantas compareceram e quantas viraram venda com sinal pago.',
 '/painel/origens',
 $txt$Até agora o Zaq sabia que a conversa chegou, mas não de onde ela veio. Anúncio, indicação e quem achou pelo Google caem todos no mesmo WhatsApp, e separar isso era impossível.

Agora dá. Quem cuida do tráfego põe um código na mensagem que já vem pronta no anúncio — algo como "Olá! Quero saber sobre o espaço. [#A3]", um código por criativo. O cliente aperta enviar sem pensar no assunto, o Zaq lê o código, carimba no lead e apaga da mensagem. O vendedor nunca vê o código; a tela sabe de onde veio pra sempre.

A tela é a Origens, no menu. Uma linha por código, mostrando quantas conversas ele trouxe, quantas foram atendidas, em quanto tempo veio a primeira resposta, quantas marcaram visita, quantas compareceram e quantas fecharam com sinal pago — com o faturamento ao lado. O recorte de tempo vai de sete dias a período escolhido na mão, pra bater com a semana que a agência fechou no painel dela.

Uma coisa a tela não faz, de propósito: não calcula custo por lead, custo por venda nem retorno sobre o anúncio. Esses números dependem do investimento, que é medida de quem cuida do tráfego. O Zaq entrega a metade que só ele tem — o que aconteceu depois da conversa — com o código do lado, e cada um cruza com o que já mede.

E ela avisa quando não sabe. A linha "sem código" junta quem chegou por indicação com quem veio do anúncio e apagou o texto antes de enviar: são coisas diferentes que o sistema não tem como separar, e dizer que tem seria pior do que não medir. Do mesmo jeito, quando visita que já aconteceu está sem resposta no Cockpit, a tela mostra quantas são antes de mostrar a taxa de comparecimento.

O funil só enche depois que os códigos estiverem nos anúncios. Antes disso a tela abre explicando o formato, em vez de mostrar zero sem dizer por quê.$txt$,
 timestamptz '2026-09-07 15:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'origens-do-lead';
