-- 281_novidade_follow_up_no_whatsapp.sql
-- O aviso de follow-up também no WhatsApp do vendedor.
--
-- O QUE MUDOU NA TELA (/painel/prospeccao, aba Follow-up): com o motor LIGADO,
-- aparece um segundo interruptor — "Avisar também no WhatsApp". Nasce em Não.
--
-- POR QUE ESTE CANAL, e por que só agora. Medido na conta 34 em 18/09/2026, no
-- segundo dia com a cobrança ligada: os três vendedores receberam push e e-mail,
-- tudo certo. O DONO não recebeu nada — a cópia de gestor dele, de 30 leads, falhou
-- nos dois canais ("nenhum aparelho com push", "membro sem e-mail cadastrado"). Ele
-- tem WhatsApp cadastrado; era o único caminho que chegava nele, e não existia.
--
-- E o canal só pôde nascer depois da trava do número da equipe (17/09): antes, o
-- chip da empresa falando com o número do vendedor virava LEAD quando ele
-- respondia. Eram nove leads assim em cinco contas, com 4.309 mensagens.
--
-- O PORTÃO: `servico`. Quem vende produto não tem funil nem follow-up.
--
-- PRA QUEM: dono e gestor. É quem liga — o vendedor recebe, não decide.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('follow-up-no-whatsapp', 'novidade', 'servico', '{dono,gestor}',
 'O aviso de follow-up agora pode ir também no WhatsApp',
 'Além do push e do e-mail, o aviso diário de follow-up pode chegar no WhatsApp de cada vendedor — pelo mesmo chip que a empresa usa nos recados internos.',
 '/painel/prospeccao',
 $txt$O aviso diário de follow-up ganhou um terceiro canal.

ONDE LIGAR

Prospecção, aba Follow-up. Com o follow-up LIGADO, aparece logo abaixo do interruptor principal: "Avisar também no WhatsApp". Nasce em Não — mandar mensagem no celular de alguém não começa ligado.

COMO FUNCIONA

A mesma mensagem do e-mail, no número de cada vendedor: quantos leads estão esperando, os primeiros nomes e o link pra abrir. Uma vez por dia, no mesmo ritmo dos outros canais — o teto diário continua valendo, então ninguém recebe uma mensagem por lead.

Sai pelo mesmo número que a empresa já usa pros recados internos. Se você tem um chip separado pra campanha, o aviso não sai por ele.

POR QUE ISSO IMPORTA MAIS DO QUE PARECE

Push depende de o vendedor ter instalado o app. E-mail depende de ter endereço cadastrado. Quem não tem nenhum dos dois simplesmente não era avisado — e isso não aparecia em lugar nenhum, porque o sistema não guardava registro de envio.

Agora guarda, e foi ele que mostrou o caso: numa empresa, o dono recebia a cópia dos leads mais atrasados da casa e ela falhava nos dois canais, todo dia, sem ninguém saber. O WhatsApp dele estava cadastrado o tempo todo.

QUEM NÃO TEM NÚMERO

Continua recebendo por e-mail e push normalmente. E fica registrado que o WhatsApp não saiu, com o motivo — nada de "ligado" que na prática não chega.$txt$,
 timestamptz '2026-09-18 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'follow-up-no-whatsapp';
