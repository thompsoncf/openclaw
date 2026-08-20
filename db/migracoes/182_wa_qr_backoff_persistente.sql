-- 182 — a espera anti-guerra de sessão precisa sobreviver ao restart
--
-- Quando outro aparelho assume a credencial (440 = connectionReplaced), o serviço
-- para de reconectar na hora e vai espaçando as tentativas: 5, 10, 20, 40, 80
-- minutos (esperaPos440, em services/wa-qr/server.js). A regra está certa e foi
-- medida — sem ela a conta 23 ficou sendo substituída de 6 em 6 minutos a noite
-- inteira em 15/08.
--
-- O buraco: o contador vivia SÓ na memória do processo. Em 20/08 a enxurrada de
-- 'failed to decrypt message' que vem depois de uma substituição saturou a única
-- CPU do contêiner, o event loop travou por 25-73s, o /saude do health check do
-- Render (que desiste em 5s) não respondeu, e o Render matou a instância 7 vezes
-- numa hora. Cada morte zerava a espera, e o restaurarSessoes religava a conta
-- imediatamente, pegava o mesmo lote indecifrável e recomeçava o ciclo.
--
-- Ou seja: a proteção contra a guerra de sessão era desarmada justamente pelo
-- reinício que a guerra provocava. Aqui o contador passa a morar no banco, junto
-- com o resto do retrato da sessão (tabela criada na 158).
alter table public.wa_qr_sessao_estado
    -- quando a conta ficou órfã (substituída, ou disjuntor aberto). É o relógio
    -- de onde o esperaPos440 conta.
    add column if not exists substituida_em timestamptz,
    -- quantas retomadas seguidas já foram tentadas sem a sessão ficar de pé. É o
    -- expoente da dobra; quem zera é o tempo de pé (sessaoFirme), não o sucesso
    -- da conexão.
    add column if not exists tentativas_440 integer not null default 0;
