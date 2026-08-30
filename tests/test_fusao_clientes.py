"""Juntar cadastros repetidos sem perder nada.

O conserto de 29/08/2026 fechou a torneira — salvar de novo passou a atualizar
em vez de cunhar um registro. Sobrou a poça: na Prime Eventos, 5 cadastros a
mais, de 4 pessoas. Este arquivo é sobre esvaziar a poça sem quebrar o balde.

A regra que manda aqui é a 0 do CLAUDE.md: informação de cliente não se perde.
Traduzida em teste, ela vira quatro exigências, e todas estão cobertas abaixo:

  1. o cadastro absorvido é ARQUIVADO, nunca apagado (a linha continua no banco);
  2. título, lançamento e orçamento dele mudam de dono junto — nada fica órfão
     apontando pra uma ficha que sumiu da lista;
  3. campo preenchido no que fica JAMAIS é sobrescrito pelo que sai;
  4. dá pra desfazer, e desfazer devolve tudo — inclusive as referências.

Os quatro casos reais da Prime estão aqui como teste, cada um pego por uma régua
diferente: Ana Clara (três vezes, mesmo telefone), Victoria/Visctoria (telefone
igual, nome com um "s" a mais), Gilvan (nome idêntico, segundo cadastro vazio só
pra marcar fornecedor) e Ronaldo VAZ/VEZ — que não tem telefone nem documento no
segundo cadastro e só é pego pela semelhança do nome.
"""
import os
import random
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import clientes as cli
from finance import dedup_clientes as dd

# ordem importa: 053 cria `titulos`, 064 cria `clientes`, e a 067 liga uma na
# outra. 070 dá conta_id ao orçamento (é por ele que a fusão isola o lojista).
_MIGRACOES = ("045_orcamentos.sql",
              "053_modulo_pj.sql",
              "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql",
              "067_titulos_cliente.sql",
              "068_orcamento_pipeline.sql",
              "070_orcamento_conta.sql",
              "131_pessoa_cnpj.sql",
              "149_cliente_cidade_uf.sql",
              "152_orcamento_cliente_vinculo.sql",
              "182_clientes_papel.sql",
              "191_clientes_fusao.sql")


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with p.connection() as c:
        # `orcamentos` é a tabela mais remendada da suíte: alguns módulos a criam
        # na mão com só as colunas de que precisam, e o `if not exists` das
        # migrações vira no-op em cima da versão capenga do vizinho. Aqui ela é
        # refeita do zero pelas migrações — só assim `cliente_id` existe pra
        # fusão ter o que mover.
        c.execute("drop table if exists orcamentos cascade")
        c.commit()
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def dono(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Teste Fusão') "
                        "returning id").fetchone()[0]
        c.commit()
    return cid


def _ativo(pool, cliente_id: int) -> bool:
    with pool.connection() as c:
        return c.execute("select ativo from clientes where id=%s",
                         (cliente_id,)).fetchone()[0]


def _existe(pool, cliente_id: int) -> bool:
    with pool.connection() as c:
        return c.execute("select count(*) from clientes where id=%s",
                         (cliente_id,)).fetchone()[0] == 1


def _pessoa(pool, nome, celular=None):
    """Cria uma identidade NOVA sem documento — é assim que nasce o lead do
    WhatsApp, e é por isso que ele duplicava."""
    with pool.connection() as c:
        pid = c.execute(
            "insert into pessoas (tipo, nome, celular) values ('pf',%s,%s) returning id",
            (nome, celular)).fetchone()[0]
        c.commit()
    return pid


# Um lote de documentos novo a cada rodada. Dois motivos, os dois descobertos
# escrevendo este arquivo: (1) reusar o mesmo CPF em dois testes faz
# `resolver_pessoa` reaproveitar a identidade do primeiro — comportamento certo
# dela, e um jeito silencioso de escrever um teste que passa medindo outra
# coisa; (2) o banco de teste sobrevive entre rodadas, então CPF fixo reencontra
# a PESSOA da rodada anterior, com o nome que ela tinha lá — e o teste quebra ao
# mudar um nome, sem que nada de errado tenha acontecido.
_LOTE = random.randrange(10_000, 100_000)


def _cpf(n: int) -> str:
    """Um CPF válido, distinto por teste e distinto por rodada."""
    d = f"{_LOTE}{n:04d}"
    for _ in range(2):
        s = sum(int(x) * p for x, p in zip(d, range(len(d) + 1, 1, -1)))
        d += str((s * 10 % 11) % 10)
    return d


