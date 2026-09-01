"""O app do vendedor faz o que o painel já faz (01/09/2026).

O pedido do dono foi paridade, não melhoria: "que os vendedores façam a mesma
coisa que é feita no desktop e já funciona". Comparando campo a campo o que as
duas telas gravam, sobraram estas diferenças — e é isso que este arquivo fixa:

1. QUANTIDADE E UNITÁRIO eram campo no painel e TEXTO no app: ele escrevia
   "LOCAÇÃO LEDS (× 15)" dentro do nome, porque o snapshot não tinha onde
   guardar. A folha do cliente, que imprime `qtd × unitário`, mostrava
   "1 × R$ 750,00".
2. O EVENTO (data, hora, tipo, convidados, local) e as PARCELAS eram gravados
   NULOS pelo app. Sem data não nasce pré-reserva na agenda e o contrato sai com
   o dia em branco; sem parcelas não existe sinal.
3. REABRIR a proposta só existia no painel — corrigir uma vírgula obrigava o
   vendedor a abrir o desktop no meio da conversa.
4. CONFIRMAR O SINAL só existia no desktop (funil e agenda), embora quem receba
   o comprovante no WhatsApp seja o vendedor, no celular.

Nada aqui é ideia nova: cada linha é uma coisa que o painel já grava.
"""
import json
import os
from datetime import date, timedelta

import pytest
from psycopg_pool import ConnectionPool

from finance import cockpit as ck

CONTA_SQL = """
create table nichos (id bigserial primary key, nome text, slug text unique, tipo text);
create table contas (id bigserial primary key, nome text, documento text, razao_social text,
  nome_fantasia text, endereco text, bairro text, cep text, cidade text, uf text,
  email_empresa text, telefone text, nicho text, cnae text,
  nicho_id bigint references nichos(id), chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true, whatsapp text,
  cockpit_push_ativo boolean default true, cockpit_pausado boolean default false);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, cnpj text, segmento text, cidade text, uf text, contato text, cargo text,
  telefone text, whatsapp text, email text, status text default 'novo',
  temperatura text default 'frio', valor_estimado_centavos bigint default 0,
  origem text, obs text, decisor_nome text, socio text, estagio text default 'lead',
  orcamento_id bigint, atualizado_em timestamptz default now(),
  criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text, rotulo text,
  ordem int default 0, fixa boolean default false, fase text not null default 'venda',
  unique (conta_id, chave));
create table servicos_catalogo (id bigserial primary key, conta_id bigint, slug text, nome text,
  descricao text, setup_centavos bigint default 0, mensal_centavos bigint default 0,
  custo_centavos bigint default 0, ordem int default 0, ativo boolean default true,
  categoria text, foto_url text, icone text);
create table orcamentos (id bigserial primary key, conta_id bigint, cliente text, empresa text,
  cnpj text, cpf text, segmento text, whatsapp text, telefone text, email text,
  cidade text, uf text, site text, cargo text, socio text, endereco text, cep text,
  modulos jsonb, itens jsonb, escopo text, canal text, modo text default 'recorrente',
  evento jsonb, parcelas jsonb, setup_centavos bigint default 0,
  mensal_centavos bigint default 0, primeiro_ano_centavos bigint,
  n_modulos int default 0, desconto_tipo text, desconto_pct numeric default 0,
  desconto_centavos bigint default 0, status text default 'rascunho', criado_por text,
  token text, numero int, evento_agenda_id bigint, sinal_pago_em timestamptz,
  aprovada_por text, aprovada_em timestamptz, aprovada_doc text,
  criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text, status text default 'aberta', agente_ativo boolean default true,
  responsavel_membro_id bigint, ultima_msg_em timestamptz default now(),
  criado_em timestamptz default now(), chip_id bigint, visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text, direcao text,
  autor text default 'humano', membro_id bigint, texto text default '',
  provider_sid text, criado_em timestamptz default now());
create table orcamento_envios (id bigserial primary key, conta_id bigint, orcamento_id bigint,
  canal text, ok boolean, erro text, por text, destino text,
  criado_em timestamptz default now());
"""


