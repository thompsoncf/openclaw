"""A troca de situação do lead pelo painel (POST /painel/prospeccao/{id}/status).

O QUE ACONTECEU. De 11/09 a 25/09/2026, TODA troca de situação pelo painel deu
erro 500: arrastar o card no quadro, o chip da janela do lead no Funil e no
Follow-up, a ficha. O vendedor via "Deu um erro do nosso lado" e o lead ficava
onde estava. Quem seguiu movendo lead foi só o app do vendedor, que entra por
outra porta (`finance.cockpit.mudar_etapa`). Em produção dá pra ver: antes de
11/09, 14 movimentos manuais foram do dono (sem `membro_id`, só possível pelo
painel); depois, nenhum.

A causa era de Python, não de dado. A rota tinha, lá embaixo, um
`from finance import funil_regua as _fr` dentro do `try` do histórico. O #670
passou a usar `_fr` no COMEÇO da função (`_fr.recusa_de_saida`), e um `import`
dentro da função faz do nome uma variável LOCAL da função inteira — a linha de
cima passou a ler uma local ainda vazia: UnboundLocalError, em toda chamada.

POR QUE NENHUM TESTE VIU. Nenhum teste chamava esta rota. Os que olhavam para
ela (`test_painel_js_sintaxe`, `test_prospeccao_abas`) liam o TEXTO da função ou
o roteamento — e o texto estava certo. Por isso os testes daqui fazem a
requisição de verdade, pelo app inteiro (gate de papel incluído), contra um
Postgres descartável com as migrações do funil de verdade.

E o defeito é de uma CLASSE, não de uma linha: `test_nenhuma_funcao_usa_um_nome_
antes_do_import_local_dele` varre a base inteira atrás do mesmo desenho.
"""
from __future__ import annotations

import ast
import base64
import json
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

RAIZ = Path(__file__).resolve().parent.parent
MIG = RAIZ / "db" / "migracoes"
CONTA = 34
VENDEDOR = 71
OUTRO_VENDEDOR = 72

#: a `prospeccao` na forma que `_carrega_alvo` lê (a mesma de test_lead_resumo),
#: mais as colunas que a troca de situação ESCREVE — sem elas o teste passaria e a
#: produção quebraria
_SQL = """
create table contas (id bigserial primary key, chip_de bigint, nicho_id bigint);
create table nichos (id bigserial primary key, slug text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table prospeccao (id bigserial primary key, evento_em date, evento_tipo text,
  evento_convidados int, evento_origem text, evento_trecho text, evento_pista text,
  evento_lido_em timestamptz, conta_id bigint, vendedor_id bigint,
  empresa text not null, cnpj text, segmento text, cidade text, uf text,
  contato text, cargo text, telefone text, whatsapp text, email text,
  status text default 'novo', temperatura text default 'frio',
  valor_estimado_centavos bigint default 0, origem text, origem_codigo text, obs text,
  instagram text, socio text, regime_tributario text, porte text,
  ultimo_contato_em timestamptz, proximo_contato_em date,
  orcamento_id bigint, tem_site boolean, maps_url text, receita jsonb, site_url text,
  decisor_nome text, decisor_cargo text, decisor_telefone text, decisor_whatsapp boolean,
  decisor_em timestamptz, decisor_telefones jsonb,
  tipo text default 'pj', cpf text,
  cep text, endereco text, numero text, bairro text, nascimento date,
  estagio text default 'lead', origem_cliente text, perda_motivo text,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table funil_movimentos (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint, de text, para text, motivo text, membro_id bigint,
  criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, semeado_de text, conta_id bigint,
  chave text, rotulo text, ordem int default 0, fixa boolean default false,
  fase text default 'venda', prazo_min integer, gatilho text,
  gatilho_ativo boolean default false,
  criado_em timestamptz default now(), constraint uq_funil_etapa unique (conta_id, chave));
create table funil_regua (conta_id bigint primary key,
  gatilhos_modo text not null default 'off', cobranca_modo text not null default 'off',
  janela_dias text, janela_abre time, janela_fecha time,
  sem_resposta_min int, bola_nossa_min int, bola_cliente_min int,
  escala_min int, teto_avisos_dia int,
  follow_up_modo text not null default 'off', fu_proposta_dias int, fu_toques_dias text,
  fu_zap boolean not null default false, fu_festa_dias int, fu_teto_dia int,
  atualizado_em timestamptz not null default now());
"""

