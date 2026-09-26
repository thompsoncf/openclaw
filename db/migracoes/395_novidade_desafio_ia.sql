-- 395_novidade_desafio_ia.sql
-- O aviso da etapa 4 do vendedor IA (finance/desafio_ia.py, /painel/prospeccao/desafio-ia),
-- seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `visita_da_ia` (dois chips E vende festa, migração 391): a tela só existe pra
-- quem vende festa e tem a regra por número — o mesmo portão.
-- PRA QUEM: dono e gestor (a tela é visão de gerência).
-- QUEM RECEBE, conferido na produção em 26/09/2026 (só leitura):
--   34 Prime Eventos
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('desafio-ia-x-equipe', 'novidade', 'visita_da_ia', '{dono,gestor}',
 'Desafio: a IA do número contra a equipe, mês a mês',
 'Um painel com as mesmas medidas pra IA e pra cada vendedor, por lead recebido no mês: 1ª resposta, visitas, orçamentos, contratos e o custo da IA por lead e por contrato.',
 '/painel/prospeccao/desafio-ia',
 $txt$Com a IA atendendo um número, dá pra medir se ela vende.

O QUE O PAINEL MOSTRA (por mês)

- As mesmas medidas pra IA e pra cada vendedor, por lead recebido no mês: leads novos, 1ª resposta (mediana) e quantos foram respondidos em até 5 minutos, leads com data e convidados, visitas agendadas, leads com orçamento e contratos assinados.
- O custo da IA no mês, por lead e por contrato.
- Por que a IA chamou alguém da equipe (visita, desconto, sinal, comprovante…) e quanto tempo a equipe levou pra responder o cliente depois do aviso.
- Onde a IA tende a ganhar: como a equipe responde fora do horário comercial.

Leia com cuidado: o tráfego da campanha do número da IA não é o mesmo do chip principal. A comparação indica, não prova.

Fica em Comunicação › Agente › Regras por número, no botão "Desafio IA × equipe".$txt$,
 timestamptz '2026-09-27 00:25:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'desafio-ia-x-equipe';