@pytest.fixture
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_cockpit_paridade"
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
        c.execute(CONTA_SQL)
        c.commit()
    yield p
    p.close()


@pytest.fixture
def cen(pool):
    """Conta do NICHO DE EVENTOS — é o modo que tem data e parcelas. Numa conta
    recorrente o servidor descarta os dois de propósito, e há teste pra isso."""
    with pool.connection() as c:
        nid = c.execute("insert into nichos (nome, slug, tipo) "
                        "values ('Eventos','eventos','pj') returning id").fetchone()[0]
        conta = c.execute("insert into contas (nome, nicho_id) values ('Prime',%s) returning id",
                          (nid,)).fetchone()[0]
        v = c.execute("insert into membros (conta_id, nome, email) "
                      "values (%s,'PEDRO','p@x.com') returning id", (conta,)).fetchone()[0]
        lead = c.execute("insert into prospeccao (conta_id, vendedor_id, empresa, contato, whatsapp) "
                         "values (%s,%s,'Festa da Ana','Ana','5586999990000') returning id",
                         (conta, v)).fetchone()[0]
        for nome, valor, cat in (("LOCAÇÃO LEDS", 5000, "Outros"),
                                 ("LOCAÇÃO COZINHA", 150000, "Locação de espaço")):
            c.execute("insert into servicos_catalogo (conta_id, slug, nome, setup_centavos, categoria) "
                      "values (%s,%s,%s,%s,%s)",
                      (conta, nome.lower().replace(" ", "-"), nome, valor, cat))
        c.commit()
    return {"conta": conta, "vend": v, "lead": lead}


def _orc(pool, oid):
    with pool.connection() as c:
        r = c.execute("select itens, evento, parcelas, setup_centavos, "
                      "primeiro_ano_centavos, token, status, sinal_pago_em "
                      "from orcamentos where id=%s", (oid,)).fetchone()
    itens = r[0] if isinstance(r[0], list) else json.loads(r[0] or "[]")
    return {"itens": itens, "evento": r[1], "parcelas": r[2], "setup": r[3],
            "liquido": r[4], "token": r[5], "status": r[6], "sinal_pago_em": r[7]}


def _criar(pool, cen, **kw):
    itens = kw.pop("itens", [{"nome": "LOCAÇÃO LEDS", "setup": 750, "mensal": 0,
                              "qtd": 15, "unitario": 50}])
    return ck.criar_orcamento(pool, cen["conta"], cen["vend"], cen["lead"], itens, **kw)


# --------------------------------------------------------------------------
# 1. Quantidade e unitário: campo, não texto no nome
# --------------------------------------------------------------------------

def test_quantidade_e_unitario_viram_campo(cen, pool):
    """O estrago que isto conserta aparecia na FOLHA DO CLIENTE: ela imprime
    `qtd × unitário`, e com a quantidade escondida no nome saía "1 × R$ 750,00"
    onde deveria estar "15 × R$ 50,00"."""
    r = _criar(pool, cen)
    assert r["ok"]
    (it,) = _orc(pool, r["id"])["itens"]
    assert it["nome"] == "LOCAÇÃO LEDS", "a quantidade voltou pro nome"
    assert it["qtd"] == 15 and it["unitario"] == 50
    assert it["setup"] == 750, "o total da linha é o que o funil soma"


def test_sem_unitario_explicito_ele_e_deduzido(cen, pool):
    """Proposta montada por um cliente antigo (ou por um item avulso) não manda
    unitário. Deduzir do total é a mesma conta que a folha do cliente já fazia
    sozinha — melhor deduzir do que gravar zero."""
    r = _criar(pool, cen, itens=[{"nome": "Buffet", "setup": 900, "qtd": 3}])
    (it,) = _orc(pool, r["id"])["itens"]
    assert it["qtd"] == 3 and it["unitario"] == 300


