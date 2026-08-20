"""Desconto no app do vendedor — e a PARIDADE com a tela do dono.

O desconto por item e o do total nasceram no painel de Serviços (#489). O vendedor
não usa aquela tela: ele usa o Cockpit, que tem montador de orçamento próprio. Aqui
o desconto passa a existir lá também — e o que estes testes prendem é justamente o
que faria a segunda tela virar um segundo número:

* **paridade** — os MESMOS itens com o MESMO desconto, montados no painel e no
  Cockpit, gravam o mesmo `primeiro_ano_centavos`. É o teste que impede as duas
  contas de divergirem daqui pra frente;

* **o JavaScript concorda com o Python** — não que ele compila: que ele CHEGA no
  mesmo número. O script sai renderizado, roda em node contra uma matriz de casos e
  é comparado com `finance.desconto`. `node --check` não pega divergência de conta;

* **`primeiro_ano_centavos`** — o Cockpit nunca gravou essa coluna, e quem emite
  título lê `coalesce(primeiro_ano_centavos, setup_centavos, 0)`. Sem ela, somar
  desconto faria o cliente assinar por um valor e o financeiro cobrar outro;

* **o portão do nicho, no SERVIDOR** — quem não vende serviço manda desconto no
  payload e o orçamento sai sem desconto nenhum. Esconder da tela é metade.

Banco dedicado e descartável.
"""
import json
import os
import re
import shutil
import subprocess

import pytest
from psycopg_pool import ConnectionPool

from finance import cockpit as ck
from finance import desconto as dsc
from web import painel_cockpit as pc

CONTA_SRV = 11        # vende serviço (nicho eventos)
CONTA_PRD = 22        # só produto (hortifruti)
LEAD_SRV, LEAD_PRD = 101, 202


