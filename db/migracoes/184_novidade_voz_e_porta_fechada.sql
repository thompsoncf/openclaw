-- 184_novidade_voz_e_porta_fechada.sql
-- Os dois avisos que ficaram pra trás — e o portão que eles exigiram.
--
-- O QUE ACONTECEU
-- Duas mudanças subiram em 19/08 e nenhuma foi anunciada: o microfone no app do
-- vendedor (#501, #503) e o sumiço do atalho "Mandar no WhatsApp" (#506). A segunda
-- é grave: o vendedor abriu o app e um botão que ele usava todo dia não estava mais
-- lá. É exatamente o caso da Doce Mell que fez o sistema de avisos existir.
--
-- O PORTÃO NOVO: `canal_proprio`
-- Seguindo o passo 1 da receita da 175 — "se nenhum portão descreve quem recebeu a
-- mudança, crie o portão no código primeiro". Nenhum dos cinco servia, porque estas
-- duas mudanças não valem por RAMO de negócio: valem por CANAL.
--
--   • QR (WhatsApp da própria empresa): sem janela de 24h. O microfone aparece, e o
--     atalho pro celular sumiu porque não faz falta.
--   • Twilio / Cloud API: só responde dentro da janela de 24h. O microfone NÃO
--     aparece e o atalho CONTINUA — fechá-lo deixaria o vendedor sem meio de
--     responder.
--
-- Mandar por nicho erraria dos dois lados: prometeria microfone pra quem não tem e
-- calaria sobre um botão que sumiu pra quem perdeu. Na base de hoje 'eventos' até
-- acertaria — por coincidência, não por regra: a conta 34 tem QR e Twilio
-- configurados ao mesmo tempo.
--
-- `canal_proprio` é o primeiro portão de CONTA (os cinco antigos são de nicho).
-- `finance/novidades.PUBLICOS` continua sendo a lista que este check espelha, e o
-- teste `test_o_banco_e_o_python_conhecem_os_mesmos_publicos` cobra as duas.
--
-- QUEM RECEBE, conferido na produção em 21/08/2026 (passo 3 da receita):
--   conta 34 · MANOEL SOARES (Prime Eventos) · eventos · equipe de 5
--   conta 35 · Louana Vanessa                · eventos · equipe de 4
--   conta 23 · Rawilson Osternes             · consultoria · trabalha sozinho
--   conta  7 · João Pedro                    · sem nicho · trabalha sozinho
--
-- FORA, e cada uma por um motivo diferente:
--   conta 3  · Twilio. Lá o microfone não existe e o atalho continua no lugar —
--              avisar seria mentir duas vezes. É o caso que o portão existe pra
--              acertar, e note que ela tem 5 pessoas: o erro seria grande.
--   conta 36 · nasceu em 20/08, DEPOIS destes avisos. `listar` corta por
--              `contas.criado_em` — ninguém abre o painel pela primeira vez com
--              changelog de coisa que nunca viveu.
--
-- A conta 34 tem QR **e** Twilio configurados ao mesmo tempo; quem decide é
-- `provedor_da_conta`, a mesma função que decide se o microfone aparece. É por isso
-- que o portão pergunta a ela, e não ao nicho.
--
-- Aditivo e idempotente.

-- ────────────────────────────────────────────── 1. o portão entra no check
alter table public.novidades drop constraint if exists novidades_publico_check;
alter table public.novidades add constraint novidades_publico_check
  check (publico in ('todos','produto','servico','eventos','recorrente','canal_proprio'));

-- ────────────────────────────────────────────── 2. os dois avisos
insert into public.novidades (chave, tipo, publico, titulo, corpo, publicado_em) values

-- GANHOU o microfone. 'novidade': marca lida sozinho ao abrir a tela.
('voz-no-app-do-vendedor', 'novidade', 'canal_proprio',
 'Seu vendedor manda áudio pelo Zaq',
 $txt$O microfone entrou na conversa do lead, no app do vendedor.

Ele segura pra gravar, solta pra ouvir o que saiu, e manda — igual ao WhatsApp. O áudio chega pro cliente como mensagem de voz de verdade, com a duração certa.

E chega TRANSCRITO no histórico. Antes, áudio era um buraco no registro do lead: quem lesse a conversa depois via um anexo e nada mais. Agora o texto fica junto, e dá pra saber o que foi combinado sem ouvir nada.

O nome do vendedor vai junto. Nos 519 áudios que saíram desta conta nos 7 dias antes disso, NENHUM tinha autor — ninguém sabia quem tinha falado com o cliente.$txt$,
 timestamptz '2026-08-19 15:00:00+00'),

-- PERDEU um botão. 'mudanca': exige o "Entendi", e é isso que permite saber quem
-- já viu — a pergunta que a Doce Mell criou quando perdeu o "Fechar contrato".
('atalho-whatsapp-fechado', 'mudanca', 'canal_proprio',
 'O atalho "Mandar no WhatsApp" saiu do app',
 $txt$Seu vendedor não vai mais achar o botão que abria o WhatsApp do celular dele — na tela do lead, na proposta e na visita.

POR QUE. Em 7 dias, 1479 mensagens saíram desta conta e só 30 tinham vendedor identificado. As outras 98% saíram por fora: o Zaq registrava que a mensagem existiu, mas não sabia quem falou. Quando alguém entra de férias ou sai da empresa, o histórico do lead não conta a história — e quem assume começa do zero.

O QUE MUDA PRO VENDEDOR. Ele responde na própria conversa do Zaq, que agora tem texto e áudio. É o mesmo trabalho, no mesmo lugar onde ele já lê o que o cliente mandou.

POR QUE SÓ AGORA. O botão só saiu depois de o microfone entrar. Antes disso, fechar essa porta seria tirar dele a única forma de mandar áudio.

O atalho continua onde ele faz falta: em contas que falam pelo WhatsApp oficial existe janela de 24 horas, e fora dela o Zaq não alcança o cliente. Aqui não — o número é seu e fala a qualquer hora.$txt$,
 timestamptz '2026-08-19 21:30:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave in ('voz-no-app-do-vendedor','atalho-whatsapp-fechado');
--   alter table public.novidades drop constraint if exists novidades_publico_check;
--   alter table public.novidades add constraint novidades_publico_check
--     check (publico in ('todos','produto','servico','eventos','recorrente'));
