'use strict'
// Teste da trava de sessão única por conta (sessao-lock.js).
//
// A pergunta que só o banco responde: duas instâncias competindo pela MESMA
// conta, quantas conseguem abrir socket? Tem que ser uma. E as bordas que
// importam mais que o caminho feliz — o aluguel vencendo, o dono sumindo, a
// tabela ainda não existindo — porque é nelas que a trava vira um enfeite que
// não protege nada, ou um cadeado que trava a conta pra sempre.
//
// Não entra no pytest: precisa de Node + as dependências do serviço. Manual:
//
//     cd services/wa-qr && npm install
//     createdb wa_lock_test
//     WA_LOCK_TEST_URL=postgresql://postgres@localhost:5432/wa_lock_test \
//       node teste-sessao-lock.js

const fs = require('fs')
const path = require('path')
const { Pool } = require('pg')
const { criarTrava, TTL_MS, RENOVA_MS } = require('./sessao-lock')

const URL = process.env.WA_LOCK_TEST_URL
if (!URL) {
  console.error('Falta WA_LOCK_TEST_URL (banco de TESTE, descartável — nunca produção).')
  process.exit(2)
}

const CONTA = 35
const OUTRA = 3
const pool = new Pool({ connectionString: URL })
const MIGRACAO = path.join(__dirname, '..', '..', 'db', 'migracoes', '157_wa_qr_sessao_lock.sql')

