"""Contas a pagar: o fornecedor que não entrava mais, e a liberação do dono.

DUAS FRENTES PEDIDAS PELO DONO EM 03/09/2026, e elas se encontram num ponto só —
o título tinha campos que ninguém conseguia preencher depois de salvar.

FRENTE 1 · O FORNECEDOR. O relato foi: "depois de cadastrar uma conta a pagar e
não colocar o fornecedor, a empresa não consegue mais colocar". Confirmado no
código: `editar_titulo` aceitava `descricao` e `valor_centavos`, e a rota do
editar também. Não era campo escondido — não existia.

O efeito medido na Prime no mesmo dia: **30 de 30 títulos a pagar sem
fornecedor**, com o nome enfiado na descrição ("ZARB CONSULTORIA", "EQUATORIAL",
"BANCO DO NORDESTE"). Quem lança tinha achado o jeito de não perder o dado.

FRENTE 2 · A LIBERAÇÃO. O pedido veio como uma lista só — "PAGO / PENDENTE /
ATENÇÃO ATRASADO / AUTORIZADO A PAGAR". São três perguntas diferentes e um
título responde as três ao mesmo tempo (ver o cabeçalho da migração 195):
o dinheiro saiu (`status`), está no prazo (conta de `vencimento`) e o dono
liberou (`aprovacao`, nova). O caso que uma lista única não sabe escrever —
conta AUTORIZADA que VENCEU — tem teste próprio aqui.

Duas escolhas do dono que este arquivo trava, porque são as que um refactor
futuro mais provavelmente desfaz sem perceber:

  * **só avisa, não trava** — a baixa de conta não liberada CONTINUA
    funcionando, e o que sobra é a marca `pago_sem_autorizacao`. Se alguém
    "melhorar" isso pra bloquear, dois testes quebram.
  * **toda conta** — não existe piso de valor. Uma conta de R$ 0,50 lançada por
    quem não é dono espera liberação igual a uma de R$ 5.000.
"""
import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp

_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "053_modulo_pj.sql",
              "058_dados_empresa.sql",      # contas.nome_fantasia (o aviso usa)
              "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql",
              "057_natureza_lancamento.sql",
              "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql",
              "067_titulos_cliente.sql",
              "131_pessoa_cnpj.sql")

CONTA = 501
OUTRA = 502
HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    """BANCO PRÓPRIO, e não o compartilhado da suíte.

    `aprovacao_aviso.rodar` é um ticker: ele varre TODAS as contas que têm conta a
    pagar esperando liberação — é isso que ele faz em produção, e não dá pra
    escopar sem descaracterizar o que está sendo testado. No banco compartilhado
    isso torna o resultado dependente de qual arquivo rodou antes (a suíte roda em
    ordem aleatória), e teste que passa ou falha pela ordem é pior que teste
    nenhum: ensina a ignorar vermelho."""
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True)
    dbname = "zaq_contas_pagar_aprovacao"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    # `membros.email` nasce no guard de runtime da Equipe, não no init_schema — e
    # `listar_titulos` lê o nome de quem lançou e de quem liberou por ali.
    from contas import equipe as _eq
    _eq.garantir_tabela(p)
    with p.connection() as c:
        for cid, nome in ((CONTA, "Prime Eventos"), (OUTRA, "Vizinha")):
            c.execute("insert into contas (id, nome, tipo) values (%s,%s,'pj') "
                      "on conflict (id) do nothing", (cid, nome))
        c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def limpo(pool):
    with pool.connection() as c:
        c.execute("delete from titulos where conta_id in (%s,%s)", (CONTA, OUTRA))
        c.commit()


def _titulo(pool, **kw):
    kw.setdefault("tipo", "pagar")
    kw.setdefault("descricao", "ENERGIA SOLAR")
    kw.setdefault("valor_centavos", 287777)
    kw.setdefault("vencimento", HOJE + timedelta(days=5))
    conta = kw.pop("conta_id", CONTA)
    return emp.criar_titulo(pool, conta, **kw)["id"]


def _um(pool, tid, conta=CONTA):
    for s in ("aberto", "pago", "cancelado"):
        for t in emp.listar_titulos(pool, conta, status=s):
            if t["id"] == tid:
                return t
    return None