#: as migrações DE VERDADE, na ordem em que o Render aplica — o que a rota lê da
#: etapa (saídas, exigência de motivo, sai do quadro) nasceu nelas
_MIGRACOES = ("218_follow_up.sql", "230_funil_teto_da_etapa.sql",
              "232_funil_saidas_da_etapa.sql", "233_funil_toques_da_etapa.sql",
              "235_motivos_de_perda_da_conta.sql", "236_reativar_o_lead_que_volta.sql",
              "238_etapa_sai_do_quadro.sql", "254_funil_semeado_de.sql")

#: o funil da Prime, como aparece na janela do print de 25/09
_ETAPAS = (("novo", "Novo", 0, True, "venda"), ("contatado", "Contatado", 10, False, "venda"),
           ("agendado_visita", "Agendado Visita", 20, False, "venda"),
           ("negociacao", "Negociação", 30, False, "venda"),
           ("ganho", "Contrato Assinado", 900, True, "fechamento"),
           ("perdido", "Perdido", 910, True, "fechamento"))


@pytest.fixture(scope="module")
def _banco():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    nome = "zaq_status_do_lead_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (nome,))
        c.execute(f"drop database if exists {nome}")
        c.execute(f"create database {nome}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + nome
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        for m in _MIGRACOES:
            c.execute((MIG / m).read_text(encoding="utf-8"))
        c.execute("insert into nichos (id, slug) values (1, 'eventos')")
        c.execute("insert into contas (id, nicho_id) values (%s, 1)", (CONTA,))
        c.execute("insert into membros (id, conta_id, nome) values (%s,%s,'Jacqueline'), "
                  "(%s,%s,'Pedro Yan')", (VENDEDOR, CONTA, OUTRO_VENDEDOR, CONTA))
        for ch, rot, ordem, fixa, fase in _ETAPAS:
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa, fase,
                                                   semeado_de)
                         values (%s,%s,%s,%s,%s,%s,'dono')""",
                      (CONTA, ch, rot, ordem, fixa, fase))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def pool(_banco, monkeypatch):
    with _banco.connection() as c:
        for t in ("funil_movimentos", "prospeccao"):
            c.execute(f"delete from {t}")
        c.execute("delete from funil_motivos_perda")
        c.execute("""update funil_etapas set saidas_permitidas=null, exige_motivo=false
                      where conta_id=%s""", (CONTA,))
        c.commit()
    monkeypatch.setattr(pp, "get_pool", lambda: _banco)
    return _banco


def _cookie(dados: dict) -> str:
    """Um cookie de sessão válido pro app real (o formato do SessionMiddleware) —
    é ele que o gate de papel lê antes de a rota rodar."""
    import itsdangerous
    bruto = base64.b64encode(json.dumps(dados).encode())
    segredo = os.environ.get("PORTAL_SECRET", "troque-isto-em-producao")
    return itsdangerous.TimestampSigner(str(segredo)).sign(bruto).decode()


def _cliente(monkeypatch, *, papel="vendedor", membro_id=VENDEDOR):
    """O app INTEIRO, com o gate de papel, logado como `papel`. `_acesso` é o
    único dublê: ele monta a conta a partir de uma dúzia de tabelas que não
    interessam aqui; o que interessa é o que vem depois dele."""
    from fastapi.testclient import TestClient

    from web.app import app
    gerencia = papel in ("dono", "gestor")
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": membro_id, "gerencia": gerencia,
         "pode_atribuir": gerencia}, None))
    c = TestClient(app, follow_redirects=False)
    c.cookies.set("session", _cookie({"conta_id": CONTA, "papel": papel,
                                      "membro_id": membro_id}))
    return c


def _lead(pool, *, status="novo", vendedor_id=VENDEDOR, empresa="Liane Caroline"):
    with pool.connection() as c:
        lid = c.execute("""insert into prospeccao (conta_id, vendedor_id, empresa, status)
                           values (%s,%s,%s,%s) returning id""",
                        (CONTA, vendedor_id, empresa, status)).fetchone()[0]
        c.commit()
    return lid


def _mudar(cli, lead_id, status, **extra):
    """A requisição exata da janela do lead (`kbLeadIr` em web/janela_lead.py)."""
    return cli.post(f"/painel/prospeccao/{lead_id}/status", data={"status": status, **extra})


def _estado(pool, lead_id):
    with pool.connection() as c:
        return c.execute("select status, estagio, perda_motivo from prospeccao where id=%s",
                         (lead_id,)).fetchone()


def _movimentos(pool, lead_id):
    with pool.connection() as c:
        return c.execute("""select de, para, motivo, membro_id from funil_movimentos
                             where prospeccao_id=%s order by id""", (lead_id,)).fetchall()


# ─────────────────────────────────────────────── o caso do print (Follow-up)

def test_o_vendedor_muda_a_situacao_do_proprio_lead(pool, monkeypatch):
    """O print de 25/09: a vendedora abre a ficha pelo Follow-up e toca em
    "Contatado". Tem que mover, e o histórico tem que dizer QUEM moveu."""
    lid = _lead(pool)
    r = _mudar(_cliente(monkeypatch), lid, "contatado")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "status": "contatado", "estagio": "lead"}
    assert _estado(pool, lid)[:2] == ("contatado", "lead")
    assert _movimentos(pool, lid) == [("novo", "contatado", "manual", VENDEDOR)]


def test_o_dono_arrasta_no_quadro(pool, monkeypatch):
    """O arrastar do quadro usa a MESMA rota. O dono não tem `membro_id` — e era
    exatamente essa linha, sem autor, que sumiu de produção em 11/09."""
    lid = _lead(pool, status="contatado")
    r = _mudar(_cliente(monkeypatch, papel="dono", membro_id=None), lid, "negociacao")
    assert r.status_code == 200, r.text
    assert _estado(pool, lid)[0] == "negociacao"
    assert _movimentos(pool, lid) == [("contatado", "negociacao", "manual", None)]


def test_a_saida_barrada_explica_pra_onde_pode_ir_e_nao_move(pool, monkeypatch):
    """A trava das saídas (migração 232) é a primeira coisa que a rota faz — e é
    a linha que quebrava. Aqui ela tem que RECUSAR com a frase, não estourar."""
    with pool.connection() as c:
        c.execute("""update funil_etapas set saidas_permitidas='contatado'
                      where conta_id=%s and chave='novo'""", (CONTA,))
        c.commit()
    lid = _lead(pool)
    r = _mudar(_cliente(monkeypatch), lid, "negociacao")
    assert r.status_code == 400
    assert r.json()["erro"] == "saida"
    assert "Contatado" in r.json()["msg"]
    assert _estado(pool, lid)[0] == "novo" and _movimentos(pool, lid) == []
    # e o caminho permitido continua aberto
    assert _mudar(_cliente(monkeypatch), lid, "contatado").status_code == 200


def test_perdido_pergunta_o_motivo_antes_e_grava_depois(pool, monkeypatch):
    """O outro caminho da janela: "Perdido" com motivo obrigatório. A primeira
    volta RECUSA com a lista (a folha "por que perdeu" lê `d.motivos`); a segunda,
    com o motivo, move e grava."""
    with pool.connection() as c:
        c.execute("update funil_etapas set exige_motivo=true where conta_id=%s and chave='perdido'",
                  (CONTA,))
        c.commit()
    lid = _lead(pool, status="negociacao")
    cli = _cliente(monkeypatch)
    r = _mudar(cli, lid, "perdido")
    assert r.status_code == 400
    d = r.json()
    assert d["erro"] == "motivo_obrigatorio" and d["motivos"], d
    assert _estado(pool, lid)[0] == "negociacao", "moveu sem o motivo"

    motivo = next(m["chave"] for m in d["motivos"] if not m["exige_descricao"])
    r = _mudar(cli, lid, "perdido", motivo=motivo)
    assert r.status_code == 200, r.text
    assert _estado(pool, lid)[0] == "perdido"
    assert _movimentos(pool, lid) == [("negociacao", "perdido", "manual", VENDEDOR)]


def test_o_vendedor_nao_mexe_no_lead_de_outro(pool, monkeypatch):
    lid = _lead(pool, vendedor_id=OUTRO_VENDEDOR)
    r = _mudar(_cliente(monkeypatch), lid, "contatado")
    assert r.status_code == 403
    assert _estado(pool, lid)[0] == "novo"


def test_etapa_que_nao_existe_e_recusada(pool, monkeypatch):
    lid = _lead(pool)
    assert _mudar(_cliente(monkeypatch), lid, "inventada").status_code == 400
    assert _estado(pool, lid)[0] == "novo"


# ─────────────────────────────────────────────── a classe do defeito

#: o que não é código de produção: a própria suíte (dublês de propósito) e docs
_FORA = {"tests", "docs", ".git", "node_modules", "__pycache__", ".venv", "venv"}


def _nos_da_funcao(fn):
    """Os nós do corpo da função, SEM descer em função, classe ou lambda aninhada
    — essas têm escopo próprio, e o import de uma não sombreia a outra."""
    pilha, nos = list(fn.body), []
    while pilha:
        n = pilha.pop()
        nos.append(n)
        for f in ast.iter_child_nodes(n):
            if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                  ast.Lambda)):
                pilha.append(f)
    return nos


def _usos_antes_do_import(src: str):
    """[(função, nome, linha do uso, linha do import)] — o nome é LIDO numa linha
    anterior ao `import` local que o cria, sem nenhuma atribuição antes da leitura.
    É o desenho exato do defeito de 11/09: a leitura acha uma local vazia."""
    achados = []
    for fn in ast.walk(ast.parse(src)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nos = _nos_da_funcao(fn)
        declarados = {x for n in nos if isinstance(n, (ast.Global, ast.Nonlocal))
                      for x in n.names}
        importados: dict[str, int] = {}
        for n in nos:
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    nome = (a.asname or a.name).split(".")[0]
                    importados[nome] = min(importados.get(nome, n.lineno), n.lineno)
        for nome, linha_imp in importados.items():
            if nome in declarados:
                continue
            leituras = [n.lineno for n in nos if isinstance(n, ast.Name) and n.id == nome
                        and isinstance(n.ctx, ast.Load)]
            escritas = [n.lineno for n in nos if isinstance(n, ast.Name) and n.id == nome
                        and isinstance(n.ctx, ast.Store)]
            escritas += [a.lineno for a in (fn.args.args + fn.args.kwonlyargs
                                            + fn.args.posonlyargs) if a.arg == nome]
            antes = [x for x in leituras if x < linha_imp]
            if antes and not any(e <= min(antes) for e in escritas):
                achados.append((fn.name, nome, min(antes), linha_imp))
    return achados


def test_o_detector_pega_o_defeito_de_11_09():
    """O detector abaixo só vale se pegar o caso real — escrito na forma exata
    em que a rota estava."""
    src = (
        "from finance import funil_regua as _fr\n"
        "def rota():\n"
        "    recusa = _fr.recusa_de_saida()\n"
        "    try:\n"
        "        from finance import funil_regua as _fr\n"
        "        _fr.registrar_movimento()\n"
        "    except Exception:\n"
        "        pass\n")
    assert _usos_antes_do_import(src) == [("rota", "_fr", 3, 5)]
    # e o desenho legítimo (atribui None antes, importa quando precisa) não é defeito
    ok = ("def laco():\n"
          "    _m = None\n"
          "    if _m is None:\n"
          "        from finance import campanhas_motor as _m\n")
    assert _usos_antes_do_import(ok) == []


def test_nenhuma_funcao_usa_um_nome_antes_do_import_local_dele():
    """Um `import` dentro da função faz do nome uma variável LOCAL da função
    INTEIRA. Se o mesmo nome também existe no topo do módulo, toda leitura acima
    do import deixa de ver o do módulo e estoura com UnboundLocalError — e só na
    hora em que a linha roda, nunca no deploy.

    O conserto é sempre o mesmo: usar o import do topo, ou importar no começo
    da função."""
    achados, lidos = [], 0
    for p in sorted(RAIZ.rglob("*.py")):
        if _FORA & set(p.relative_to(RAIZ).parts):
            continue
        try:
            nesta = _usos_antes_do_import(p.read_text(encoding="utf-8"))
        except SyntaxError:
            # só as sondas soltas da raiz (`sonda_token3.py`, f-string do 3.12):
            # arquivo que nem compila neste Python não é o que este teste protege
            continue
        lidos += 1
        for fn, nome, uso, imp in nesta:
            achados.append(f"{p.relative_to(RAIZ)}:{uso} {fn}() lê `{nome}` antes do "
                           f"`import` local da linha {imp}")
    assert lidos > 100, f"a varredura leu só {lidos} arquivos — o caminho mudou?"
    assert not achados, "\n".join(achados)