def test_quantidade_invalida_nao_zera_a_linha(cen, pool):
    """Quantidade nunca é zero: dividir o total por ela para achar o unitário
    estouraria, e uma linha com qtd 0 não quer dizer nada."""
    r = _criar(pool, cen, itens=[{"nome": "Buffet", "setup": 900, "qtd": 0}])
    (it,) = _orc(pool, r["id"])["itens"]
    assert it["qtd"] == 1 and it["unitario"] == 900


def test_a_categoria_do_catalogo_chega_no_snapshot(cen, pool):
    """É ela que agrupa a folha do cliente. Sem passar pelo app, toda proposta
    feita no celular nascia sem categoria nenhuma."""
    cat = ck.catalogo_servicos(pool, cen["conta"])
    leds = [s for s in cat if s["nome"] == "LOCAÇÃO LEDS"][0]
    assert leds["categoria"] == "Outros"
    r = _criar(pool, cen, itens=[{"nome": "LOCAÇÃO LEDS", "setup": 50, "qtd": 1,
                                  "unitario": 50, "categoria": "Outros"}])
    (it,) = _orc(pool, r["id"])["itens"]
    assert it["categoria"] == "Outros"


# --------------------------------------------------------------------------
# 2. O evento e as parcelas
# --------------------------------------------------------------------------

def test_o_evento_e_gravado_pelo_app(cen, pool):
    """Sem `evento.data` não nasce pré-reserva na agenda e o contrato sai com o
    dia em branco. O app gravava a coluna nula."""
    ev = {"data": "2027-11-18", "inicio": "19:00", "tipo": "Casamento",
          "convidados": 180, "local": "Av. Fátima, 1200"}
    r = _criar(pool, cen, evento=ev)
    salvo = _orc(pool, r["id"])["evento"]
    assert salvo["data"] == "2027-11-18" and salvo["inicio"] == "19:00"
    assert salvo["tipo"] == "Casamento" and salvo["convidados"] == 180
    assert salvo["local"] == "Av. Fátima, 1200"


def test_evento_em_branco_nao_vira_dicionario_vazio(cen, pool):
    """Coluna nula e coluna com um dicionário de campos em branco são estados
    diferentes pra quem lê (a agenda, o contrato). O formulário aberto e não
    preenchido tem que sair como nulo."""
    r = _criar(pool, cen, evento={"data": "", "inicio": "", "tipo": "",
                                  "local": "", "convidados": None})
    assert _orc(pool, r["id"])["evento"] is None


def test_convidados_com_texto_nao_derruba_o_orcamento(cen, pool):
    """O campo é livre no celular; "180 pessoas" não pode estourar o salvamento."""
    r = _criar(pool, cen, evento={"data": "2027-11-18", "convidados": "muitos"})
    assert r["ok"] and _orc(pool, r["id"])["evento"]["convidados"] is None


def test_parcelas_gravadas_e_o_sinal_sai_delas(cen, pool):
    """É desta lista que `vendas.valor_do_sinal` tira a entrada — sem parcelas não
    há sinal, e sem sinal a data nunca fica firme."""
    pcs = [{"venc": "2026-10-18", "valor_centavos": 234000, "forma": "PIX"},
           {"venc": "2027-10-18", "valor_centavos": 546000, "forma": ""}]
    r = _criar(pool, cen, parcelas=pcs)
    salvas = _orc(pool, r["id"])["parcelas"]
    assert len(salvas) == 2 and salvas[0]["valor_centavos"] == 234000
    from finance import vendas
    assert vendas.valor_do_sinal(salvas) == 234000


def test_parcela_zerada_e_descartada(cen, pool):
    """Mesma régua do painel: parcela sem valor é linha esquecida no formulário,
    não plano de pagamento."""
    r = _criar(pool, cen, parcelas=[{"venc": "2026-10-18", "valor_centavos": 0},
                                    {"venc": "2026-11-18", "valor_centavos": 100}])
    assert len(_orc(pool, r["id"])["parcelas"]) == 1


