-- 332_novidade_anuncios_e_perdidos.sql
-- A aba Anúncios no app da Equipe, a tela Anúncios no computador (a antiga
-- Origens) e o "Por que perdemos" que abre os leads. Mockup aprovado pelo dono
-- em 24/09/2026 (docs/mockups/anuncios_e_perdidos.html), com as decisões:
-- nome "Anúncios", o gestor pode corrigir o motivo lido, e as duas ideias entram
-- (a data que abriu e o ponto em que o cliente parou).
--
-- O QUE MUDOU:
--   * /cockpit/anuncios — aba nova (dono e gestor): um cartão por anúncio, com
--     quantos não eram cliente e quantos chegaram fora do horário; tocar abre o
--     porquê daquele criativo.
--   * /painel/origens vira "Anúncios", com as duas colunas novas e a linha que
--     abre embaixo. A rota não muda.
--   * "Por que perdemos" (na Visão e dentro de cada anúncio): cada motivo abre os
--     leads perdidos por ele, com a frase do cliente e o botão da conversa. Data
--     indisponível mostra as datas que pediram. O gestor corrige o motivo lido.
--   * A leitura das conversas passa a dizer em que ponto o cliente parou e, de
--     quem não era cliente, o que queria (migração 331).
--
-- PRA QUEM: dono e gestor, nas contas que vendem serviço (`servico`) — é o
-- mesmo alcance da Visão (329) e da Origens. Produto não tem funil nem anúncio
-- medido aqui. Vendedor não tem essas telas.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('anuncios-e-perdidos', 'novidade', 'servico', '{dono,gestor}',
 'Anúncios: de qual anúncio veio cada lead, e por que ele não fechou',
 'Uma aba nova no app mostra, por anúncio, quem não era cliente e quem chegou fora do horário. E cada motivo de perda agora abre os leads, com a frase do cliente.',
 '/cockpit/anuncios',
 $txt$Duas mudanças pra quem cuida da venda e pra quem cuida do anúncio.

ANÚNCIOS, NO APP E NO COMPUTADOR

O app da Equipe ganhou a aba Anúncios. Cada anúncio vira um cartão: conversas, visitas, vendas e duas marcas novas — quantos não eram cliente e quantos chegaram fora do horário. O anúncio que traz muita gente que nunca vai comprar fica em vermelho sozinho. Tocando no cartão, abre o porquê daquele anúncio: por que perdemos, quem não era cliente, o que pedem e quando chegam.

No computador, a tela Origens passou a se chamar Anúncios e ganhou as mesmas duas colunas. Clicando na linha, o porquê abre embaixo dela.

Pra um lead aparecer com o anúncio certo, a mensagem pronta do anúncio precisa levar o código entre colchetes, com cerquilha: "Olá! Quero saber sobre o espaço. [#CAS-01]". Um código por criativo; o sistema descobre os novos sozinho.

POR QUE PERDEMOS, AGORA COM O LEAD

Cada motivo do "Por que perdemos" virou um link. Ele abre os leads perdidos por aquele motivo, com a frase do cliente que explica a perda, quem decidiu o motivo (a equipe ou a leitura da conversa) e o botão pra abrir a conversa.

Quando o motivo foi lido da conversa e está errado, o gestor corrige ali mesmo, e o número da tela passa a contar o motivo certo.

A leitura da conversa também diz em que ponto o cliente parou — antes do preço, depois do preço ou depois da proposta — e, de quem não era cliente, o que a pessoa queria: emprego, oferecer serviço, alugar um item avulso, doação.

Pra quem vende festa, "data indisponível" mostra as datas que os clientes pediram e a casa não tinha. Com "Festas por dia" preenchido em Empresa, quem pediu uma data que abrir é avisado pela lista de espera — agora também quando o motivo foi lido da conversa.$txt$,
 timestamptz '2026-09-24 18:05:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'anuncios-e-perdidos';