# ======================================================================== 1
# O FORNECEDOR

def test_o_fornecedor_entra_depois_de_salvar(pool):
    """O relato do dono, ponta a ponta: salvou sem, agora coloca."""
    tid = _titulo(pool)
    assert _um(pool, tid)["contraparte"] == ""
    assert emp.editar_titulo(pool, CONTA, tid, contraparte="Equatorial") is True
    assert _um(pool, tid)["contraparte"] == "Equatorial"


def test_sem_fornecedor_e_um_selo_e_ele_some_sozinho(pool):
    """O selo é o que faz os 30 que já existem serem achados — sem ele ninguém
    abre linha por linha pra descobrir quais faltam."""
    tid = _titulo(pool)
    assert _um(pool, tid)["sem_fornecedor"] is True
    emp.editar_titulo(pool, CONTA, tid, contraparte="Equatorial")
    assert _um(pool, tid)["sem_fornecedor"] is False


def test_ficha_ligada_tambem_conta_como_ter_fornecedor(pool):
    """`sem_fornecedor` é "nem texto NEM ficha" — quem tem só o vínculo não é
    cobrado de novo."""
    tid = _titulo(pool)
    with pool.connection() as c:
        cli = c.execute("insert into pessoas (tipo, nome) values ('pf','Equatorial') "
                        "returning id").fetchone()[0]
        cid = c.execute("insert into clientes (dono_id, pessoa_id, nome) "
                        "values (%s,%s,'Equatorial') returning id",
                        (CONTA, cli)).fetchone()[0]
        c.commit()
    emp.editar_titulo(pool, CONTA, tid, cliente_id=cid)
    assert _um(pool, tid)["sem_fornecedor"] is False


def test_apagar_o_fornecedor_e_permitido(pool):
    """Ao contrário da descrição, fornecedor vazio APAGA: ligar no errado é uma
    correção legítima, e nome errado é pior que nome nenhum."""
    tid = _titulo(pool, contraparte="Errado Ltda")
    emp.editar_titulo(pool, CONTA, tid, contraparte="")
    assert _um(pool, tid)["contraparte"] == ""


def test_editar_so_a_descricao_nao_apaga_o_fornecedor(pool):
    """`contraparte=None` é "não mexe". Sem isso, corrigir um typo na descrição
    limparia o fornecedor de quem já tinha."""
    tid = _titulo(pool, contraparte="Equatorial")
    emp.editar_titulo(pool, CONTA, tid, descricao="ENERGIA SOLAR (setembro)")
    t = _um(pool, tid)
    assert t["contraparte"] == "Equatorial" and "setembro" in t["descricao"]


def test_receber_nao_ganha_selo_de_fornecedor(pool):
    """Título a receber não tem fornecedor — cobrar isso seria alarme falso."""
    tid = _titulo(pool, tipo="receber", descricao="Honorário")
    assert _um(pool, tid)["sem_fornecedor"] is False


def test_quem_lancou_fica_gravado(pool):
    """A rota passava criado_por=None cravado, e por isso os 30 títulos da Prime
    tinham autor nulo. Sem isso a liberação aprova sem saber de quem."""
    with pool.connection() as c:
        m = c.execute("insert into membros (conta_id, nome) values (%s,'Jacqueline') "
                      "returning id", (CONTA,)).fetchone()[0]
        c.commit()
    tid = _titulo(pool, criado_por=m)
    assert _um(pool, tid)["criado_nome"] == "Jacqueline"


# ======================================================================== 2
# A LIBERAÇÃO DO DONO

def test_TODA_conta_a_pagar_nasce_aguardando(pool):
    """A regra da casa desde 04/09/2026, escolhida pelo dono depois que a primeira
    versão não acendeu: **tudo espera, inclusive o que ele mesmo lança.**

    Antes a regra era "aguarda quem não é dono", e morava na tela. Na Prime só o
    dono abre o financeiro (os três do time são `vendedor`), então nenhum título
    jamais nasceu aguardando — a funcionalidade estava no ar e invisível."""
    assert _um(pool, _titulo(pool))["aprovacao"] == "aguardando"