def _titulo(pool, dono_id, cliente_id) -> int:
    with pool.connection() as c:
        tid = c.execute(
            """insert into titulos (conta_id, tipo, descricao, valor_centavos,
                                    vencimento, cliente_id)
               values (%s,'receber','Sinal do salão',50000,current_date,%s)
               returning id""", (dono_id, cliente_id)).fetchone()[0]
        c.commit()
    return tid


def _orcamento(pool, dono_id, cliente_id) -> int:
    with pool.connection() as c:
        oid = c.execute(
            "insert into orcamentos (conta_id, cliente, cliente_id) "
            "values (%s,'Festa',%s) returning id", (dono_id, cliente_id)).fetchone()[0]
        c.commit()
    return oid


# ----------------------------------------------------- a régua do nome parecido
# Função pura: dá pra fixar caso a caso sem banco. É a régua mais frouxa das
# quatro e a única que pode acusar gente diferente, então é a que mais precisa
# de exemplo escrito.

@pytest.mark.parametrize("a, b, parecidos", [
    # os dois casos reais da Prime: uma letra dentro de uma palavra de verdade
    ("RONALDO VAZ DE ALMEIDA", "RONALDO VEZ DE ALMEIDA", True),
    ("Victoria Caroline de Andrade", "Visctoria Caroline de Andrade", True),
    ("Ana Beatriz Nogueira", "Ana Beatrix Nogueira", True),
    ("Pedro Henrique Sales", "Pedro Henrique Salles", True),
    # inicial do meio não diz nada: são duas pessoas, e a versão que comparava a
    # string inteira aceitava este par (0.95 de semelhança)
    ("Fernanda G Oliveira", "Fernanda H Oliveira", False),
    # nome curto não tem contexto pra afirmar nada
    ("Ana B Silva", "Ana C Silva", False),
    # três letras de diferença já é outro nome
    ("Maria Souza Lima", "Marcos Souza Lima", False),
    ("José Ribamar Filho", "José Ribamar Neto", False),
    # duas palavras diferentes ao mesmo tempo não é erro de digitação
    ("Carlos Alberto Fontenele", "Carlos Alberta Fontenelo", False),
    # igual não é "parecido" — quem pega isso é a régua do nome exato
    ("Ana Clara Nogueira", "Ana Clara Nogueira", False),
])
def test_regua_do_nome_parecido(a, b, parecidos):
    assert dd._quase_igual(dd._norm(a), dd._norm(b)) is parecidos


# --------------------------------------------------------------- achar os pares

def test_ana_clara_tres_vezes_sai_como_um_grupo_so(pool, dono):
    """25/08/2026: três cadastros em cinco minutos, mesmo telefone. Os três pares
    possíveis têm que virar UM aviso de três linhas, não três avisos."""
    for _ in range(3):
        cli.puxar_ou_criar_cliente(
            pool, dono, pessoa_id=_pessoa(pool, "Ana Clara Nogueira",
                                          "558695000003"))
    grupos = dd.candidatos(pool, dono)
    assert len(grupos) == 1
    assert len(grupos[0]["clientes"]) == 3
    assert set(grupos[0]["motivos"].values()) == {"mesmo telefone"}


def test_victoria_com_s_a_mais_e_pega_pelo_telefone(pool, dono):
    """O nome tem erro de digitação; o telefone é o mesmo. Agrupar por nome não
    acharia — foi assim que esse par passou batido na primeira contagem."""
    a = cli.puxar_ou_criar_cliente(
        pool, dono, pessoa_id=_pessoa(pool, "Victoria Caroline de Andrade",
                                      "8695000011"))
    b = cli.puxar_ou_criar_cliente(
        pool, dono, pessoa_id=_pessoa(pool, "Visctoria Caroline de Andrade",
                                      "8695000011"))
    g = dd.candidatos(pool, dono)
    assert len(g) == 1
    assert {c["id"] for c in g[0]["clientes"]} == {a, b}


