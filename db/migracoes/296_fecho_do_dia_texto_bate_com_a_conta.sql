-- 296_fecho_do_dia_texto_bate_com_a_conta.sql
-- O aviso publicado do fecho do dia prometia uma conta que o número não fazia.
--
-- O QUE MUDOU: duas frases da novidade 'fecho-do-dia' (migração 294), que ficam
-- na tela Novidades e no site (`GET /novidades.json`).
--
--   1. "ou o cliente voltando a falar" — o texto dizia que isso conta como
--      tratado, e `esteira.resumo` nunca somou: `tratou` é falou + moveu +
--      fechou. O 'cliente_voltou' TIRA o lead da esteira (quem respondeu deixou
--      de ser lead parado), mas não é crédito do vendedor, que é o que a linha do
--      placar mede. O texto do aviso que sai por WhatsApp e e-mail foi corrigido
--      junto, em finance/esteira.py.
--   2. "Sem push: é leitura de fim de dia, não interrupção" — verdade por quatro
--      horas. Em 19/09 o dono pediu os três canais ("sempre nos 3 canais de
--      comunicação ok?? whatts + email + push") e a 295 entregou. Uma novidade
--      que descreve o que o sistema NÃO faz mais é pior que nenhuma: quem lê
--      acha que o push falhou.
--
-- POR QUE MEXER NUM AVISO JÁ PUBLICADO. Ele não é registro histórico — é a
-- explicação que o cliente lê HOJE pra entender o que recebe. `publicado_em` não
-- muda, então ninguém é notificado de novo.
--
-- Idempotente: `replace` de texto exato, rodar de novo não faz nada.

update public.novidades
   set corpo = replace(corpo,
         'o card movido à mão, ou o cliente voltando a falar.',
         'o card movido à mão, ou a venda fechada. Cliente que voltou a falar sai da esteira, mas não entra nesta conta — quem respondeu deixou de ser lead parado sozinho.')
 where chave = 'fecho-do-dia';

update public.novidades
   set corpo = replace(corpo,
         'Sem push: é leitura de fim de dia, não interrupção.',
         'Pelos três canais: WhatsApp, e-mail e push.')
 where chave = 'fecho-do-dia';

-- rollback: não tem volta automática — é texto. Pra reverter, rode o `replace`
--   ao contrário, ou reinsira o corpo original da 294.