def test_conta_recorrente_nao_grava_evento_nem_parcelas(pool):
    """O modo vem do NICHO, não do navegador — e o servidor descarta, não confia
    na tela ter escondido os campos."""
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome) values ('SaaS') returning id").fetchone()[0]
        v = c.execute("insert into membros (conta_id, nome, email) "
                      "values (%s,'V','v@x.com') returning id", (conta,)).fetchone()[0]
        lead = c.execute("insert into prospeccao (conta_id, vendedor_id, empresa) "
                         "values (%s,%s,'ACME') returning id", (conta, v)).fetchone()[0]
        c.commit()
    r = ck.criar_orcamento(pool, conta, v, lead, [{"nome": "CRM", "setup": 100}],
                           evento={"data": "2027-01-01"},
                           parcelas=[{"venc": "x", "valor_centavos": 5000}])
    assert r["ok"]
    salvo = _orc(pool, r["id"])
    assert salvo["evento"] is None and salvo["parcelas"] is None


# --------------------------------------------------------------------------
# 3. Reabrir a proposta
# --------------------------------------------------------------------------

def test_reabrir_atualiza_em_vez_de_cunhar_a_segunda(cen, pool):
    r1 = _criar(pool, cen, evento={"data": "2027-11-18"})
    r2 = _criar(pool, cen, orcamento_id=r1["id"],
                itens=[{"nome": "LOCAÇÃO LEDS", "setup": 1000, "qtd": 20, "unitario": 50}],
                evento={"data": "2027-11-19"})
    assert r2["ok"] and r2["id"] == r1["id"]
    with pool.connection() as c:
        assert c.execute("select count(*) from orcamentos").fetchone()[0] == 1
    d = _orc(pool, r1["id"])
    assert d["itens"][0]["qtd"] == 20 and d["evento"]["data"] == "2027-11-19"


def test_reabrir_mantem_o_token_que_o_cliente_ja_tem(cen, pool):
    """Trocar o token transformaria uma correção em proposta perdida: o link já
    pode estar no WhatsApp do cliente."""
    r1 = _criar(pool, cen)
    antes = _orc(pool, r1["id"])["token"]
    _criar(pool, cen, orcamento_id=r1["id"])
    assert _orc(pool, r1["id"])["token"] == antes


def test_proposta_fechada_nao_reabre(cen, pool):
    """Fechada quer dizer que os títulos existem. Reabrir por baixo faria o
    financeiro cobrar um valor e o papel dizer outro."""
    r1 = _criar(pool, cen)
    with pool.connection() as c:
        c.execute("update orcamentos set status='fechado' where id=%s", (r1["id"],))
        c.commit()
    r2 = _criar(pool, cen, orcamento_id=r1["id"])
    assert r2["ok"] is False


def test_reabrir_proposta_de_outra_conta_nao_acha(cen, pool):
    """O id vem da barra de endereço."""
    with pool.connection() as c:
        alheio = c.execute("insert into orcamentos (conta_id, cliente) "
                           "values (999,'De outra loja') returning id").fetchone()[0]
        c.commit()
    r = _criar(pool, cen, orcamento_id=alheio)
    assert r["ok"] is False


def test_o_app_le_de_volta_o_evento_e_as_parcelas(cen, pool):
    """É com isso que o montador se repreenche ao reabrir. Sem voltar do banco, a
    tela abriria vazia e o vendedor redigitaria tudo — ou pior, salvaria por cima
    com os campos em branco."""
    r = _criar(pool, cen, evento={"data": "2027-11-18", "tipo": "Casamento"},
               parcelas=[{"venc": "2026-10-18", "valor_centavos": 234000}])
    d = ck.orcamento(pool, cen["conta"], r["id"], membro_id=cen["vend"])
    assert d["evento"]["tipo"] == "Casamento"
    assert d["parcelas"][0]["valor_centavos"] == 234000
    assert d["sinal_centavos"] == 234000 and d["sinal_pago_em"] is None