def test_telefone_casa_com_e_sem_o_55_na_frente(pool, dono):
    """Digitado à mão o número vem sem o país ('86 9…'); vindo do WhatsApp vem
    com o 55 na frente. Comparando texto exato eles nunca se encontram — é a
    diferença que fazia a mesma pessoa duplicar. (Os números aqui são de teste;
    o que importa é a forma.)"""
    a = cli.puxar_ou_criar_cliente(pool, dono,
                                   pessoa_id=_pessoa(pool, "Marcos Aurélio Pontes", "8695000015"))
    b = cli.puxar_ou_criar_cliente(pool, dono,
                                   pessoa_id=_pessoa(pool, "Marcos A. Pontes", "558695000015"))
    g = dd.candidatos(pool, dono)
    assert len(g) == 1 and {c["id"] for c in g[0]["clientes"]} == {a, b}


def test_gilvan_segundo_cadastro_vazio_e_pego_pelo_nome_igual(pool, dono):
    """O segundo cadastro do Gilvan nasceu só pra marcar 'fornecedor': sem
    telefone, sem CPF, nome idêntico. Só a régua do nome pega."""
    a = cli.criar_cliente(pool, dono, "GILVAN PEREIRA DE MATOS",
                          telefone="8695000015", cpf=_cpf(1))
    b = cli.puxar_ou_criar_cliente(
        pool, dono, pessoa_id=_pessoa(pool, "GILVAN PEREIRA DE MATOS"),
        eh_cliente=False, eh_fornecedor=True)
    g = dd.candidatos(pool, dono)
    assert len(g) == 1
    assert g[0]["sugerido"] == a, "quem fica é o cadastro completo, não o vazio"
    assert g[0]["motivos"][b] == "mesmo nome"


def test_ronaldo_vaz_e_vez_e_pego_pela_semelhanca_do_nome(pool, dono):
    """Uma letra de diferença, e nada mais pra comparar. É o único dos quatro
    casos reais que exige a régua difusa."""
    a = cli.criar_cliente(pool, dono, "RONALDO VAZ DE ALMEIDA",
                          telefone="8695000009", cpf=_cpf(2))
    b = cli.puxar_ou_criar_cliente(
        pool, dono, pessoa_id=_pessoa(pool, "RONALDO VEZ DE ALMEIDA"),
        eh_cliente=False, eh_fornecedor=True)
    g = dd.candidatos(pool, dono)
    assert len(g) == 1
    assert set(g[0]["motivos"].values()) == {"nome muito parecido"}
    assert {c["id"] for c in g[0]["clientes"]} == {a, b}


def test_sobrenome_igual_com_nome_diferente_nao_e_duplicata(pool, dono):
    """A régua difusa não pode virar uma máquina de juntar irmão com irmã."""
    cli.criar_cliente(pool, dono, "Maria Souza Lima", telefone="8695000004")
    cli.criar_cliente(pool, dono, "Marcos Souza Lima", telefone="8695000005")
    assert dd.candidatos(pool, dono) == []


def test_cpfs_diferentes_nunca_viram_par_nem_com_nome_igual(pool, dono):
    """Pai e filho de mesmo nome, no mesmo celular, é caso comum. Documento
    diferente é gente diferente, e nenhuma outra régua derruba isso."""
    cli.criar_cliente(pool, dono, "José Ribamar Filho", telefone="8695000007",
                      cpf=_cpf(3))
    cli.criar_cliente(pool, dono, "José Ribamar Filho", telefone="8695000007",
                      cpf=_cpf(4))
    assert dd.candidatos(pool, dono) == []


def test_base_com_um_cadastro_so_nao_tem_o_que_comparar(pool, dono):
    cli.criar_cliente(pool, dono, "Único da Silva", telefone="8695000006")
    assert dd.candidatos(pool, dono) == []


# ------------------------------------------------------------------- a prévia

def test_previa_barra_quando_quem_sai_e_o_dono_do_documento(pool, dono):
    """Seguir em frente jogaria o único CPF da dupla pra dentro do arquivado. A
    tela oferece inverter."""
    completo = cli.criar_cliente(pool, dono, "Larissa Rangel", telefone="8695000008",
                                 cpf=_cpf(5))
    vazio = cli.puxar_ou_criar_cliente(pool, dono,
                                       pessoa_id=_pessoa(pool, "Larissa Rangel"))
    pv = dd.previa(pool, dono, vazio, completo)
    assert pv["impedimento"] and "documento" in pv["impedimento"]
    # invertido, passa
    assert dd.previa(pool, dono, completo, vazio)["impedimento"] is None


