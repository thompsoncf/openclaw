-- O sinal de engajamento morria no CHECK, e levava o aviso do vendedor junto.
--
-- `engajou_lead` (finance/campanhas_motor.py) grava uma atividade tipo
-- 'engajamento' quando o lead abre o e-mail ou lê no WhatsApp. Só que o CHECK de
-- `tipo` nunca conheceu esse valor — a lista era ('ligacao','whatsapp','email',
-- 'reuniao','visita','nota') e ganhou 'bounce' depois. O insert violava a regra,
-- estourava, e o `except Exception: return` da função engolia.
--
-- O estrago não é a atividade perdida: o `return` acontece ANTES das duas coisas
-- que importam. O lead nunca esquentava de frio→morno, e o e-mail "🔥 fulano leu
-- seu WhatsApp" nunca era enviado pro vendedor. O recurso inteiro de "avisa quem
-- está engajando" nunca funcionou uma vez — 0 linhas com tipo='engajamento' na
-- base, contra 80 leituras de WhatsApp registradas.
--
-- Aqui só abrimos a porta pro valor que o código já usa. Sem backfill: o que não
-- foi gravado não dá pra reconstruir, e as leituras antigas já estão em
-- campanha_eventos de qualquer jeito.
alter table public.prospeccao_atividades
  drop constraint if exists prospeccao_atividades_tipo_check;

alter table public.prospeccao_atividades
  add constraint prospeccao_atividades_tipo_check
  check (tipo in ('ligacao', 'whatsapp', 'email', 'reuniao', 'visita', 'nota',
                  'bounce', 'engajamento'));
