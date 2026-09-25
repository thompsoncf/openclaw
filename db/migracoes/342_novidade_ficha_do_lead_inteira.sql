-- 342_novidade_ficha_do_lead_inteira.sql
-- A ficha do lead (a janela que abre no card) volta a mudar a situação, e abre
-- inteira na tela.
--
-- DE ONDE VEIO (25/09/2026): print do dono no Follow-up da Prime — a vendedora
-- tocava numa situação e recebia "Deu um erro do nosso lado". Duas coisas:
--
--   * desde 11/09 TODA troca de situação pelo painel dava erro (arrastar no
--     quadro, a janela, a ficha) — consertado no #839, sem aviso, porque a tela
--     prometia e não cumpria. Este aviso diz que voltou;
--   * a janela abria no espaço que sobrava embaixo do botão, e com o cartão no
--     meio da tela cortava "Encerrar" e o histórico, sem barra pra rolar.
--
-- PÚBLICO `servico`: a janela é do Funil, que toda conta que vende serviço tem
-- (e a Comunicação e, nos eventos, o Follow-up). Vai pro VENDEDOR também — é a
-- ferramenta de trabalho dele e foi ele quem ficou duas semanas sem conseguir
-- mudar a situação pelo painel.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('ficha-do-lead-inteira-e-situacao-volta', 'mudanca', 'servico', '{dono,gestor,vendedor}',
 'A ficha do lead abre inteira e volta a mudar a situação',
 'A janela do lead agora abre sempre inteira na tela, e mudar a situação por ela voltou a funcionar.',
 '/painel/prospeccao',
 $txt$Duas correções na ficha do lead — a janela que abre quando você toca no card.

MUDAR A SITUAÇÃO VOLTOU A FUNCIONAR

Desde 11/09, trocar a situação pelo painel (arrastar o card no quadro ou tocar num botão da ficha) mostrava "Deu um erro do nosso lado" e o lead ficava onde estava. O app do vendedor não foi afetado. Já está consertado: pode mover os leads pelo painel de novo. Nenhum dado se perdeu.

E se algum erro voltar a aparecer, ele agora fica registrado com a tela e o horário — dá pra investigar sem depender de print.

A FICHA ABRE INTEIRA

A janela abria no espaço que sobrava embaixo do botão, e com o card no meio da tela cortava "Encerrar" e o histórico. Agora ela abre embaixo se couber, em cima se couber lá, e se não couber em nenhum dos dois ela sobe pra dentro da tela, inteira. O botão verde de suporte some enquanto ela está aberta, pra não cobrir o canto.$txt$,
 timestamptz '2026-09-25 10:00:00+00')
on conflict (chave) do nothing;