def test_previa_lista_o_que_seria_preenchido_e_nao_muda_nada(pool, dono):
    magro = cli.criar_cliente(pool, dono, "Bianca Prado", telefone="8695000012")
    gordo = cli.puxar_ou_criar_cliente(
        pool, dono, pessoa_id=_pessoa(pool, "Bianca Prado"),
        cidade="Teresina", uf="PI", endereco="Avenida Presidente Kennedy")
    pv = dd.previa(pool, dono, magro, gordo)
    assert pv["campos"] == {"cidade": "Teresina", "uf": "PI",
                            "endereco": "Avenida Presidente Kennedy"}
    depois = cli.obter_cliente(pool, dono, magro)
    assert not depois["cidade"], "prévia não escreve"


# -------------------------------------------------------------------- a fusão

def test_fusao_arquiva_o_perdedor_mas_nao_apaga(pool, dono):
    """A exigência número 1. `ativo=false`, e a linha continua lá."""
    fica = cli.criar_cliente(pool, dono, "Isadora Peixoto", telefone="8695000017",
                             cpf=_cpf(6))
    sai = cli.puxar_ou_criar_cliente(pool, dono,
                                     pessoa_id=_pessoa(pool, "Isadora Peixoto"))
    dd.fundir(pool, dono, fica, sai)
    assert _existe(pool, sai), "a linha do perdedor NÃO pode sumir do banco"
    assert _ativo(pool, sai) is False
    assert cli.obter_cliente(pool, dono, sai) is None      # sumiu da lista
    assert cli.obter_cliente(pool, dono, fica) is not None


def test_fusao_move_titulo_lancamento_e_orcamento(pool, dono):
    fica = cli.criar_cliente(pool, dono, "Josiany Portela", telefone="8695000014",
                             cpf=_cpf(7))
    sai = cli.puxar_ou_criar_cliente(pool, dono,
                                     pessoa_id=_pessoa(pool, "Josiany Portela"))
    tid, oid = _titulo(pool, dono, sai), _orcamento(pool, dono, sai)
    with pool.connection() as c:
        lid = c.execute(
            "insert into lancamentos (conta_id, tipo, valor_centavos, data, "
            "                         categoria, cliente_id) "
            "values (%s,'receita',30000,current_date,'servicos',%s) returning id",
            (dono, sai)).fetchone()[0]
        c.commit()
    r = dd.fundir(pool, dono, fica, sai)
    assert r["refs"]["titulos"] == [tid]
    with pool.connection() as c:
        assert c.execute("select cliente_id from titulos where id=%s", (tid,)).fetchone()[0] == fica
        assert c.execute("select cliente_id from orcamentos where id=%s", (oid,)).fetchone()[0] == fica
        assert c.execute("select cliente_id from lancamentos where id=%s", (lid,)).fetchone()[0] == fica


def test_fusao_preenche_o_vazio_e_nunca_sobrescreve(pool, dono):
    """A exigência número 3, que é a razão de esta operação ser desenhada como
    'enriquece' e não como 'copia'."""
    fica = cli.criar_cliente(pool, dono, "Camila Bastos", telefone="8695000010",
                             cpf=_cpf(8), cidade="Teresina")
    sai = cli.puxar_ou_criar_cliente(
        pool, dono, pessoa_id=_pessoa(pool, "Camila Bastos"),
        cidade="Timon", uf="MA", endereco="Rua José Sales Costa")
    dd.fundir(pool, dono, fica, sai)
    v = cli.obter_cliente(pool, dono, fica)
    assert v["cidade"] == "Teresina", "a cidade que já estava lá manda"
    assert v["uf"] == "MA" and v["endereco"] == "Rua José Sales Costa"


def test_fusao_soma_os_papeis_do_gilvan(pool, dono):
    """O caso do Gilvan inteiro: cliente completo + linha vazia de fornecedor
    viram um cadastro só, marcado como os dois."""
    fica = cli.criar_cliente(pool, dono, "GILVAN PEREIRA DE MATOS",
                             telefone="8695000015", cpf=_cpf(9))
    sai = cli.puxar_ou_criar_cliente(
        pool, dono, pessoa_id=_pessoa(pool, "GILVAN PEREIRA DE MATOS"),
        eh_cliente=False, eh_fornecedor=True)
    dd.fundir(pool, dono, fica, sai)
    v = cli.obter_cliente(pool, dono, fica)
    assert v["eh_cliente"] and v["eh_fornecedor"]


