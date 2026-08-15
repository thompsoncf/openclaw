'use strict'
// A trava vale de verdade DENTRO do iniciarSessao?
//
// O teste-sessao-lock.js prova que o módulo da trava funciona. Isso não prova
// nada sobre o serviço: uma trava perfeita ligada em lugar nenhum protege zero.
// Aqui o alvo é o portão — com a conta alugada por outra instância, o
// iniciarSessao NÃO pode chegar a abrir socket.
//
// Dá pra testar sem tocar no WhatsApp porque o portão fica ANTES de tudo: antes
// de ler credencial, antes do makeWASocket. Se um dia alguém mover a verificação
// pra depois, este teste passa a estourar rede de verdade e falhar — que é
// exatamente o alarme desejado.
//
// Manual (não entra no pytest):
//
//     cd services/wa-qr && npm install
//     createdb wa_lock_test
//     WA_LOCK_TEST_URL=postgresql://postgres@localhost:5432/wa_lock_test \
//       node teste-trava-integrada.js

const fs = require('fs')
const path = require('path')
const { Pool } = require('pg')

const URL = process.env.WA_LOCK_TEST_URL
if (!URL) {
  console.error('Falta WA_LOCK_TEST_URL (banco de TESTE, descartável — nunca produção).')
  process.exit(2)
}
// o server.js monta o pool a partir daqui, no require
process.env.DATABASE_URL = URL
process.env.LOG_LEVEL = process.env.LOG_LEVEL || 'silent'
// o server.js se recusa a subir sem o segredo compartilhado; aqui vale qualquer
// coisa, nenhuma requisição HTTP é feita
process.env.WA_QR_SHARED_SECRET = process.env.WA_QR_SHARED_SECRET || 'teste'

const CONTA = 35
const preparo = new Pool({ connectionString: URL })
const MIGRACAO = path.join(__dirname, '..', '..', 'db', 'migracoes', '157_wa_qr_sessao_lock.sql')

const ok = []; const bad = []
const t = (nome, cond) => (cond ? ok : bad).push(nome)

;(async () => {
  await preparo.query('drop table if exists wa_qr_sessao_lock cascade')
  await preparo.query('drop table if exists wa_qr_auth cascade')
  await preparo.query('drop table if exists contas cascade')
  await preparo.query('create table contas (id bigserial primary key, nome text)')
  await preparo.query('insert into contas (id, nome) values ($1,$2)', [CONTA, 'Conta 35'])
  await preparo.query(fs.readFileSync(MIGRACAO, 'utf8'))
  // credencial pareada de mentira: se o portão falhar, o iniciarSessao passa
  // daqui e tenta abrir socket de verdade — e o teste denuncia
  await preparo.query(`create table wa_qr_auth (
    conta_id bigint not null, arquivo text not null, conteudo text not null,
    atualizado timestamptz default now(), primary key (conta_id, arquivo))`)

  // OUTRA instância já está com a conta (é o deploy sobrepondo)
  await preparo.query(
    `insert into wa_qr_sessao_lock (conta_id, dono, expira_em)
     values ($1, 'outra-instancia', now() + interval '5 minutes')`, [CONTA])

  const srv = require('./server')

  const s = await srv.iniciarSessao(CONTA)
  t('iniciarSessao devolve sessão sem socket', !!s && !s.sock)
  t("status vira 'reconectando' (o painel já sabe mostrar)", s.status === 'reconectando')
  t('não ficou marcada como iniciando', s.iniciando === false)
  t('a trava confirma que NÃO é nossa', srv.trava.segura(CONTA) === false)
  t('agendou nova tentativa sozinho', srv.tentativasDeTrava.has(CONTA))
  const dono = (await preparo.query('select dono from wa_qr_sessao_lock where conta_id=$1', [CONTA])).rows[0].dono
  t('e não roubou o aluguel da outra instância', dono === 'outra-instancia')

  // O portão precisa barrar SEMPRE, não só na primeira vez — /enviar e
  // restaurarSessoes chamam iniciarSessao em looping durante um deploy.
  const s2 = await srv.iniciarSessao(CONTA)
  t('segunda chamada também é barrada', !s2.sock && s2.status === 'reconectando')
  t('e continua sem roubar a trava', srv.trava.segura(CONTA) === false)

  // limpa o timer pro processo poder sair
  for (const timer of srv.tentativasDeTrava.values()) clearTimeout(timer)

  // Com a conta livre, o portão TEM que deixar passar — uma trava que nunca
  // libera derruba o WhatsApp de todo mundo, que é pior que o problema original.
  // Confere na mesma fiação do serviço (mesmo pool, mesma trava), sem chegar a
  // abrir socket: o portão é só este `pegar`.
  await preparo.query('delete from wa_qr_sessao_lock where conta_id=$1', [CONTA])
  t('com a conta livre, o portão do serviço libera', (await srv.trava.pegar(CONTA)) === true)
  t('e o aluguel passa a ser nosso', srv.trava.segura(CONTA) === true)
  await srv.trava.soltar(CONTA)

  // SIGTERM tem que soltar o aluguel — é isso que faz a instância nova assumir em
  // segundos em vez de esperar o prazo vencer. Roda num processo FILHO porque o
  // encerrar() termina com process.exit.
  const { execFileSync } = require('child_process')
  const filho = `
    process.env.DATABASE_URL = ${JSON.stringify(URL)}
    process.env.WA_QR_SHARED_SECRET = 'teste'
    process.env.LOG_LEVEL = 'silent'
    const srv = require(${JSON.stringify(path.join(__dirname, 'server.js'))})
    srv.trava.pegar(${CONTA}).then(() => srv.encerrar('SIGTERM'))
  `
  try { execFileSync(process.execPath, ['-e', filho], { timeout: 30000, stdio: 'ignore' }) } catch (_) {}
  const restou = (await preparo.query(
    'select count(*)::int n from wa_qr_sessao_lock where conta_id=$1', [CONTA])).rows[0].n
  t('SIGTERM solta o aluguel ao sair', restou === 0)

  ok.forEach((n) => console.log('  ok   ' + n))
  bad.forEach((n) => console.log('  FALHOU  ' + n))
  console.log(`\n${ok.length} ok, ${bad.length} falhou`)
  await preparo.end()
  await srv.pool.end()
  process.exit(bad.length ? 1 : 0)
})().catch((e) => { console.error('ERRO NO TESTE:', e); process.exit(1) })