@pytest.fixture()
def pool(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_cockpit_desconto"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute("create table nichos (id bigserial primary key, nome text, slug text unique)")
        # as colunas de cadastro existem porque `modo_do_orcamento` passa por
        # `empresa.obter_dados_empresa` — o modo do orçamento vem do NICHO, não da tela
        c.execute("""create table contas (id bigserial primary key, nome text,
                     nicho_id bigint references nichos(id),
                     vende_produto boolean, vende_servico boolean,
                     documento text, razao_social text, nome_fantasia text,
                     endereco text, bairro text, cep text, cidade text, uf text,
                     email_empresa text, telefone text, cnae text, chip_de bigint)""")
        c.execute("""create table prospeccao (id bigserial primary key, conta_id bigint,
                     membro_id bigint, empresa text, contato text, decisor_nome text,
                     socio text, cnpj text, segmento text, whatsapp text, telefone text,
                     email text, cidade text, uf text, orcamento_id bigint,
                     atualizado_em timestamptz default now())""")
        c.execute("insert into nichos (nome, slug) values ('Eventos','eventos'),"
                  "('Hortifruti','hortifruti')")
        c.execute("""insert into contas (id, nome, nicho_id) values
            (%s,'Prime',(select id from nichos where slug='eventos')),
            (%s,'Quitanda',(select id from nichos where slug='hortifruti'))""",
                  (CONTA_SRV, CONTA_PRD))
        c.execute("""create table servicos_catalogo (id bigserial primary key, conta_id bigint,
                     slug text, nome text, descricao text,
                     setup_centavos bigint default 0, mensal_centavos bigint default 0,
                     custo_centavos bigint default 0, ordem int default 0,
                     categoria text, foto_url text, icone text, ativo boolean default true)""")
        c.execute("""insert into servicos_catalogo (conta_id, slug, nome, setup_centavos)
                     values (%s,'salao','Locação do salão',900000),
                            (%s,'hora-extra','Hora extra',62000)""",
                  (CONTA_SRV, CONTA_SRV))
        c.execute("""insert into prospeccao (id, conta_id, membro_id, empresa, contato, whatsapp)
                     values (%s,%s,1,'Buffet Estrela','Ana','86999990000'),
                            (%s,%s,1,'Mercadinho','Zé','86999991111')""",
                  (LEAD_SRV, CONTA_SRV, LEAD_PRD, CONTA_PRD))
        c.commit()
    with p.connection() as c:
        from web.painel_servicos import _garantir_tabela
        _garantir_tabela(c)
        c.commit()
    monkeypatch.setattr(ck, "_posse", lambda c, cid, mid, lid: True)
    monkeypatch.setenv("APP_URL", "https://zaq.test")
    yield p
    p.close()


def _orc(pool, oid):
    with pool.connection() as c:
        r = c.execute("""select itens, setup_centavos, mensal_centavos,
                                primeiro_ano_centavos, desconto_tipo, desconto_pct,
                                desconto_centavos
                           from orcamentos where id=%s""", (oid,)).fetchone()
    return {"itens": r[0], "setup": r[1], "mensal": r[2], "total": r[3],
            "d_tipo": r[4], "d_pct": float(r[5]), "d_cent": r[6]}


ITENS = [{"nome": "Locação do salão", "setup": 9000, "mensal": 0,
          "desc_tipo": "pct", "desc_val": 5},
         {"nome": "Hora extra (× 2)", "setup": 1240, "mensal": 0,
          "desc_tipo": "valor", "desc_val": 240}]
DFIM = {"tipo": "pct", "pct": 10, "valor": 10}


# ══════════════════════════════════════════════ o portão do nicho

def test_conta_de_servico_pode_descontar(pool):
    assert ck.vende_servico(pool, CONTA_SRV) is True


def test_conta_que_so_vende_produto_nao_pode(pool):
    assert ck.vende_servico(pool, CONTA_PRD) is False


def test_o_portao_e_o_mesmo_do_painel(pool):
    """`empresa.o_que_vende` é a fonte — a mesma que alimenta o `conta[14]` que
    guarda o painel de Serviços. Portão paralelo acerta no primeiro dia."""
    from finance import empresa as emp
    for cid in (CONTA_SRV, CONTA_PRD):
        assert ck.vende_servico(pool, cid) is emp.o_que_vende(pool, cid)["servico"]


def test_o_servidor_ignora_desconto_de_quem_nao_vende_servico(pool):
    """Esconder o controle da tela é metade do portão. O payload vem do navegador."""
    r = ck.criar_orcamento(pool, CONTA_PRD, 1, LEAD_PRD, ITENS, {"tipo": "pct", "pct": 90})
    assert r["ok"]
    o = _orc(pool, r["id"])
    assert o["d_pct"] == 0 and o["d_cent"] == 0
    # e o desconto NÃO fica gravado na linha: o campo é descartado, não zerado
    assert all("desc_val" not in i for i in o["itens"])
    assert o["total"] == 10240 * 100      # o cheio, sem tirar nada


def test_a_leitura_do_nicho_falhando_nao_libera_desconto(pool, monkeypatch):
    """TOLERANTE pro lado seguro: errar pra menos custa um controle que não
    aparece; errar pra mais gravaria desconto que vira título a receber."""
    from finance import empresa as emp
    monkeypatch.setattr(emp, "o_que_vende",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ck.vende_servico(pool, CONTA_SRV) is False


# ══════════════════════════════════════════════ o que fica gravado

def test_o_desconto_entra_no_orcamento(pool):
    r = ck.criar_orcamento(pool, CONTA_SRV, 1, LEAD_SRV, ITENS, DFIM)
    assert r["ok"]
    o = _orc(pool, r["id"])
    # a linha guarda o desconto DELA, dentro do próprio retrato do item
    assert [(i["desc_tipo"], i["desc_val"]) for i in o["itens"]] == [("pct", 5), ("valor", 240)]
    assert o["d_tipo"] == "pct" and o["d_pct"] == 10
    esperado = dsc.totais(ITENS, tipo="pct", pct=10, valor=1000)
    assert o["total"] == esperado["total"]


def test_setup_e_mensal_continuam_sendo_o_bruto(pool):
    """Eles são o preço de TABELA do que foi escolhido — quem carrega o líquido é
    `primeiro_ano_centavos`. Descontar aqui também tiraria o desconto duas vezes."""
    r = ck.criar_orcamento(pool, CONTA_SRV, 1, LEAD_SRV, ITENS, DFIM)
    o = _orc(pool, r["id"])
    assert o["setup"] == (9000 + 1240) * 100 and o["mensal"] == 0
    assert o["total"] < o["setup"]


def test_o_primeiro_ano_e_gravado_mesmo_sem_desconto_nenhum(pool):
    """A COLUNA QUE FALTAVA. `finance/vendas.py` lê
    `coalesce(primeiro_ano_centavos, setup_centavos, 0)` pra emitir o título — o
    Cockpit nunca gravou, então caía na soma bruta. Sem desconto o número é o
    mesmo e ninguém notava; com desconto, o cliente assina um valor e o sistema
    cobra outro."""
    r = ck.criar_orcamento(pool, CONTA_SRV, 1, LEAD_SRV,
                           [{"nome": "Salão", "setup": 9000, "mensal": 0}], None)
    o = _orc(pool, r["id"])
    assert o["total"] == 9000 * 100


def test_o_desconto_do_total_pega_o_item_avulso(pool):
    """O avulso não tem desconto de linha (não existe preço de tabela pra
    descontar), mas está no subtotal — então o desconto do total cai nele."""
    itens = [{"nome": "Salão", "setup": 1000, "mensal": 0, "desc_tipo": "pct", "desc_val": 0},
             {"nome": "Visita técnica", "setup": 1000, "mensal": 0}]
    r = ck.criar_orcamento(pool, CONTA_SRV, 1, LEAD_SRV, itens, {"tipo": "pct", "pct": 10})
    assert _orc(pool, r["id"])["total"] == 1800 * 100


def test_desconto_maior_que_o_valor_nao_vira_acrescimo(pool):
    r = ck.criar_orcamento(pool, CONTA_SRV, 1, LEAD_SRV,
                           [{"nome": "X", "setup": 100, "mensal": 0,
                             "desc_tipo": "valor", "desc_val": 999}],
                           {"tipo": "valor", "pct": 0, "valor": 999})
    assert _orc(pool, r["id"])["total"] == 0


@pytest.mark.parametrize("lixo", [{"pct": "dez"}, {"valor": None}, {"tipo": "grátis"},
                                  "não é dict", None, {"pct": -50}])
def test_desconto_torto_vira_zero_em_vez_de_explodir(pool, lixo):
    """A tela é a fonte, e ela erra — campo vazio, texto solto, payload de uma aba
    velha. Nada disso pode derrubar o orçamento do vendedor no meio da visita."""
    r = ck.criar_orcamento(pool, CONTA_SRV, 1, LEAD_SRV,
                           [{"nome": "X", "setup": 100, "mensal": 0}], lixo)
    assert r["ok"] and _orc(pool, r["id"])["total"] == 100 * 100


# ══════════════════════════════════════════════ paridade com o painel

def test_as_duas_telas_gravam_o_mesmo_total(pool):
    """A TRAVA CENTRAL. O painel deriva o total com `dsc.totais`; o Cockpit passa a
    derivar com a mesma chamada. Se um dos dois um dia fizer a conta por fora, este
    teste é quem avisa — antes de o cliente receber duas folhas diferentes."""
    do_painel = dsc.totais(ITENS, tipo="pct", pct=10, valor=0)
    r = ck.criar_orcamento(pool, CONTA_SRV, 1, LEAD_SRV, ITENS, DFIM)
    assert _orc(pool, r["id"])["total"] == do_painel["total"]


# ══════════════════════════════════════════════ o JS concorda com o Python

CASOS = [
    # (itens, desconto do total) — cobre os dois modos nas duas pontas
    ([{"setup": 9000, "mensal": 0, "desc_tipo": "pct", "desc_val": 5},
      {"setup": 1240, "mensal": 0, "desc_tipo": "valor", "desc_val": 240}],
     {"t": "pct", "v": 10}),
    ([{"setup": 500, "mensal": 120, "desc_tipo": "pct", "desc_val": 33}],
     {"t": "valor", "v": 77}),                       # recorrente: setup + mensal×12
    ([{"setup": 333, "mensal": 0, "desc_tipo": "pct", "desc_val": 33}],
     {"t": "pct", "v": 7}),                          # dízima nos dois degraus
    # METADE EXATA, e não é hipótese: R$ 21 com 50% na linha dá subtotal de 1050
    # centavos, e 1% disso é 10,5 — o Python arredonda pro PAR (10) e o
    # Math.round do JS arredonda pra cima (11). Foi procurado por força bruta
    # dentro da própria fórmula; sem ele o `arr()` do script passa despercebido.
    ([{"setup": 21, "mensal": 0, "desc_tipo": "pct", "desc_val": 50}],
     {"t": "pct", "v": 1}),
    ([{"setup": 3, "mensal": 0, "desc_tipo": "pct", "desc_val": 50}],
     {"t": "pct", "v": 0}),                          # 150 centavos → 1,5 → ímpar, não separa
    ([{"setup": 100, "mensal": 0, "desc_tipo": "valor", "desc_val": 999}],
     {"t": "valor", "v": 999}),                      # estoura os dois tetos
    ([{"setup": 0, "mensal": 0, "desc_tipo": "pct", "desc_val": 20}],
     {"t": "pct", "v": 20}),                         # base zero: divisão por zero
    ([{"setup": 2000, "mensal": 0}, {"setup": 1000, "mensal": 0}],
     {"t": "pct", "v": 0}),                          # sem desconto nenhum
]

_HARNESS = r"""
globalThis.window = globalThis;      // .mjs em node não tem `window`
window.ORC = {cat:[], leadId:1, base:"", desc:true};
// stubs do DOM: o script mexe na tela ao subir, e aqui só interessa a CONTA
var vazio = {style:{}, classList:{add:function(){},remove:function(){},toggle:function(){}},
             innerHTML:"", textContent:"", value:"", disabled:false,
             querySelector:function(){return null;}, appendChild:function(){},
             setAttribute:function(){}, getAttribute:function(){return null;}};
globalThis.document = {
  getElementById:function(){return Object.create(vazio);},
  querySelector:function(){return null;},
  createElement:function(){return Object.create(vazio);},
  addEventListener:function(){},
};

__SCRIPT__

var casos = __CASOS__, saida = [];
casos.forEach(function(c){
  // METADE pelo CATÁLOGO e metade pelo AVULSO, de propósito: são dois caminhos de
  // código (`linhaDe` lê de sel[i], o avulso é o próprio objeto) e alimentar só um
  // deixaria o outro sem teste — foi o que aconteceu quando os dois guardavam o
  // desconto com nomes de campo diferentes.
  var sel = {}, avulsos = [];
  window.ORC.cat = [];
  c.itens.forEach(function(it, i){
    // "pct"/0 quando o caso não diz: é como o app inicializa a linha (marcar um
    // serviço cria {q:1,desc_tipo:"pct",desc_val:0}), e montar estado que o app
    // nunca produz testaria outra coisa.
    var t = it.desc_tipo || "pct", v = it.desc_val || 0;
    if(i % 2 === 0){
      window.ORC.cat.push({nome:"c"+i, setup:it.setup, mensal:it.mensal});
      sel[window.ORC.cat.length-1] = {q:1, desc_tipo:t, desc_val:v};
    } else {
      avulsos.push({nome:"a"+i, setup:it.setup, mensal:it.mensal,
                    desc_tipo:t, desc_val:v});
    }
  });
  window.__orc.set(sel, avulsos, {t:c.d.t, v:c.d.v});
  // e as LINHAS que vão no payload têm que sair com o desconto nos dois caminhos
  var linhas = window.__orc.itens();
  linhas.forEach(function(l, j){
    if(l.desc_val === undefined) throw new Error("linha sem desc_val: " + JSON.stringify(l));
  });
  var r = window.__orc.conta();
  saida.push({total:r.total, subtotal:r.subtotal, descItens:r.descItens,
              descFim:r.descFim, setup:r.setup, mensal:r.mensal});
});
console.log(JSON.stringify(saida));
"""


def _script_do_cockpit() -> str:
    return re.findall(r"<script>(.*?)</script>", pc._ORC_JS, re.S)[0]


@pytest.mark.skipif(not shutil.which("node"), reason="node não instalado")
def test_o_javascript_do_cockpit_chega_no_mesmo_numero_do_python(tmp_path):
    """`node --check` prova que compila, não que CONCORDA — e é a concordância que
    importa: o vendedor mostra um número pro cliente na visita e o servidor grava
    outro. Já custou um orçamento com parcelas somando R$ 12.105 contra um total de
    R$ 9.405, e foi por isso que `finance/desconto.py` virou ponto único da regra.

    A matriz tem de propósito os casos que separam as duas linguagens: dízima nos
    dois degraus, metade exata (Python arredonda pro PAR, o JS arredonda pra cima),
    base zero e desconto maior que o valor.
    """
    casos = [{"itens": i, "d": d} for i, d in CASOS]
    f = tmp_path / "conta.mjs"
    f.write_text(_HARNESS.replace("__SCRIPT__", _script_do_cockpit())
                         .replace("__CASOS__", json.dumps(casos)), encoding="utf-8")
    p = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    do_js = json.loads(p.stdout.strip().splitlines()[-1])

    assert len(do_js) == len(CASOS)
    for (itens, d), js in zip(CASOS, do_js):
        py = dsc.totais(itens, tipo=d["t"], pct=d["v"], valor=d["v"] * 100)
        assert js["total"] == py["total"], f"total divergiu em {itens} + {d}"
        assert js["subtotal"] == py["subtotal"], f"subtotal divergiu em {itens} + {d}"
        assert js["descItens"] == py["desconto_itens"], f"desconto de item divergiu em {itens}"
        assert js["descFim"] == py["desconto_final"], f"desconto final divergiu em {itens}"
        # as PONTAS também: é delas que saem os títulos de setup e de mensalidade
        assert js["setup"] + js["mensal"] * dsc.MESES_ANO1 == py["subtotal"]


@pytest.mark.skipif(not shutil.which("node"), reason="node não instalado")
def test_o_javascript_compila(tmp_path):
    p = subprocess.run(["node", "--check", "-"], input=_script_do_cockpit(),
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


# ══════════════════════════════════════════════ a tela, pela rota real

@pytest.fixture()
def tela(pool, monkeypatch):
    """O montador servido de verdade, com o gate de papel do app no caminho."""
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from starlette.middleware.sessions import SessionMiddleware

    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    monkeypatch.setattr(pc.ck, "lead_do_vendedor",
                        lambda *a, **k: {"empresa": "Buffet Estrela"})
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pc.router)

    @app.post("/_entrar")
    async def _entrar(request: Request, dados: dict):
        request.session["conta_id"] = dados["conta"]
        request.session["membro_id"] = 1
        request.session["papel"] = "vendedor"
        return {"ok": True}

    return TestClient(app)


def test_a_tela_traz_o_controle_pra_quem_vende_servico(tela):
    tela.post("/_entrar", json={"conta": CONTA_SRV})
    h = tela.get(f"/cockpit/lead/{LEAD_SRV}/orcamento").text
    assert '"desc": true' in h.replace("'", '"')
    assert 'data-dfim=pct' in h and 'id=dfimv' in h          # o desconto do total
    assert 'class=dsc' in pc._ORC_JS                          # e o da linha, no script


def test_a_tela_nao_traz_o_controle_pra_quem_nao_vende_servico(tela):
    """O `ORC.desc` sai do MESMO portão que o servidor usa — não de uma segunda
    regra escrita no template, que acertaria hoje e divergiria depois."""
    tela.post("/_entrar", json={"conta": CONTA_PRD})
    h = tela.get(f"/cockpit/lead/{LEAD_PRD}/orcamento").text
    assert '"desc": false' in h.replace("'", '"')


def test_o_rodape_do_desconto_nasce_escondido(tela):
    """Quem não dá desconto vê a tela de antes, igualzinha: as linhas de conta só
    aparecem quando existe desconto — quem liga é o `soma()`, no navegador."""
    tela.post("/_entrar", json={"conta": CONTA_SRV})
    h = tela.get(f"/cockpit/lead/{LEAD_SRV}/orcamento").text
    for ident in ("dfim", "lsub", "ldesc"):
        m = re.search(r"id=" + ident + r"[^>]*", h)
        assert m and "display:none" in m.group(0), ident


def test_o_desconto_atravessa_a_rota_ate_o_banco(tela, pool):
    """O caminho INTEIRO, pelo HTTP. Os outros testes chamam `criar_orcamento`
    direto e não notariam a rota largando o `desconto` do payload — o vendedor
    digitaria 10%%, a tela mostraria o abatido e o banco gravaria o cheio, calado.
    É o tipo de buraco que só aparece com o cliente na frente."""
    tela.post("/_entrar", json={"conta": CONTA_SRV})
    r = tela.post(f"/cockpit/lead/{LEAD_SRV}/orcamento",
                  json={"itens": ITENS, "desconto": {"tipo": "pct", "pct": 10, "valor": 10}})
    assert r.status_code == 200 and r.json()["ok"], r.text
    o = _orc(pool, r.json()["id"])
    assert o["d_pct"] == 10
    assert o["total"] == dsc.totais(ITENS, tipo="pct", pct=10, valor=1000)["total"]
    # e o número que a tela mostrou volta confirmado na resposta
    assert r.json()["total_centavos"] == o["total"]


def test_sem_desconto_a_rota_continua_funcionando_como_antes(tela, pool):
    """Payload de uma aba aberta ANTES do deploy não manda `desconto` nenhum."""
    tela.post("/_entrar", json={"conta": CONTA_SRV})
    r = tela.post(f"/cockpit/lead/{LEAD_SRV}/orcamento",
                  json={"itens": [{"nome": "Salão", "setup": 9000, "mensal": 0}]})
    assert r.status_code == 200 and r.json()["ok"]
    o = _orc(pool, r.json()["id"])
    assert o["d_pct"] == 0 and o["total"] == 9000 * 100


def test_a_folha_do_cliente_mostra_o_desconto_que_o_vendedor_deu(tela, pool):
    """A folha (/proposta/<token>) NÃO precisou mudar — e é isso que este teste
    prende, porque eu afirmei antes de conferir. Ela chama
    `dsc.liquido_do_item` direto no retrato do item, então o formato que o Cockpit
    grava tem que ser o mesmo que o painel grava. Se um dia divergir, o cliente
    recebe uma folha sem o riscado e não vê que ganhou desconto."""
    from web import proposta as pp
    tela.post("/_entrar", json={"conta": CONTA_SRV})
    r = tela.post(f"/cockpit/lead/{LEAD_SRV}/orcamento",
                  json={"itens": ITENS, "desconto": {"tipo": "pct", "pct": 10}})
    itens = _orc(pool, r.json()["id"])["itens"]
    linhas = pp._linhas_evento({"itens": itens})
    assert [l["desconto_pct"] for l in linhas] == ["5%", "19%"]
    assert all(l["cheio"] for l in linhas), "faltou o valor cheio riscado"
    # o riscado é o BRUTO e o que ele paga é o LÍQUIDO — nessa ordem
    assert linhas[0]["cheio"] != linhas[0]["subtotal"]


_HARNESS_DOM = r"""
globalThis.window = globalThis;
window.ORC = {cat:[{nome:"Salão", setup:9000, mensal:0}], leadId:1, base:"", desc:true};

// ---- DOM de mentira, só o bastante pros handlers rodarem ----
// Não é fidelidade ao navegador: é provar que a pílula e o campo acham a LINHA
// certa. Com catálogo e avulso guardando o desconto no mesmo lugar, apontar pro
// objeto errado é o jeito de errar aqui.
function No(tag, attrs){
  this.tag=tag; this.attrs=attrs||{}; this.filhos=[]; this._html="";
  this.style={}; this.textContent=""; this.value=""; this.disabled=false;
  this.parentNode={children:[]};
  this.classList={_c:{}, add:function(){}, remove:function(){}, toggle:function(){}};
}
No.prototype.getAttribute=function(k){return this.attrs[k]!==undefined?this.attrs[k]:null;};
No.prototype.setAttribute=function(k,v){this.attrs[k]=v;};
No.prototype.appendChild=function(n){this.filhos.push(n);return n;};
No.prototype.querySelector=function(){return new No("div",{});};
Object.defineProperty(No.prototype,"innerHTML",{
  get:function(){return this._html;}, set:function(v){this._html=v;}});

var caixas={};
globalThis.document={
  getElementById:function(id){return caixas[id]||(caixas[id]=new No("div",{id:id}));},
  querySelector:function(){return null;},
  createElement:function(t){return new No(t,{});},
  addEventListener:function(ev,fn){(this._ls=this._ls||{})[ev]=(this._ls[ev]||[]).concat([fn]);},
};

__SCRIPT__

// dispara um evento como o navegador dispararia, com o alvo que interessa
function manda(ev, alvo){
  (document._ls[ev]||[]).forEach(function(fn){
    fn({target:alvo, stopPropagation:function(){}});
  });
}
function comClosest(no, mapa){
  no.closest=function(sel){return mapa[sel]||null;};
  return no;
}

var erros=[], ok=function(c,m){if(!c)erros.push(m);};
var A=window.__orc;

// 1) marca o serviço do catálogo
var linha=comClosest(new No("div",{}), {".srv": new No("div",{"data-i":"0"})});
manda("click", linha);
ok(A.itens().length===1, "não marcou o serviço do catálogo");

// 2) põe 5% NELE — pela pílula e pelo campo, como o dedo faria
var dsc=new No("div",{"data-k":"c0"});
var botao=comClosest(new No("button",{"data-dt":"pct"}), {"[data-dt]":null, ".dsc":dsc});
botao.closest=function(s){return s==="[data-dt]"?botao:(s===".dsc"?dsc:null);};
manda("click", botao);
var campo=new No("input",{"data-di":"c0"}); campo.value="5";
campo.closest=function(s){return s==="[data-di]"?campo:(s===".dsc"?dsc:null);};
manda("input", campo);
ok(A.itens()[0].desc_val===5, "o desconto não entrou na linha do catálogo: "
   + JSON.stringify(A.itens()[0]));

// 3) agora um AVULSO, adicionado pelo botão como o vendedor faria
document.getElementById("addnome").value="Visita";
document.getElementById("addval").value="1000";
document.getElementById("addbtn").onclick();
var desenho=document.getElementById("avulsos").innerHTML;
ok(/data-di="a0"/.test(desenho), "a linha do avulso saiu SEM o campo de desconto");
ok(/data-dt="pct"/.test(desenho) && /data-dt="valor"/.test(desenho),
   "a linha do avulso saiu SEM a pílula %/R$");
ok(A.itens().length===2, "o avulso não entrou: "+A.itens().length);
var dsc2=new No("div",{"data-k":"a0"});
var b2=new No("button",{"data-dt":"valor"});
b2.closest=function(s){return s==="[data-dt]"?b2:(s===".dsc"?dsc2:null);};
manda("click", b2);
var c2=new No("input",{"data-di":"a0"}); c2.value="240";
c2.closest=function(s){return s==="[data-di]"?c2:(s===".dsc"?dsc2:null);};
manda("input", c2);
var av=A.itens()[1];
ok(av && av.setup===1000, "o avulso perdeu o valor: "+JSON.stringify(av));
ok(av && av.desc_tipo==="valor" && av.desc_val===240,
   "o desconto não entrou no avulso: " + JSON.stringify(av));

// 4) e o do catálogo NÃO foi mexido junto — é o erro que alvo() existe pra evitar
ok(A.itens()[0].desc_val===5 && A.itens()[0].desc_tipo==="pct",
   "mexer no avulso mudou a linha do catálogo: " + JSON.stringify(A.itens()[0]));

// 5) e o PORTÃO na tela: conta que não vende serviço não ganha o controle em
// linha nenhuma. O servidor já recusa o campo, mas sem isto o vendedor digitaria
// um desconto que some calado — pior que não ter o campo.
window.ORC.desc = false;
document.getElementById("addnome").value="Outra";
document.getElementById("addval").value="500";
document.getElementById("addbtn").onclick();
var semPortao=document.getElementById("avulsos").innerHTML;
ok(!/data-di=/.test(semPortao), "conta sem serviço ganhou campo de desconto");
ok(!/data-dt=/.test(semPortao), "conta sem serviço ganhou a pílula %/R$");

if(erros.length){console.log("FALHOU: "+erros.join(" | "));process.exit(1);}
console.log("OK");
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node não instalado")
def test_a_pilula_e_o_campo_acertam_a_linha_certa(tmp_path):
    """Catálogo e avulso guardam o desconto no MESMO formato e passam pela mesma
    porta (`alvo`). O jeito de errar aqui é apontar pro objeto errado — mexer no
    avulso e o desconto cair no serviço do catálogo, calado. Então os handlers
    rodam de verdade, com evento e alvo, em vez de serem lidos."""
    f = tmp_path / "dom.mjs"
    f.write_text(_HARNESS_DOM.replace("__SCRIPT__", _script_do_cockpit()), encoding="utf-8")
    p = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "OK" in p.stdout