def test_proposta_antiga_sem_evento_volta_vazia_e_nao_quebra(cen, pool):
    """Os orçamentos que já existem têm a coluna nula."""
    with pool.connection() as c:
        oid = c.execute("insert into orcamentos (conta_id, cliente, criado_por) "
                        "values (%s,'Antiga',%s) returning id",
                        (cen["conta"], str(cen["vend"]))).fetchone()[0]
        c.commit()
    d = ck.orcamento(pool, cen["conta"], oid, membro_id=cen["vend"])
    assert d["evento"] == {} and d["parcelas"] == [] and d["sinal_centavos"] == 0


def test_a_marca_do_sinal_e_a_mesma_nas_duas_telas(cen, pool):
    """`vendas.indice_do_sinal` reconhece o sinal pela palavra na `obs` da primeira
    parcela. O painel escreve essa frase LITERAL no JavaScript (bloco `{% raw %}`,
    onde variável de template não interpola) e o app usa `vendas.OBS_SINAL`.

    Se as duas separarem, o sintoma é mudo: o plano montado numa tela deixa de ter
    sinal quando lido pela régua da outra. Este teste é o que segura as duas
    juntas — se alguém mudar a frase num lado só, ele quebra."""
    from pathlib import Path
    from finance import vendas
    painel = (Path(__file__).resolve().parent.parent
              / "web" / "painel_servicos.py").read_text(encoding="utf-8")
    assert f"obs:'{vendas.OBS_SINAL}'" in painel, (
        "a frase do painel e vendas.OBS_SINAL divergiram")
    # e a régua reconhece a frase, venha de onde vier
    assert vendas.indice_do_sinal([{"valor_centavos": 100, "obs": vendas.OBS_SINAL}]) == 0


def test_parcela_com_observacao_propria_nao_e_sobrescrita(cen, pool):
    """O carimbo do sinal só entra onde NÃO há observação: o que o vendedor (ou o
    painel) escreveu manda."""
    r = _criar(pool, cen, parcelas=[{"venc": "2026-10-18", "valor_centavos": 1000,
                                     "obs": "entrada combinada por fora"}])
    assert _orc(pool, r["id"])["parcelas"][0]["obs"] == "entrada combinada por fora"


# --------------------------------------------------------------------------
# 3b. O JAVASCRIPT da tela, rodado de verdade
#
# Os testes acima provam o SERVIDOR. Mas quem monta o payload é o script do app, e
# `node --check` só prova que ele compila. Aqui ele roda contra um DOM de mentira —
# o mesmo arreio que `test_cockpit_desconto` já usa — e o que se olha é o que a
# tela MANDARIA. Sem isto, o nome podia voltar a levar "(× 15)" e nada acusaria.
# --------------------------------------------------------------------------

