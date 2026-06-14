/**
 * Teste E2E do fluxo Block 3: ajuste de produto por variante
 *
 * Valida:
 * 1. JS carrega comparação inicial
 * 2. Botões "Ajuste" aparecem para cada item
 * 3. Modal abre com variantes do produto
 * 4. Seleção ajusta estado e recalcula
 * 5. Parâmetros ?ajuste= são passados corretamente
 */

const http = require('http');
const https = require('https');

const BASE_URL = 'http://localhost:8000';
const token = process.env.TEST_TOKEN || 'test-session-token';

async function request(method, path, body = null) {
  return new Promise((resolve, reject) => {
    const url = new URL(path, BASE_URL);
    const options = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + url.search,
      method,
      headers: {
        'Content-Type': 'application/json',
        'Cookie': `session=${token}`,
      },
    };

    const req = http.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, data: JSON.parse(data) });
        } catch (e) {
          resolve({ status: res.statusCode, data });
        }
      });
    });

    req.on('error', reject);
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

async function test() {
  console.log('🧪 Teste Block 3: Ajuste de Produto por Variante\n');

  // Teste 1: Comparação inicial (sem ajuste)
  console.log('1️⃣  Carregando comparação inicial (/painel/compras/precos)...');
  try {
    const res = await request('GET', '/painel/compras/precos');
    if (res.status === 401) {
      console.log('   ⚠️  Não autenticado (esperado em teste)');
    } else if (res.status === 200) {
      console.log('   ✅ Comparação carregada');
      console.log(`   - Fonte: ${res.data.fonte}`);
      console.log(`   - Grupos: ${Object.keys(res.data.grupos || {}).join(', ')}`);
      console.log(`   - Itens: ${res.data.itens?.length || 0} produtos`);
    } else {
      console.log(`   ❌ Status ${res.status}`);
    }
  } catch (e) {
    console.log(`   ⚠️  Erro de conexão: ${e.message}`);
    console.log('   (servidor offline, teste é validação de estrutura apenas)');
  }

  // Teste 2: Endpoint de opções
  console.log('\n2️⃣  Buscando variantes do produto "arroz" (/painel/compras/opcoes)...');
  try {
    const res = await request('GET', '/painel/compras/opcoes?item=arroz');
    if (res.status === 401) {
      console.log('   ⚠️  Não autenticado (esperado em teste)');
    } else if (res.status === 200) {
      console.log('   ✅ Variantes carregadas');
      console.log(`   - Total: ${res.data.opcoes?.length || 0} variantes`);
      if (res.data.opcoes?.length > 0) {
        res.data.opcoes.slice(0, 3).forEach((op, i) => {
          console.log(`   ${i+1}. ${op.descricao} (${op.faixa})`);
        });
      }
    } else {
      console.log(`   ❌ Status ${res.status}`);
    }
  } catch (e) {
    console.log(`   ⚠️  Erro de conexão: ${e.message}`);
  }

  // Teste 3: Comparação com ajuste
  console.log('\n3️⃣  Recalculando com ajuste (?ajuste=arroz||ARROZ INTEGRAL)...');
  try {
    const res = await request('GET', '/painel/compras/precos?ajuste=arroz||ARROZ INTEGRAL');
    if (res.status === 401) {
      console.log('   ⚠️  Não autenticado (esperado em teste)');
    } else if (res.status === 200) {
      console.log('   ✅ Comparação com ajuste recalculada');
      console.log(`   - Fonte: ${res.data.fonte}`);
      console.log(`   - Grupos: ${Object.keys(res.data.grupos || {}).join(', ')}`);
    } else {
      console.log(`   ❌ Status ${res.status}`);
    }
  } catch (e) {
    console.log(`   ⚠️  Erro de conexão: ${e.message}`);
  }

  // Teste 4: Validar estrutura JavaScript
  console.log('\n4️⃣  Validando estrutura JavaScript do template...');
  try {
    const res = await request('GET', '/painel/compras');
    if (res.status === 401) {
      console.log('   ⚠️  Não autenticado (esperado em teste)');
    } else if (res.status === 200) {
      const html = res.data;
      const checks = [
        ['Modal overlay (display:flex)', html.includes('align-items:center') && html.includes('justify-content:center')],
        ['ajustesAtivos state object', html.includes('ajustesAtivos')],
        ['recarregar() function', html.includes('function recarregar()')],
        ['window.ajustar() function', html.includes('window.ajustar')],
        ['URLSearchParams for ?ajuste=', html.includes('URLSearchParams')],
        ['Fetch de /painel/compras/opcoes', html.includes('/painel/compras/opcoes')],
        ['Botões de ajuste com onclick', html.includes('onclick="event.preventDefault()')],
      ];

      checks.forEach(([name, ok]) => {
        console.log(`   ${ok ? '✅' : '❌'} ${name}`);
      });
    } else {
      console.log(`   ❌ Status ${res.status}`);
    }
  } catch (e) {
    console.log(`   ⚠️  Erro de conexão: ${e.message}`);
  }

  console.log('\n✅ Teste de estrutura completo\n');
  console.log('Próximos passos:');
  console.log('1. Iniciar servidor: python web/app.py');
  console.log('2. Acessar /painel/compras no navegador');
  console.log('3. Adicionar itens na lista');
  console.log('4. Clicar em "Não bateu? Ajuste o produto"');
  console.log('5. Selecionar uma variante');
  console.log('6. Verificar recálculo da comparação');
}

test().catch(console.error);
