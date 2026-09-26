-- 380_novidade_plano_de_tratamento.sql
-- O aviso do plano de tratamento da clínica (finance/clinica_planos.py, 379),
-- seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção monta e manda).
-- QUEM RECEBE, conferido na produção em 26/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-plano-de-tratamento', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Plano de tratamento: da consulta à proposta no WhatsApp',
 'Quando o médico propõe tratamento, a recepção monta o plano (procedimentos, sessões, desconto e formas de pagamento) e manda no WhatsApp com o link. O Zaq cobra a decisão sozinho e, aceito, as parcelas viram contas a receber.',
 '/painel/clinica/planos',
 $txt$A consulta agora pode sair com a proposta do tratamento, e a decisão é cobrada sem ninguém lembrar.

COMO FUNCIONA

Ao finalizar a consulta, responda "o médico propôs tratamento? Sim". O Zaq abre o plano já com o paciente e o médico: escolha os procedimentos do catálogo, quantas sessões de cada um e o valor por sessão (vazio, vale o preço do catálogo). Dá pra pôr também um produto ou protocolo.

Depois, o desconto e as formas de pagamento: Pix à vista (com desconto, se quiser), até N vezes no cartão, e entrada + parcelas no boleto/Pix. "Salvar e enviar no WhatsApp" manda a proposta com o link, e o card vai para Plano de tratamento.

O paciente aceita pelo link (nome e forma de pagamento) ou respondendo 1 (Pix), 2 (cartão) ou 3 (parcelado). Aceito, o card vai para Fechado, cada parcela vira uma conta a receber e você recebe um aviso. Se ele fechar na recepção, é o botão "Fechou na recepção".

DESCONTO TEM DONO

A recepção dá desconto até o teto (10% no começo). Acima disso o plano espera o dono ou o gestor aprovar antes de sair. O teto, o desconto do Pix, as parcelas e a validade (7 dias) ficam em Planos de tratamento, no fim da página.

A DECISÃO É COBRADA SOZINHA

Um dia depois: "Conseguiu ver o seu plano?". Três dias depois: o lembrete com as formas de pagamento. Na véspera de vencer, a recepção é avisada. Os lembretes não dizem o procedimento, respeitam o horário de atendimento e no máximo 1 mensagem automática por dia. Se o paciente responder qualquer coisa, o automático para e o plano aparece na tela Hoje para a recepção.

A proposta descreve procedimento, sessões e preço. Nunca diagnóstico nem promessa de resultado.$txt$,
 timestamptz '2026-09-26 20:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-plano-de-tratamento';