const mudo = { info () {}, warn () {}, error () {} }
const ok = []; const bad = []
const t = (nome, cond) => (cond ? ok : bad).push(nome)
const dormir = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  await pool.query('drop table if exists wa_qr_sessao_lock cascade')
  await pool.query('drop table if exists contas cascade')
  // a migração referencia contas(id)
  await pool.query('create table contas (id bigserial primary key, nome text)')
  await pool.query('insert into contas (id, nome) values ($1,$2), ($3,$4)',
    [CONTA, 'Conta 35', OUTRA, 'Conta 3'])
  await pool.query(fs.readFileSync(MIGRACAO, 'utf8'))

  // ---- 1. duas instâncias, uma conta: só uma passa
  const velha = criarTrava(pool, mudo, { dono: 'instancia-velha' })
  const nova = criarTrava(pool, mudo, { dono: 'instancia-nova' })
  t('a primeira instância pega a conta', (await velha.pegar(CONTA)) === true)
  t('a segunda NÃO pega (é o deploy sobrepondo)', (await nova.pegar(CONTA)) === false)
  t('a primeira sabe que segura', velha.segura(CONTA) === true)
  t('a segunda sabe que não segura', nova.segura(CONTA) === false)

  // ---- 2. contas diferentes não competem entre si
  t('outra conta é independente', (await nova.pegar(OUTRA)) === true)

  // ---- 3. renovar mantém; pegar de novo é idempotente
  await velha.renovar()
  t('renovar mantém a conta', velha.segura(CONTA) === true)
  t('pegar de novo a própria conta segue ok', (await velha.pegar(CONTA)) === true)
  t('e a rival continua barrada', (await nova.pegar(CONTA)) === false)

  // ---- 4. soltar libera na hora (é isto que faz o deploy trocar em segundos)
  await velha.soltar(CONTA)
  t('depois de soltar, a nova assume', (await nova.pegar(CONTA)) === true)
  t('e a velha não segura mais', velha.segura(CONTA) === false)

  // ---- 5. dono que SUMIU (SIGKILL/OOM): o prazo vence e alguém assume.
  // Simula envelhecendo a linha em vez de esperar 60s de relógio.
  const morta = criarTrava(pool, mudo, { dono: 'instancia-morta' })
  await pool.query('delete from wa_qr_sessao_lock where conta_id=$1', [CONTA])
  t('instância morta pega a conta', (await morta.pegar(CONTA)) === true)
  const viva = criarTrava(pool, mudo, { dono: 'instancia-viva' })
  t('enquanto o prazo vale, ninguém rouba', (await viva.pegar(CONTA)) === false)
  await pool.query("update wa_qr_sessao_lock set expira_em = now() - interval '1 second' where conta_id=$1", [CONTA])
  t('prazo vencido: a próxima instância assume', (await viva.pegar(CONTA)) === true)

  // ---- 6. quem perdeu o aluguel TEM que largar o socket (senão viram dois)
  const perdidas = []
  const distraida = criarTrava(pool, mudo, {
    dono: 'instancia-distraida', aoPerder: (c) => perdidas.push(c)
  })
  await pool.query('delete from wa_qr_sessao_lock where conta_id=$1', [OUTRA])
  await distraida.pegar(OUTRA)
  // outra instância rouba a conta (prazo vencido do ponto de vista dela)
  await pool.query("update wa_qr_sessao_lock set dono='ladra', expira_em = now() + interval '1 minute' where conta_id=$1", [OUTRA])
  await distraida.renovar()
  t('renovar detecta que perdeu a conta', perdidas.includes(OUTRA))
  t('e para de considerar que segura', distraida.segura(OUTRA) === false)

  // ---- 7. batimento renova sozinho, sem ninguém chamar
  const batendo = criarTrava(pool, mudo, { dono: 'instancia-batendo', ttlMs: 1000, renovaMs: 120 })
  await pool.query('delete from wa_qr_sessao_lock where conta_id=$1', [CONTA])
  await batendo.pegar(CONTA)
  const prazo1 = (await pool.query('select expira_em from wa_qr_sessao_lock where conta_id=$1', [CONTA])).rows[0].expira_em
  await dormir(400)
  const prazo2 = (await pool.query('select expira_em from wa_qr_sessao_lock where conta_id=$1', [CONTA])).rows[0].expira_em
  t('o batimento empurra o prazo sozinho', new Date(prazo2) > new Date(prazo1))
  t('e a conta continua nossa', batendo.segura(CONTA) === true)

  // ---- 8. soltarTudo (SIGTERM) libera todas de uma vez
  await pool.query('delete from wa_qr_sessao_lock where conta_id=$1', [OUTRA])
  t('a mesma instância segura duas contas', (await batendo.pegar(OUTRA)) === true)
  await batendo.soltarTudo()
  const sobrou = (await pool.query('select count(*)::int n from wa_qr_sessao_lock where dono=$1', ['instancia-batendo'])).rows[0].n
  t('soltarTudo não deixa aluguel pra trás', sobrou === 0)

  // ---- 9. sem a tabela (web ainda não migrou), segue SEM trava em vez de
  // deixar o WhatsApp de todo mundo no chão
  await pool.query('drop table wa_qr_sessao_lock')
  const semTabela = criarTrava(pool, mudo, { dono: 'instancia-sem-tabela' })
  t('sem a tabela, não bloqueia o serviço', (await semTabela.pegar(CONTA)) === true)
  t('e soltar não estoura', await semTabela.soltar(CONTA).then(() => true).catch(() => false))

  // ---- 10. string x número: o restaurarSessoes lê conta_id do Postgres (bigint
  // vira STRING) e as rotas fazem parseInt. A mesma conta pelos dois caminhos não
  // pode virar duas entradas — senão `segura()` responde diferente conforme quem
  // pergunta, e o portão do iniciarSessao passa a mentir.
  await pool.query('create table if not exists wa_qr_sessao_lock (conta_id bigint primary key, dono text not null, expira_em timestamptz not null, criado_em timestamptz default now(), atualizado timestamptz default now())')
  const tipos = criarTrava(pool, mudo, { dono: 'instancia-tipos' })
  t('pega com string', (await tipos.pegar(String(CONTA))) === true)
  t('e segura() responde igual pra número', tipos.segura(CONTA) === true)
  t('e pra string também', tipos.segura(String(CONTA)) === true)
  await tipos.soltar(CONTA)
  t('soltar com número solta o que entrou como string', tipos.segura(String(CONTA)) === false)

  // ---- 11. a desistência tem PRAZO e reconcilia sozinha.
  // Na estreia em produção o wa-qr subiu antes de o web criar a tabela e as três
  // contas religaram sem trava. Se a desistência fosse permanente, a instância
  // rodaria desprotegida até o próximo deploy — que é justamente o buraco que
  // isto fecha. Três desfechos possíveis quando a tabela aparece:
  const CRIA_TABELA = `create table wa_qr_sessao_lock (
      conta_id bigint primary key, dono text not null, expira_em timestamptz not null,
      criado_em timestamptz default now(), atualizado timestamptz default now())`

  // (a) a tabela aparece e a conta estava livre -> passa a ficar protegida
  await pool.query('drop table if exists wa_qr_sessao_lock')
  const tardia = criarTrava(pool, mudo, { dono: 'instancia-tardia', revalidaMs: 60 })
  t('sem tabela, abre socket assim mesmo', (await tardia.pegar(CONTA)) === true)
  t('e sabe que está rodando sem trava', tardia.semTrava(CONTA) === true)
  t('sem trava não é o mesmo que segurar', tardia.segura(CONTA) === false)
  await pool.query(CRIA_TABELA)
  await dormir(80)
  await tardia.revalidarDegradadas()
  t('tabela apareceu: a conta vira protegida', tardia.segura(CONTA) === true)
  t('e sai da lista de degradadas', tardia.semTrava(CONTA) === false)

  // (b) enquanto a tabela não existe, segue degradado (não desiste, não protege)
  await pool.query('drop table wa_qr_sessao_lock')
  const insistente = criarTrava(pool, mudo, { dono: 'instancia-insistente', revalidaMs: 60 })
  await insistente.pegar(OUTRA)
  await dormir(80)
  await insistente.revalidarDegradadas()
  t('tabela ainda ausente: continua degradada, sem estourar', insistente.semTrava(OUTRA) === true)

  // (c) a tabela aparece e a conta é de OUTRA instância -> a nossa é a intrusa
  // e tem que largar a sessão, senão viram duas com a mesma credencial
  const largou = []
  await pool.query(CRIA_TABELA)
  await pool.query(`insert into wa_qr_sessao_lock (conta_id, dono, expira_em)
                    values ($1, 'a-legitima', now() + interval '5 minutes')`, [OUTRA])
  const intrusa = criarTrava(pool, mudo, {
    dono: 'instancia-intrusa', revalidaMs: 60, aoPerder: (c) => largou.push(c)
  })
  await pool.query('drop table wa_qr_sessao_lock')          // força o caminho degradado
  await intrusa.pegar(OUTRA)
  await pool.query(CRIA_TABELA)
  await pool.query(`insert into wa_qr_sessao_lock (conta_id, dono, expira_em)
                    values ($1, 'a-legitima', now() + interval '5 minutes')`, [OUTRA])
  await dormir(80)
  await intrusa.revalidarDegradadas()
  t('conta é de outra instância: larga a sessão', largou.includes(OUTRA))
  t('e não rouba o aluguel alheio',
    (await pool.query('select dono from wa_qr_sessao_lock where conta_id=$1', [OUTRA])).rows[0].dono === 'a-legitima')

  // ---- 12. banco fora do ar NÃO pode virar "pode abrir socket"
  const poolMorto = { query: async () => { throw new Error('connection refused') } }
  const semBanco = criarTrava(poolMorto, mudo, { dono: 'instancia-sem-banco' })
  t('sem banco, recusa abrir socket', (await semBanco.pegar(CONTA)) === false)

  // ---- 13. o prazo tem que sobreviver à pior travada do event loop
  //
  // O batimento roda no MESMO event loop de tudo o mais. Quando a CPU satura, ele
  // não roda — e o prazo corre no relógio do Postgres, que não sabe que a gente
  // parou. Medido no wa_qr_log em 24/08/2026, doze horas: 128 travadas, 11
  // instâncias mortas pelo health check, e a pior travada de 59.915ms contra um
  // TTL de 60.000ms. Faltaram 85 milésimos pro aluguel vencer com o socket vivo —
  // e dois sockets na mesma credencial é o que tira o aparelho da lista de
  // dispositivos do WhatsApp (foi o que matou a conta 35).
  const PIOR_TRAVADA_MEDIDA_MS = 59915
  t('o prazo cobre a pior travada já medida, com folga de 2x',
    TTL_MS >= PIOR_TRAVADA_MEDIDA_MS * 2)
  t('e o batimento cabe várias vezes dentro do prazo (uma falha não derruba)',
    TTL_MS / RENOVA_MS >= 4)
  t('mas o prazo não é tão longo a ponto de prender a conta numa queda dura',
    TTL_MS <= 300000)

  ok.forEach((n) => console.log('  ok   ' + n))
  bad.forEach((n) => console.log('  FALHOU  ' + n))
  console.log(`\n${ok.length} ok, ${bad.length} falhou`)
  await pool.end()
  process.exit(bad.length ? 1 : 0)
})().catch((e) => { console.error('ERRO NO TESTE:', e); process.exit(1) })