def test_papel_que_ja_estava_ligado_nao_e_creditado_a_fusao(pool, dono):
    """Quem decide o papel é a linha TRAVADA, não a prévia. Se o vencedor já é
    fornecedor, a fusão não pode registrar que foi ela quem ligou — senão o
    desfazer apagaria a marcação de outra pessoa."""
    fica = cli.criar_cliente(pool, dono, "Otávio Sampaio", cpf=_cpf(21),
                             eh_cliente=True, eh_fornecedor=True)
    sai = cli.puxar_ou_criar_cliente(
        pool, dono, pessoa_id=_pessoa(pool, "Otávio Sampaio"),
        eh_cliente=False, eh_fornecedor=True)
    r = dd.fundir(pool, dono, fica, sai)
    assert r["papeis"] == []
    dd.desfazer(pool, dono, r["fusao_id"])
    assert cli.obter_cliente(pool, dono, fica)["eh_fornecedor"] is True


def test_fusao_puxa_o_telefone_de_quem_sai_quando_falta(pool, dono):
    fica = cli.criar_cliente(pool, dono, "Eline Tavares", cpf=_cpf(10))
    sai = cli.puxar_ou_criar_cliente(pool, dono,
                                     pessoa_id=_pessoa(pool, "Eline Tavares",
                                                       "558695000002"))
    dd.fundir(pool, dono, fica, sai)
    assert cli.obter_cliente(pool, dono, fica)["telefone"] == "558695000002"


def test_fundir_com_documentos_diferentes_e_recusado(pool, dono):
    a = cli.criar_cliente(pool, dono, "Sarah Quirino", cpf=_cpf(11))
    b = cli.criar_cliente(pool, dono, "Sarah Quirino", cpf=_cpf(12))
    with pytest.raises(ValueError):
        dd.fundir(pool, dono, a, b)
    assert _ativo(pool, b) is True


def test_o_mesmo_par_nao_funde_duas_vezes(pool, dono):
    """Dois cliques no mesmo botão. O segundo tem que bater na trava, senão as
    referências se moveriam de novo e o desfazer não saberia pra onde voltar."""
    fica = cli.criar_cliente(pool, dono, "Beatriz do Amaral", cpf=_cpf(13))
    sai = cli.puxar_ou_criar_cliente(pool, dono,
                                     pessoa_id=_pessoa(pool, "Beatriz do Amaral"))
    dd.fundir(pool, dono, fica, sai)
    with pytest.raises(ValueError):
        dd.fundir(pool, dono, fica, sai)


def test_fusao_de_outra_loja_nao_enxerga_nada(pool, dono):
    """Multi-tenant: o cadastro do vizinho não existe pra esta conta."""
    with pool.connection() as c:
        outro = c.execute("insert into contas (tipo,nome) values ('pj','Vizinha') "
                          "returning id").fetchone()[0]
        c.commit()
    meu = cli.criar_cliente(pool, dono, "Flávia Bezerra", cpf=_cpf(14))
    dele = cli.criar_cliente(pool, outro, "Flávia Bezerra", telefone="8695000016")
    with pytest.raises(ValueError):
        dd.fundir(pool, dono, meu, dele)
    assert _ativo(pool, dele) is True


# ------------------------------------------------------------------- desfazer

def test_desfazer_devolve_ficha_referencias_e_campos(pool, dono):
    """A exigência número 4 inteira, num caso só."""
    fica = cli.criar_cliente(pool, dono, "Luccas Teobaldo", cpf=_cpf(15))
    sai = cli.puxar_ou_criar_cliente(
        pool, dono, pessoa_id=_pessoa(pool, "Luccas Teobaldo", "8695000018"),
        cidade="Teresina", eh_cliente=False, eh_fornecedor=True)
    tid = _titulo(pool, dono, sai)
    r = dd.fundir(pool, dono, fica, sai)

    dd.desfazer(pool, dono, r["fusao_id"])

    assert _ativo(pool, sai) is True
    v = cli.obter_cliente(pool, dono, fica)
    assert v["cidade"] is None, "o campo que a fusão preencheu volta a vazio"
    assert v["telefone"] is None
    assert v["eh_fornecedor"] is False, "o papel que a fusão ligou volta a desligado"
    with pool.connection() as c:
        assert c.execute("select cliente_id from titulos where id=%s",
                         (tid,)).fetchone()[0] == sai


