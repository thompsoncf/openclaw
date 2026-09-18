-- 288_novidade_quem_nao_recebe_aviso.sql
-- O vigia do canal morto: a tela avisa quem não está sendo avisado.
--
-- O QUE MUDOU NA TELA, em dois lugares:
--   * Prospecção → Follow-up: uma faixa acima do card de entrega, listando as
--     pessoas que não estão recebendo, com o motivo de cada uma.
--   * Empresa → Equipe: um selo vermelho na linha da pessoa, com o motivo — é lá
--     que se corrige, porque o e-mail e o WhatsApp estão na mesma linha.
--
-- POR QUE EXISTE. Em 18/09/2026, na conta 34, o DONO recebia todo dia a cópia de
-- gestor dos 30 leads mais atrasados da casa e não recebia nada: sem e-mail
-- cadastrado, sem aparelho com push, e um número de WhatsApp que aceita o envio e
-- nunca devolve recibo. Ficou dois dias assim, e só apareceu porque alguém foi
-- procurar. O registro de envio (276 e 284) já tinha a resposta guardada; faltava
-- a pergunta.
--
-- PRA QUEM: dono e gestor. O vendedor não corrige cadastro de ninguém, e ver a
-- lista de quem não recebe não muda a rotina dele.
--
-- O PORTÃO: `servico`, o mesmo do follow-up — quem vende produto não tem a régua
-- que gera esses avisos.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('quem-nao-recebe-aviso', 'novidade', 'servico', '{dono,gestor}',
 'O painel agora avisa quando alguém da equipe parou de receber os avisos',
 'Se um vendedor (ou você) deixa de receber o aviso de follow-up — por falta de e-mail, de aparelho com push ou por um número de WhatsApp que não confirma entrega — a tela passa a dizer, em vez de esperar alguém desconfiar.',
 '/painel/follow-up',
 $txt$Medir se o aviso chegou só serve se alguém for avisado quando ele parar de chegar.

O QUE VOCÊ VAI VER

Na aba Follow-up, acima do card de entrega: uma faixa com o nome de quem não está recebendo e o motivo. Em Empresa → Equipe, um selo vermelho na linha da pessoa — é lá que se corrige, porque o e-mail e o WhatsApp dela estão na mesma linha.

Sem ninguém no vermelho, nada aparece. A faixa não existe pra decorar a tela.

AS TRÊS COISAS QUE ELE PROCURA

1. Quem não tem POR ONDE ser avisado: sem e-mail, sem WhatsApp e sem aparelho com push. Esse aparece antes mesmo do primeiro aviso se perder.
2. Quem teve aviso tentado nos últimos dias e não recebeu NENHUM, em canal nenhum. Um canal que funciona já tira a pessoa da lista: o que se procura é o silêncio completo.
3. O número de WhatsApp que aceita o envio e nunca confirma a entrega. É o mais traiçoeiro: o registro diz "enviado ✓" e parece tudo certo.

O CUIDADO QUE ELE TOMA

Se a conta INTEIRA está sem recibo — o chip religando, por exemplo —, ninguém é acusado. Marcar a equipe toda como "número errado" por causa de um problema do sistema seria culpar as pessoas por algo que não é delas, e o alerta só vale enquanto se acredita nele.

E um envio sozinho sem recibo não acusa ninguém: telefone no bolso demora a confirmar. São precisos dois, e passadas algumas horas.

DE ONDE VEIO

De um caso real, nesta semana: o dono de uma empresa recebia a cópia dos leads mais atrasados da casa e não recebia nada, em três canais ao mesmo tempo. Agora a tela pergunta sozinha.$txt$,
 timestamptz '2026-09-18 20:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'quem-nao-recebe-aviso';