def test_a_receber_nao_espera(pool):
    """Dinheiro entrando ninguém precisa autorizar."""
    tid = _titulo(pool, tipo="receber", descricao="Honorário")
    assert _um(pool, tid)["aprovacao"] == "autorizado"


def test_a_regra_vale_pra_toda_porta_que_cria_titulo(pool):
    """A regra mora no `criar_titulo`, não na tela — senão cada porta teria a sua
    cópia. O agente do WhatsApp (tools_pj) também cria conta a pagar, e na versão
    anterior ele não tinha cópia nenhuma dessa regra."""
    import inspect
    from web import portal
    fonte_rota = inspect.getsource(portal.empresa_titulo_criar)
    assert "precisa_aprovacao" not in fonte_rota, (
        "a tela voltou a decidir quem espera — a regra é do negócio, não da tela")
    assert 'precisa_aprovacao = (tipo == "pagar")' in inspect.getsource(emp.criar_titulo)


def test_escape_explicito_ainda_existe(pool):
    """O parâmetro continua servindo pra quem tem motivo — hoje, a recorrente."""
    tid = _titulo(pool, precisa_aprovacao=False)
    assert _um(pool, tid)["aprovacao"] == "autorizado"


def test_o_dono_libera(pool):
    tid = _titulo(pool, precisa_aprovacao=True)
    with pool.connection() as c:
        dono = c.execute("insert into membros (conta_id, nome) values (%s,'Manoel') "
                         "returning id", (CONTA,)).fetchone()[0]
        c.commit()
    assert emp.decidir_aprovacao(pool, CONTA, tid, "autorizado", membro_id=dono) == 1
    t = _um(pool, tid)
    assert t["aprovacao"] == "autorizado" and t["aprovado_nome"] == "Manoel"
    assert t["aprovado_em"] is not None


def test_recusa_guarda_o_motivo_e_nao_apaga_nada(pool):
    """Recusar não é apagar: o título continua lá, com o porquê, pra quem lançou
    corrigir."""
    tid = _titulo(pool, precisa_aprovacao=True)
    emp.decidir_aprovacao(pool, CONTA, tid, "recusado", motivo="conferir a leitura antes")
    t = _um(pool, tid)
    assert t["aprovacao"] == "recusado"
    assert t["aprovacao_motivo"] == "conferir a leitura antes"
    assert t["status"] == "aberto" and t["valor_centavos"] == 287777


def test_libera_em_lote(pool):
    """30 títulos de uma vez foi o caso real da Prime. Um a um, o controle é
    desligado na semana seguinte."""
    ids = [_titulo(pool, descricao=f"C{i}", precisa_aprovacao=True) for i in range(4)]
    assert emp.decidir_aprovacao(pool, CONTA, ids, "autorizado") == 4
    assert all(_um(pool, i)["aprovacao"] == "autorizado" for i in ids)


def test_a_fila_do_dono_so_traz_o_que_espera(pool):
    _titulo(pool, descricao="JA LIBERADO", precisa_aprovacao=False)
    esperando = _titulo(pool, descricao="ESPERANDO")
    fila = emp.aguardando_aprovacao(pool, CONTA)
    assert [f["id"] for f in fila] == [esperando]


def test_a_fila_nao_atravessa_conta(pool):
    _titulo(pool, conta_id=OUTRA, precisa_aprovacao=True)
    assert emp.aguardando_aprovacao(pool, CONTA) == []
    assert emp.decidir_aprovacao(pool, CONTA, [999999], "autorizado") == 0


def test_decisao_invalida_e_recusada(pool):
    with pytest.raises(ValueError):
        emp.decidir_aprovacao(pool, CONTA, [1], "mais_ou_menos")


# ---- as três perguntas são independentes -------------------------------

def test_autorizado_E_atrasado_convivem(pool):
    """O caso que a lista única não sabia escrever, e o mais urgente que existe:
    a conta está liberada, era pra ter saído, e venceu sem ninguém pagar. Num
    campo só seria preciso escolher entre "autorizado" e "atrasado" — e some
    justamente a metade que faria alguém agir."""
    tid = _titulo(pool, vencimento=HOJE - timedelta(days=19), precisa_aprovacao=True)
    emp.decidir_aprovacao(pool, CONTA, tid, "autorizado")
    t = _um(pool, tid)
    assert t["aprovacao"] == "autorizado"
    assert t["atrasado"] is True
    assert t["status"] == "aberto"