_HARNESS = r"""
globalThis.window = globalThis;
window.ORC = {cat:[{nome:"LOCAÇÃO LEDS", setup:50, mensal:0, categoria:"Outros"}],
              leadId:1, base:"", desc:true, evento:true, orcId:0, abrir:null};
var vazio = {style:{}, classList:{add:function(){},remove:function(){},toggle:function(){}},
             innerHTML:"", textContent:"", value:"", disabled:false,
             querySelector:function(){return null;},
             querySelectorAll:function(){return [];}, appendChild:function(){},
             setAttribute:function(){}, getAttribute:function(){return null;}};
globalThis.document = {
  getElementById:function(){return Object.create(vazio);},
  querySelector:function(){return null;},
  querySelectorAll:function(){return [];},
  createElement:function(){return Object.create(vazio);},
  addEventListener:function(){},
};

__SCRIPT__

// 15 unidades a R$ 50 — o caso exato do orçamento nº 41, que hoje grava
// "LOCAÇÃO LEDS (× 15)" com setup 750 e nenhuma quantidade.
window.__orc.set({0:{q:15, u:50, desc_tipo:"pct", desc_val:0}}, [], {t:"pct", v:0});
var linha = window.__orc.itens()[0];
console.log(JSON.stringify(linha));
"""


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node não instalado")
def test_o_script_do_app_manda_qtd_e_unitario_separados():
    """O caso exato do orçamento nº 41 da Prime, rodado no script de verdade."""
    import json as _json
    import re
    import subprocess
    import tempfile
    from web import painel_cockpit as pc
    script = re.findall(r"<script>(.*?)</script>", pc._ORC_JS, re.S)[0]
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                     encoding="utf-8") as f:
        f.write(_HARNESS.replace("__SCRIPT__", script))
        caminho = f.name
    saida = subprocess.run(["node", caminho], capture_output=True, text=True, timeout=60)
    assert saida.returncode == 0, saida.stderr
    linha = _json.loads(saida.stdout.strip())
    assert linha["nome"] == "LOCAÇÃO LEDS", "a quantidade voltou pro nome"
    assert linha["qtd"] == 15 and linha["unitario"] == 50
    assert linha["setup"] == 750, "o total da linha continua sendo qtd × unitário"
    assert linha["categoria"] == "Outros"


# --------------------------------------------------------------------------
# 4. Confirmar o sinal do celular
# --------------------------------------------------------------------------

def test_confirmar_sinal_pelo_app(cen, pool):
    """Quem recebe o comprovante no WhatsApp é o vendedor, no celular — e o botão
    só existia no desktop."""
    r = _criar(pool, cen, parcelas=[{"venc": "2026-10-18", "valor_centavos": 234000}])
    out = ck.confirmar_sinal(pool, cen["conta"], r["id"], membro_id=cen["vend"])
    assert out["ok"] and _orc(pool, r["id"])["sinal_pago_em"] is not None


def test_confirmar_duas_vezes_nao_refaz_nada(cen, pool):
    """A trava de idempotência é a do `vendas.confirmar_sinal` — a mesma do botão
    do painel."""
    r = _criar(pool, cen, parcelas=[{"venc": "2026-10-18", "valor_centavos": 234000}])
    ck.confirmar_sinal(pool, cen["conta"], r["id"], membro_id=cen["vend"])
    quando = _orc(pool, r["id"])["sinal_pago_em"]
    out = ck.confirmar_sinal(pool, cen["conta"], r["id"], membro_id=cen["vend"])
    assert out["ok"] and out["ja_estava"] is True
    assert _orc(pool, r["id"])["sinal_pago_em"] == quando


def test_vendedor_nao_confirma_o_sinal_da_proposta_de_outro(cen, pool):
    """Mesma régua do fechar contrato: o vendedor mexe na proposta DELE."""
    r = _criar(pool, cen, parcelas=[{"venc": "2026-10-18", "valor_centavos": 234000}])
    with pool.connection() as c:
        outro = c.execute("insert into membros (conta_id, nome, email) "
                          "values (%s,'OUTRO','o@x.com') returning id",
                          (cen["conta"],)).fetchone()[0]
        c.commit()
    out = ck.confirmar_sinal(pool, cen["conta"], r["id"], membro_id=outro)
    assert out["ok"] is False
    assert _orc(pool, r["id"])["sinal_pago_em"] is None


def test_gestor_confirma_o_sinal_de_qualquer_um(cen, pool):
    """`membro_id=None` é o gestor — mesma porta que o `fechar_contrato` usa."""
    r = _criar(pool, cen, parcelas=[{"venc": "2026-10-18", "valor_centavos": 234000}])
    out = ck.confirmar_sinal(pool, cen["conta"], r["id"], membro_id=None)
    assert out["ok"] and _orc(pool, r["id"])["sinal_pago_em"] is not None


def test_sinal_de_proposta_inexistente(cen, pool):
    out = ck.confirmar_sinal(pool, cen["conta"], 99999, membro_id=cen["vend"])
    assert out["ok"] is False