def test_desfazer_nao_mexe_no_que_alguem_editou_depois(pool, dono):
    """Desfazer não pode virar uma segunda forma de perder dado: se o dono
    corrigiu a cidade depois da fusão, a correção dele fica."""
    fica = cli.criar_cliente(pool, dono, "João José Rocha", cpf=_cpf(16))
    sai = cli.puxar_ou_criar_cliente(pool, dono,
                                     pessoa_id=_pessoa(pool, "João José Rocha"),
                                     cidade="Teresina")
    r = dd.fundir(pool, dono, fica, sai)
    cli.atualizar_cliente(pool, dono, fica, cidade="Timon")
    dd.desfazer(pool, dono, r["fusao_id"])
    assert cli.obter_cliente(pool, dono, fica)["cidade"] == "Timon"


def test_desfazer_duas_vezes_e_recusado(pool, dono):
    fica = cli.criar_cliente(pool, dono, "Isabela Furtado", cpf=_cpf(17))
    sai = cli.puxar_ou_criar_cliente(pool, dono,
                                     pessoa_id=_pessoa(pool, "Isabela Furtado"))
    r = dd.fundir(pool, dono, fica, sai)
    dd.desfazer(pool, dono, r["fusao_id"])
    with pytest.raises(ValueError):
        dd.desfazer(pool, dono, r["fusao_id"])


def test_depois_de_desfazer_da_pra_fundir_de_novo(pool, dono):
    """A trava do 'mesmo par duas vezes' não pode virar uma porta trancada."""
    fica = cli.criar_cliente(pool, dono, "Fernanda Bandeira", cpf=_cpf(18))
    sai = cli.puxar_ou_criar_cliente(pool, dono,
                                     pessoa_id=_pessoa(pool, "Fernanda Bandeira"))
    r = dd.fundir(pool, dono, fica, sai)
    dd.desfazer(pool, dono, r["fusao_id"])
    dd.fundir(pool, dono, fica, sai)
    assert _ativo(pool, sai) is False


def test_historico_mostra_os_dois_nomes_com_o_perdedor_arquivado(pool, dono):
    fica = cli.criar_cliente(pool, dono, "Ana Paula Vilela", cpf=_cpf(19))
    sai = cli.puxar_ou_criar_cliente(pool, dono,
                                     pessoa_id=_pessoa(pool, "Ana Paula Vilela"))
    dd.fundir(pool, dono, fica, sai, motivo="mesmo nome")
    h = dd.historico(pool, dono)
    assert len(h) == 1
    assert h[0]["vencedor_nome"] == "Ana Paula Vilela"
    assert h[0]["perdedor_nome"] == "Ana Paula Vilela"
    assert h[0]["desfeita_em"] is None


def test_depois_da_fusao_o_grupo_some_da_lista(pool, dono):
    """O fecho do ciclo: o aviso da aba Clientes tem que zerar sozinho."""
    fica = cli.criar_cliente(pool, dono, "Rita de Cássia", telefone="8695000013")
    sai = cli.puxar_ou_criar_cliente(pool, dono,
                                     pessoa_id=_pessoa(pool, "Rita de Cássia",
                                                       "8695000013"))
    assert len(dd.candidatos(pool, dono)) == 1
    dd.fundir(pool, dono, fica, sai)
    assert dd.candidatos(pool, dono) == []


def test_fusao_funciona_em_conta_sem_o_modulo_de_orcamentos(pool, dono):
    """Nem toda instalação tem as três tabelas de referência. A fusão pergunta
    ao catálogo quais existem, em vez de descobrir no erro — descobrir no erro
    obrigaria a um rollback DENTRO da transação, desfazendo a trava das duas
    linhas no meio do caminho."""
    fica = cli.criar_cliente(pool, dono, "Rosa Meireles", cpf=_cpf(20))
    sai = cli.puxar_ou_criar_cliente(pool, dono,
                                     pessoa_id=_pessoa(pool, "Rosa Meireles"))
    tid = _titulo(pool, dono, sai)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with pool.connection() as c:
        c.execute("drop table if exists orcamentos cascade")
        c.commit()
    try:
        r = dd.fundir(pool, dono, fica, sai)
        assert "orcamentos" not in r["refs"]
        with pool.connection() as c:
            assert c.execute("select cliente_id from titulos where id=%s",
                             (tid,)).fetchone()[0] == fica
    finally:
        for m in ("045_orcamentos.sql", "068_orcamento_pipeline.sql",
                  "070_orcamento_conta.sql", "152_orcamento_cliente_vinculo.sql"):
            with pool.connection() as c:
                c.execute((base / m).read_text(encoding="utf-8"))
                c.commit()