def test_liberar_nao_paga_e_pagar_nao_libera(pool):
    """Os dois eixos não se contaminam: liberar não mexe no dinheiro."""
    tid = _titulo(pool, precisa_aprovacao=True)
    emp.decidir_aprovacao(pool, CONTA, tid, "autorizado")
    assert _um(pool, tid)["status"] == "aberto"


def test_decidir_nao_mexe_no_que_ja_foi_pago(pool):
    """Reabrir a discussão do que já saiu não muda o dinheiro e só embaralha o
    histórico. O que foi pago sem liberação tem marca própria."""
    tid = _titulo(pool, precisa_aprovacao=True)
    emp.dar_baixa_titulo(pool, CONTA, tid, HOJE)
    assert emp.decidir_aprovacao(pool, CONTA, tid, "autorizado") == 0


def test_a_recorrente_herda_a_decisao_do_mes_anterior(pool):
    """O aluguel liberado em janeiro não volta a perguntar em fevereiro.

    Com "tudo nasce aguardando", a parcela seguinte de um recorrente seria uma
    pergunta nova todo mês sobre algo já respondido — e fila que repete pergunta
    respondida é fila que alguém desliga. A parcela do mês que vem não é decisão
    nova: é a mesma, continuando."""
    tid = _titulo(pool, descricao="ALUGUEL", recorrente=True)
    emp.decidir_aprovacao(pool, CONTA, tid, "autorizado")
    r = emp.dar_baixa_titulo(pool, CONTA, tid, HOJE)
    prox = r["proximo_titulo_id"]
    assert prox, "recorrente tinha que gerar a próxima"
    assert _um(pool, prox)["aprovacao"] == "autorizado"


def test_a_recorrente_nao_liberada_continua_esperando(pool):
    """Herdar vale nos dois sentidos: o que passou sem liberação não vira
    liberado no mês seguinte por descuido."""
    tid = _titulo(pool, descricao="ALUGUEL", recorrente=True)
    r = emp.dar_baixa_titulo(pool, CONTA, tid, HOJE)   # baixa sem liberar
    assert _um(pool, r["proximo_titulo_id"])["aprovacao"] == "aguardando"


# ---- "só avisa, não trava" (decisão do dono em 03/09/2026) --------------

def test_a_baixa_sem_liberacao_PASSA(pool):
    """A escolha foi B: avisar, não travar. Se alguém "melhorar" isto pra
    bloquear, é uma mudança de política — não um conserto."""
    tid = _titulo(pool, precisa_aprovacao=True)
    r = emp.dar_baixa_titulo(pool, CONTA, tid, HOJE)
    assert r["ok"] is True
    assert _um(pool, tid)["status"] == "pago"


def test_a_baixa_sem_liberacao_FICA_MARCADA(pool):
    """É o que dá peso à escolha B: sem a marca, o aviso da tela seria um clique
    a mais e ninguém saberia depois o que passou por fora."""
    tid = _titulo(pool, precisa_aprovacao=True)
    r = emp.dar_baixa_titulo(pool, CONTA, tid, HOJE)
    assert r["sem_autorizacao"] is True
    assert _um(pool, tid)["pago_sem_autorizacao"] is True


def test_a_baixa_do_que_foi_liberado_nao_fica_marcada(pool):
    tid = _titulo(pool, precisa_aprovacao=True)
    emp.decidir_aprovacao(pool, CONTA, tid, "autorizado")
    r = emp.dar_baixa_titulo(pool, CONTA, tid, HOJE)
    assert r["sem_autorizacao"] is False
    assert _um(pool, tid)["pago_sem_autorizacao"] is False


def test_baixa_de_recusado_tambem_marca(pool):
    tid = _titulo(pool, precisa_aprovacao=True)
    emp.decidir_aprovacao(pool, CONTA, tid, "recusado", motivo="não reconheço")
    assert emp.dar_baixa_titulo(pool, CONTA, tid, HOJE)["sem_autorizacao"] is True


