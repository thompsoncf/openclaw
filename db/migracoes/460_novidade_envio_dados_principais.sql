-- 460_novidade_envio_dados_principais.sql
-- Proposta e contrato só saem com os dados principais preenchidos (regra do dono
-- em 01/10/2026, depois de o contrato do orçamento nº 47 da Prime chegar no
-- cliente com "Campos sem valor: cliente.doc"), seguindo a seção 5 do CLAUDE.md.
--
-- O QUE MUDOU NA TELA: os botões de mandar (e-mail, conversa, WhatsApp, copiar
-- link, o "Conferir e mandar" do orçamento da IA) não mandam enquanto faltar nome
-- do cliente, CPF ou CNPJ, data e horário do evento (no orçamento de evento) ou a
-- forma de pagamento de alguma parcela. No lugar, aparece o que falta e onde
-- preencher. A venda de estande (Outlet Chic) não muda.
--
-- DUAS LINHAS, porque o alcance é diferente por papel:
--   * dono e gestor: público `servico` — quem monta orçamento no painel;
--   * vendedor: público `orcamento_evento_app` (o portão da 457) — quem monta no
--     app. O vendedor do app de estandes não tem a tela e não recebe.
-- Sem resumo: é regra interna de envio, não vai pro site.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, link, corpo, publicado_em) values
('envio-dados-principais', 'mudanca', 'servico', '{dono,gestor}',
 'Proposta e contrato só saem com os dados principais',
 '/painel/servicos',
 $txt$Nem a proposta nem o contrato saem mais com dado principal em branco. Antes de mandar, o sistema confere:

- nome do cliente;
- CPF ou CNPJ — vale o do orçamento, o da aba Clientes ou o da ficha do lead;
- data e horário de início do evento (só no orçamento de evento);
- a forma de pagamento de cada parcela.

Se faltar algo, o botão de mandar não manda: aparece o que falta e onde preencher. Foi o que aconteceu com um contrato que chegou no cliente avisando "campo sem valor" porque o CPF não tinha sido informado.$txt$,
 timestamptz '2026-10-01 22:00:00+00'),
('envio-dados-principais-app', 'mudanca', 'orcamento_evento_app', '{vendedor}',
 'Antes de mandar, o app confere os dados do cliente',
 '/cockpit',
 $txt$A proposta e o contrato só saem do app com nome, CPF ou CNPJ, data e horário do evento e a forma de pagamento de cada parcela preenchidos.

Se faltar algo, no lugar dos botões de mandar aparece o que falta, com o atalho "Preencher na ficha" (nome e CPF) ou "Editar o orçamento" (evento e parcelas). O CPF que você anota na ficha do lead já vale.$txt$,
 timestamptz '2026-10-01 22:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave in ('envio-dados-principais','envio-dados-principais-app');
