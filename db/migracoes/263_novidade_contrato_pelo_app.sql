-- 263_novidade_contrato_pelo_app.sql
-- O vendedor passa a mandar o contrato pelo celular.
--
-- O QUE MUDOU NA TELA (/cockpit/orcamentos/<id>)
-- Nasce o bloco "Contrato" na proposta, com três estados e os botões:
--     Contrato nº 8 · Ainda não foi enviado.        (coral)  → Mandar na conversa
--     Contrato nº 8 · Enviado há N dias, sem assin. (âmbar)  → Reenviar na conversa
--     Contrato nº 8 assinado — em 14/09             (verde)
-- Mais "Mandar por e-mail" (quando o cliente tem e-mail) e "Abrir o contrato".
--
-- A MEDIÇÃO QUE MOTIVOU, conta 34, 14/09/2026 — os 6 contratos assinados:
--     parado em casa (criado -> enviado)      35 dias somados
--     esperando o cliente (enviado -> assin.)  1 dia somado
-- Todo cliente assinou no MESMO DIA em que recebeu. A demora nunca foi do
-- cliente. E a causa apareceu ao procurar o botão: até aqui o app não tinha
-- NADA sobre contrato — ele nascia na aprovação e só o DESKTOP sabia mandar.
-- O vendedor que trabalha no celular não tinha por onde.
--
-- O CARIMBO DE `enviado_em` vale pros DOIS caminhos (conversa e e-mail). O
-- desktop só carimbava no e-mail; nesta casa o canal principal é a conversa
-- (QR), e sem o carimbo o Raio-X seguiria anunciando "nunca enviado" pra um
-- contrato que o cliente já tem na mão. Reenviar NÃO reinicia o relógio:
-- `enviado_em` é quando o cliente passou a esperar, e se o reenvio recarimbasse,
-- bastaria reenviar pra uma pendência de 35 dias parecer nova.
--
-- O PORTÃO: `eventos`. O bloco só existe onde existe contrato pra assinar —
-- `contrato.tem_contrato`, que hoje é verdadeiro só no modo evento (§6). No
-- recorrente a proposta aprovada vai direto pra "Fechar", e o bloco não nasce.
--
-- PRA QUEM: vendedor, gestor e dono — os três abrem a proposta pelo app.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('contrato-pelo-app', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'Dá pra mandar o contrato pelo celular',
 'A proposta aprovada passou a mostrar o contrato dentro do aplicativo, com um botão pra mandar na conversa ou por e-mail — antes isso só existia no computador.',
 '/cockpit/orcamentos',
 $txt$Depois que o cliente aprova a proposta, o contrato já nasce pronto. Só que até agora ele só podia ser enviado pelo computador — quem trabalha no celular não tinha por onde, e o contrato ficava esperando alguém sentar na frente do desktop.

Medimos quanto isso custava. Nos contratos já assinados desta casa, o tempo somado esperando o cliente assinar foi de UM dia: as pessoas assinam no mesmo dia em que recebem. O tempo parado aqui dentro, com o contrato pronto e não enviado, foi de 35 dias. A demora nunca foi do cliente.

AGORA, NA TELA DA PROPOSTA, tem o bloco "Contrato". Ele diz em que pé está, com todas as letras:

"Ainda não foi enviado" — em vermelho. A bola é sua.
"Enviado há 3 dias, sem assinatura" — aí sim cabe cobrar o cliente.
"Contrato nº 8 assinado" — em verde, com a data.

E os botões: Mandar na conversa (vai pelo WhatsApp da empresa, na conversa que já existe), Mandar por e-mail, e Abrir o contrato pra você mesmo conferir antes.

O cliente lê e assina pelo link, do celular dele. Não precisa imprimir, não precisa juntar todo mundo.

UM DETALHE QUE IMPORTA: reenviar não zera a contagem. "Enviado há 3 dias" continua contando desde a PRIMEIRA vez que saiu — que é quando o cliente passou a esperar de verdade.$txt$,
 timestamptz '2026-09-15 21:05:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'contrato-pelo-app';