def test_nao_existe_piso_de_valor(pool):
    """O dono escolheu "toda conta". Meio real espera liberação igual a cinco
    mil — se um piso aparecer um dia, é decisão nova, não detalhe."""
    barato = _titulo(pool, descricao="CAFE", valor_centavos=50, precisa_aprovacao=True)
    caro = _titulo(pool, descricao="OBRA", valor_centavos=500000, precisa_aprovacao=True)
    assert {f["id"] for f in emp.aguardando_aprovacao(pool, CONTA)} == {barato, caro}


# ======================================================================== 3
# O AVISO PRO DONO — um por LOTE

def test_a_assinatura_e_o_conjunto_nao_o_instante():
    """O dedup guarda QUEM está na fila, não quando avisou. Por instante, o aviso
    repetiria a cada passada enquanto a fila não esvaziasse."""
    from finance import aprovacao_aviso as ap
    assert ap.assinatura([3, 1, 2]) == ap.assinatura([2, 3, 1]) == "1,2,3"
    assert ap.assinatura([1, 2]) != ap.assinatura([1, 2, 3])


def test_um_aviso_por_lote_e_nao_um_por_conta(pool, monkeypatch):
    """Numa manhã a Prime cadastrou 30 títulos. Trinta mensagens seguidas não são
    trinta avisos — são um aviso e vinte e nove motivos pra desligar tudo."""
    from finance import aprovacao_aviso as ap
    for i in range(4):
        _titulo(pool, descricao=f"CONTA {i}", precisa_aprovacao=True)
    saiu = []
    monkeypatch.setattr(ap.notificar, "enviar_para_dono",
                        lambda p, cid, txt: saiu.append((cid, txt)) or True)
    monkeypatch.setattr(ap.config_app, "get_config", lambda *a, **k: None)
    monkeypatch.setattr(ap.config_app, "set_config", lambda *a, **k: None)
    ap.rodar(pool, hora_brt=14)
    # escopado na CONTA: `rodar` varre todas as contas de propósito (é ticker), e
    # a suíte inteira divide o mesmo banco — contar o total seria contar sobra
    # dos outros arquivos.
    meus = [t for cid, t in saiu if cid == CONTA]
    assert len(meus) == 1, "o lote tem que virar UMA mensagem, não uma por conta"
    assert "4 contas a pagar" in meus[0]


def test_a_mesma_fila_nao_avisa_de_novo(pool, monkeypatch):
    from finance import aprovacao_aviso as ap
    _titulo(pool, precisa_aprovacao=True)
    marca, saiu = {}, []
    monkeypatch.setattr(ap.notificar, "enviar_para_dono",
                        lambda p, cid, txt: saiu.append(cid) or True)
    monkeypatch.setattr(ap.config_app, "get_config", lambda p, k, d=None: marca.get(k, d))
    monkeypatch.setattr(ap.config_app, "set_config",
                        lambda p, k, v: marca.__setitem__(k, v))
    ap.rodar(pool, hora_brt=14)
    ap.rodar(pool, hora_brt=14)
    assert saiu.count(CONTA) == 1, "a mesma fila avisou duas vezes"


def test_conta_nova_na_fila_avisa_de_novo_com_a_lista_inteira(pool, monkeypatch):
    """A pergunta que o dono responde é "o que falta eu liberar?", não "o que
    chegou agora?" — então o segundo aviso repete a lista toda."""
    from finance import aprovacao_aviso as ap
    _titulo(pool, descricao="PRIMEIRA", precisa_aprovacao=True)
    marca, saiu = {}, []
    monkeypatch.setattr(ap.notificar, "enviar_para_dono",
                        lambda p, cid, txt: saiu.append((cid, txt)) or True)
    monkeypatch.setattr(ap.config_app, "get_config", lambda p, k, d=None: marca.get(k, d))
    monkeypatch.setattr(ap.config_app, "set_config", lambda p, k, v: marca.__setitem__(k, v))
    ap.rodar(pool, hora_brt=14)
    _titulo(pool, descricao="SEGUNDA", precisa_aprovacao=True)
    ap.rodar(pool, hora_brt=14)
    meus = [t for cid, t in saiu if cid == CONTA]
    assert len(meus) == 2, "a fila mudou e o dono não foi avisado de novo"
    assert "PRIMEIRA" in meus[-1] and "SEGUNDA" in meus[-1]


