-- 289_novidade_historico_do_aviso.sql
-- O histórico do aviso, pessoa por pessoa, dentro do card do Follow-up.
--
-- O QUE MUDOU NA TELA (/painel/follow-up): a barra "Leitura no WhatsApp, por
-- vendedor" virou uma LISTA que abre. Fechada, cada linha já diz se a pessoa está
-- recebendo, quantos leads o aviso cobrou dela e qual foi o sinal mais forte que
-- voltou. Aberta, mostra hora e estado de cada passo, em três colunas — WhatsApp,
-- push e e-mail.
--
-- POR QUE. Pedido do dono em 18/09/2026, mostrando o card do lead na campanha:
-- "quero que lá no follow-up fique assim, só que adapte as informações do vendedor
-- com as notificações". A barra dizia a taxa e escondia o caminho; ele pediu o
-- caminho.
--
-- O QUE NÃO FOI COPIADO do card do lead: o "Abriu 👁" do e-mail. O pixel existe e
-- funciona, mas o proxy de imagem do Gmail e do Apple Mail busca sozinho — medido
-- nesta base, 62 das 69 "aberturas" aconteceram em menos de 1 minuto depois do
-- envio. É por isso que a abertura de e-mail já não entra em balde nenhum do "Quem
-- atacar", e não entra aqui.
--
-- PRA QUEM: dono e gestor, como o card que a abriga.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('historico-do-aviso', 'novidade', 'servico', '{dono,gestor}',
 'O aviso de cada vendedor agora abre e mostra o caminho inteiro',
 'Na aba Follow-up, a lista de pessoas abre e mostra hora e estado de cada aviso, em três colunas — WhatsApp, push e e-mail —, do mesmo jeito que o card do lead já mostrava a régua da campanha.',
 '/painel/follow-up',
 $txt$A taxa dizia quanto; agora dá pra ver o caminho.

O QUE MUDOU

No card "Como os avisos chegaram", a barra de leitura por vendedor virou uma lista que abre.

FECHADA, cada linha já responde o que interessa: se a pessoa está recebendo, quantos leads o aviso de hoje cobrou dela, e o sinal mais forte que voltou — leu, entregue, abriu o push, ou saiu e não voltou nada.

ABERTA, mostra o caminho: hora e estado de cada aviso, em três colunas.

  WhatsApp    Enviado · Entregue ✓✓ · Leu 👀 — recibo de verdade, do aparelho
  Push        Enviado · Abriu — "abriu" é o toque na notificação
  E-mail      Enviado — e só

Sete dias abertos; o resto do mês fica atrás de "ver os 30 dias". O aviso é diário, e trinta dias escancarados viram quase noventa linhas por pessoa.

POR QUE O E-MAIL NÃO TEM "ABRIU"

O card do lead, nas campanhas, mostra "Abriu 👁" no e-mail. Aqui não, de propósito. Esse número vem de um pixel escondido na mensagem, e o proxy de imagem do Gmail e do Apple Mail busca esse pixel sozinho, sem ninguém abrir nada.

Está medido nesta base: 62 das 69 "aberturas" aconteceram em menos de um minuto depois do envio. É por isso que a abertura de e-mail também não conta no "Quem atacar" — usar isso mandaria você cobrar quem nunca leu.

O TESTE APARECE, MARCADO

O que sai pelo botão "Testar agora" entra na linha do tempo com a etiqueta "teste". Ele continua fora das contas do card — mas esconder o que realmente chegou no celular do vendedor seria esconder metade da história do dia.$txt$,
 timestamptz '2026-09-19 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'historico-do-aviso';
