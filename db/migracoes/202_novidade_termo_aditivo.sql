-- 202_novidade_termo_aditivo.sql
-- O aviso do termo aditivo, seguindo a seção 5 do CLAUDE.md: PR que muda tela
-- leva o aviso, no mesmo PR. Precisa da 199 (pra_quem, resumo, link) e da 201
-- (a tabela que o aviso está anunciando).
--
-- O PORTÃO É `eventos`, e não `todos`: o contrato de locação só existe no nicho
-- de eventos (`finance.contrato.tem_contrato` -> `vendas.modo_por_nicho`), e sem
-- contrato não há o que aditar. Anunciar pra quem não tem contrato seria
-- prometer uma tela que não abre.
--
-- QUEM RECEBE, conferido na produção em 05/09/2026:
--   conta 34 · PRIME EVENTOS   4 vendedores · 2 contratos assinados (é a conta
--                              que vive o problema: o contrato nº 5 da Cláudia
--                              está assinado e travado desde 02/09)
--   conta 35 · DOCE MELL       1 vendedor · nenhum contrato assinado ainda
--
-- PRA QUEM: dono, gestor E VENDEDOR. O dono decidiu em 05/09/2026 que os três
-- fazem aditivo, e a tela mora sob /painel/servicos justamente pra caber no que
-- o vendedor já alcança. Avisar só dono e gestor repetiria, em forma de aviso, o
-- erro que o próprio recurso evitou em forma de rota.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('termo-aditivo', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'Mudou a data, o horário ou o número de convidados? Agora tem termo aditivo',
 'Contrato assinado pode ser alterado por termo aditivo: o cliente assina pelo link e o sistema atualiza a agenda, o orçamento e a cobrança.',
 '/painel/servicos',
 $txt$Até hoje, contrato assinado não podia ser mexido: o sistema respondia "faça um aditivo" e não tinha para onde mandar. A saída era refazer no Word.

Agora, no menu do contrato dentro do funil, aparece "📝 Fazer termo aditivo". A tela pergunta o que mudou, em cinco blocos — data, horário, quantidade de convidados, serviços contratados e valor — e só o que você marcar entra no documento.

O cliente recebe um link, lê um quadro de "como está → passa a ser" e assina com nome e CPF, do mesmo jeito que assina o contrato. Fica registrada a data, a hora e o IP.

QUANDO ELE ASSINA, O SISTEMA SE ATUALIZA SOZINHO: a data e o horário mudam na agenda (é o que sua equipe olha no dia), o número de convidados e o valor mudam no orçamento, e a diferença combinada vira uma conta a receber com o vencimento que você marcou.

MUDANÇA DE DATA É TRATADA À PARTE, porque é a cláusula 7 do seu contrato que manda nela. Ao escolher a data nova, a tela confere quatro coisas e escreve o resultado: se já houve outra alteração de data, se a antecedência mínima está cumprida, se o dia está livre na sua agenda (e, se não estiver, com quem choca) e se a data cabe no prazo do contrato. A taxa de reagendamento vem calculada, com um botão de zerar do lado.

Nada disso trava: são avisos. Quem decide é você.

Enquanto o cliente não assina, nada muda no sistema — e dá pra cancelar e refazer o aditivo. Depois de assinado, o contrato original ganha uma tarja dizendo que foi alterado, com link para o aditivo, para que o link antigo nunca mostre o número velho.$txt$,
 timestamptz '2026-09-05 13:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'termo-aditivo';