def test_fora_do_horario_comercial_espera(pool, monkeypatch):
    from finance import aprovacao_aviso as ap
    _titulo(pool, precisa_aprovacao=True)
    saiu = []
    monkeypatch.setattr(ap.notificar, "enviar_para_dono",
                        lambda p, cid, txt: saiu.append(cid) or True)
    ap.rodar(pool, hora_brt=3)
    ap.rodar(pool, hora_brt=22)
    assert CONTA not in saiu


def test_o_texto_diz_o_total_e_o_que_fazer():
    from finance import aprovacao_aviso as ap
    itens = [{"id": 1, "descricao": "INTERNET", "valor_centavos": 9490,
              "vencimento": date(2026, 9, 10), "quem": "Jacqueline"},
             {"id": 2, "descricao": "PISCINEIRO", "valor_centavos": 27000,
              "vencimento": date(2026, 9, 15), "quem": "Jacqueline"}]
    tg, email = ap.texto("PRIME EVENTOS", itens)
    for t in (tg, email):
        assert "R$ 364,90" in t          # o total decide se ele para pra olhar
        assert "INTERNET" in t and "PISCINEIRO" in t
        assert "Empresa" in t
    # o e-mail diz que ninguém fica travado — é a política escolhida
    assert "sem a sua liberação" in email


def test_lista_longa_vira_mais_N():
    """Vinte linhas não cabem em push nenhum."""
    from finance import aprovacao_aviso as ap
    itens = [{"id": i, "descricao": f"C{i}", "valor_centavos": 100,
              "vencimento": date(2026, 9, 10), "quem": ""} for i in range(9)]
    tg, _ = ap.texto("X", itens)
    assert "+4 outras" in tg


# ======================================================================== 4
# A TELA

def test_a_tela_mostra_o_selo_e_os_botoes():
    from web import portal
    tpl = portal._EMPRESA
    assert "aguardando {{ 'você' if pode_liberar else 'o dono' }}" in tpl
    assert "sem fornecedor" in tpl
    assert 'name="cliente"' in tpl and 'name="tem_cliente"' in tpl, (
        "o editar precisa do campo de fornecedor — era o buraco da frente 1")


def test_a_baixa_avisa_mas_o_botao_continua_vivo():
    """A trava seria `disabled`; o aviso é `confirm`. Se um dia virar disabled, a
    política mudou."""
    from web import portal
    tpl = portal._EMPRESA
    i = tpl.index('/painel/empresa/titulo/{{ t.id }}/baixa')
    trecho = tpl[i:i + 700]
    assert "confirm(" in trecho and "sem autorização" in trecho
    assert "disabled" not in trecho


def test_so_quem_tem_gerir_ve_os_botoes_de_decisao():
    from web import portal
    from contas import equipe as eq
    assert eq.caps_do_papel("dono")["gerir"] is True
    for papel in ("gestor", "financeiro", "vendedor"):
        assert eq.caps_do_papel(papel)["gerir"] is False, papel
    # A condição ganhou `pode_decidir` em 04/09/2026, quando a lista virou dois
    # blocos: liberar e recusar só existem no de baixo, e param de aparecer no que
    # já foi decidido. O que este teste guarda é o `pode_liberar` — sem ele, quem
    # não é dono veria botão que o servidor recusa.
    for guarda in ("pode_liberar and t.tipo=='pagar' and t.aprovacao!='autorizado'",
                   "pode_liberar and t.aprovacao=='aguardando'"):
        assert guarda in portal._EMPRESA, guarda


def test_a_rota_de_decisao_barra_quem_nao_e_dono():
    import inspect
    from web import portal
    fonte = inspect.getsource(portal.empresa_titulo_aprovacao)
    assert "_so_o_dono" in fonte
    assert fonte.index("_so_o_dono") < fonte.index("decidir_aprovacao")
