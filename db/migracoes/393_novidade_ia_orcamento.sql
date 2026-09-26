-- 393_novidade_ia_orcamento.sql
-- O aviso da etapa 3 do vendedor IA (migração 392, finance/ia_orcamento.py), seguindo a
-- seção 5 do CLAUDE.md.
--
-- PÚBLICO `visita_da_ia` (dois chips E vende festa, migração 391): é o mesmo cartão
-- Regras por número, na mesma seção de quem vende festa — o portão da tela é o mesmo.
-- PRA QUEM: dono e gestor (só a gerência liga; quem confere é avisado a cada orçamento).
-- QUEM RECEBE, conferido na produção em 26/09/2026 (só leitura):
--   34 Prime Eventos
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('ia-do-numero-monta-orcamento', 'novidade', 'visita_da_ia', '{dono,gestor}',
 'A IA do número monta o orçamento, e alguém confere antes de ir',
 'Com a regra por número, a IA monta o orçamento com o pacote e os adicionais, alguém da equipe confere com um toque, e depois da aprovação a data fica segurada 72h esperando o sinal.',
 '/painel/prospeccao/comunicacao?aba=agente',
 $txt$A IA que atende um número (Regras por número) agora pode montar o orçamento.

COMO FUNCIONA

- A IA pergunta se o cliente prefere conhecer o espaço antes ou receber um orçamento prévio. Quem pediu preço recebe o valor de referência junto com o convite pra visita.
- Com a data, o horário e o número de convidados, ela monta o orçamento com o pacote certo e os adicionais que o cliente quis, sinal de 30% e validade de 7 dias.
- O orçamento NÃO vai direto: quem confere (você escolhe na regra) recebe o aviso e, no app, toca em "Conferir e mandar", "Editar" ou "Descartar". O link sai pelo mesmo número da conversa.

DEPOIS DA APROVAÇÃO

- A data fica segurada por 72 horas esperando o sinal (o orçamento feito por vendedor segue a regra de sempre). Se o dia já tiver festa, a data não é segurada e a equipe é avisada pra decidir.
- A IA manda o valor do sinal — com o Pix copia e cola, se a empresa tiver chave Pix cadastrada — e lembra o cliente em 24h e 48h.
- Quando o cliente manda o comprovante, quem decide o sinal recebe "comprovante chegou" e confirma com o botão de sempre. A IA avisa o cliente que a data está garantida.
- Sem sinal no prazo, a data é liberada e o cliente é avisado.

Liga em Comunicação › Agente, no cartão do número: "A IA monta o orçamento".$txt$,
 timestamptz '2026-09-27 00:20:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'ia-do-numero-monta-orcamento';
