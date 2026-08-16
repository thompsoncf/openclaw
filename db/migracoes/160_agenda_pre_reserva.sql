-- Pré-reserva de data: a data só é do cliente depois do sinal.
--
-- O gerador de parcelas do orçamento de evento já escreve, na primeira linha,
-- "Sinal — confirma a reserva da data". Mas web/proposta._reservar_na_agenda
-- criava o compromisso na APROVAÇÃO, sem olhar pagamento nenhum: a proposta
-- prometia uma coisa e o sistema fazia outra.
--
-- Agora a aprovação cria uma PRÉ-RESERVA com prazo. Quando o dono confirma que o
-- sinal caiu, ela vira compromisso firme; se o prazo vence antes, a data libera
-- sozinha e ele é avisado.
--
-- Por que um STATUS novo e não uma coluna solta: as consultas do app filtram
-- `status='ativo'` em 14 lugares — lembretes, resumo do dia, feed .ics, cockpit.
-- Com um status próprio, todas elas passam a IGNORAR a pré-reserva sem precisar
-- ser tocadas, que é exatamente o certo: data provisória não é compromisso e não
-- deve virar lembrete nem entrar no calendário de ninguém. Quem precisa enxergar
-- (a tela da Agenda e as ações sobre ela) abre exceção explícita.
alter table public.eventos_agenda drop constraint if exists eventos_agenda_status_check;
alter table public.eventos_agenda add constraint eventos_agenda_status_check
    check (status in ('ativo', 'cancelado', 'pre_reservado'));

-- até quando a data fica segurada. null = compromisso normal, sem prazo correndo.
alter table public.eventos_agenda
    add column if not exists pre_reserva_ate timestamptz;

-- o job de expiração varre por aqui; índice parcial porque a esmagadora maioria
-- dos compromissos nunca é pré-reserva.
create index if not exists idx_eventos_pre_reserva
    on public.eventos_agenda (pre_reserva_ate)
 where status = 'pre_reservado';

-- O lado do orçamento (sinal_centavos/sinal_pago_em) fica na 161, separado: é
-- outra tabela e outro módulo. Junto, obrigaria todo teste da agenda a montar o
-- schema de orçamentos só pra criar um compromisso.

-- Quanto tempo a data fica segurada, POR EMPRESA: é decisão de negócio. Buffet
-- que vive de data cheia segura 2 dias; espaço que fecha com meses de
-- antecedência segura 7. 3 dias é o padrão de quem não mexer.
alter table public.agenda_config
    add column if not exists pre_reserva_dias int not null default 3;

-- rollback:
--   alter table public.eventos_agenda drop constraint if exists eventos_agenda_status_check;
--   alter table public.eventos_agenda add constraint eventos_agenda_status_check
--       check (status in ('ativo','cancelado'));
--   drop index if exists idx_eventos_pre_reserva;
--   alter table public.eventos_agenda drop column if exists pre_reserva_ate;
--   alter table public.agenda_config drop column if exists pre_reserva_dias;
