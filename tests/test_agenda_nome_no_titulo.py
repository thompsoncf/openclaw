"""Ler o nome do cliente que ficou preso dentro do título.

CAMADA 2 do conserto de 31/08/2026. A camada 1 deu ao formulário um campo de
cliente, então todo compromisso NOVO nasce com dono. Sobraram os 51 antigos da
Prime, que têm o nome do cliente dentro do texto do título — "Locação — Fulano" —
porque até então não havia outro lugar onde escrevê-lo.

Isto aqui LÊ esse nome; não grava. A tela mostra como palpite (apagado, com selo)
e oferece "ligar ao cadastro", que é o que transforma palpite em vínculo. Sem essa
marcação a leitura viraria dado de mentira, e sem o botão ela viraria moradia
permanente em vez de se esgotar com o uso.

A ARMADILHA, que é o motivo de o teste existir com tantos casos: metade das
visitas da Prime se chama "VISITA TÉCNICA - PEDRO", e Pedro é VENDEDOR. Pôr o
nome do vendedor na coluna Cliente é pior do que deixar em branco — erra com
confiança. Mas a proteção não pode ser cega ao contrário: há clientes de verdade
cujo nome encosta no da equipe, e eles precisam continuar aparecendo.
"""
import pytest

from finance import agenda as ag

#: a equipe real da conta 34, que é o que dá sentido aos casos de descarte
EQUIPE = ["MANOEL SOARES", "JACQUELINE PRIME", "PEDRO YAN PRIME",
          "THIAGO PINHEIRO", "zaq teste"]


# ------------------------------------------------- o que a régua RECUPERA

@pytest.mark.parametrize("titulo, tipo_evento, esperado", [
    # o formato que o sistema monta e que a equipe já digita: travessão
    ("Locação — Jonas Barreto Castro Neto", "Locação", "Jonas Barreto Castro Neto"),
    ("Casamento — Eva da Silva Fontoura", "Casamento", "Eva da Silva Fontoura"),
    ("Buffet — Erlane de Barros Pinho", "Buffet", "Erlane de Barros Pinho"),
    ("Festa de aniversário — Joao Pedro Monteiro", None, "Joao Pedro Monteiro"),
    # hífen cercado de espaço, que é como se digita na pressa
    ("Formatura - Beatriz", None, "Beatriz"),
    ("15 Anos - Fernanda", None, "Fernanda"),
    # sem separador nenhum, mas com o tipo na frente
    ("Locação Marcelo Rios", "Locação", "Marcelo Rios"),
    # parêntese e acento não atrapalham: o nome é o que vem depois do separador
    ("Locação — Marcelo Rios (Ponto X)", "Locação", "Marcelo Rios (Ponto X)"),
])
def test_recupera_o_nome_do_titulo(titulo, tipo_evento, esperado):
    assert ag.nome_no_titulo(titulo, tipo_evento, EQUIPE) == esperado


# --------------------------------------- o que ela DESCARTA por ser da equipe

@pytest.mark.parametrize("titulo", [
    "VISITA TÉCNICA - PEDRO",
    "VISITA TÉCNICA – PEDRO",            # travessão curto, o mesmo caso
    "VISITA TÉCNICA - PEDRO/JACQUE",     # DOIS vendedores; nenhum sozinho explica
    "Reunião - Jacqueline",
    "Visita - Thiago Pinheiro",
])
def test_descarta_nome_de_quem_e_da_equipe(titulo):
    assert ag.nome_no_titulo(titulo, None, EQUIPE) is None


def test_a_barra_nao_esconde_a_dupla_de_vendedores():
    """Partindo só por espaço, "PEDRO/JACQUE" vira uma palavra só e escapa da
    checagem — foi assim que esse par passou na primeira versão da régua."""
    assert ag.nome_no_titulo("VISITA TÉCNICA - PEDRO/JACQUE", None, EQUIPE) is None
    # e a checagem tem que olhar a equipe INTEIRA junta: testando membro a membro,
    # nenhum tem "pedro" e "jacque" ao mesmo tempo, e o par passava
    assert ag.nome_no_titulo("Reunião - Pedro e Jacqueline", None, EQUIPE) is None


# ------------------------- e o contrário: cliente que encosta no nome do time

@pytest.mark.parametrize("titulo, tipo_evento, esperado", [
    # o dono chama-se MANOEL SOARES; este é outro Manoel, e é cliente
    ("Confraternização — Manoel Soares Vilanova Jr", "Confraternização",
     "Manoel Soares Vilanova Jr"),
    ("Casamento — Doutor Manoel Soares Vilanova Jr", "Casamento",
     "Doutor Manoel Soares Vilanova Jr"),
    # "Pedro" é vendedor, mas este Pedro tem sobrenome que ninguém do time tem
    ("Locação — Pedro Ribamar de Sousa Santos Júnior", "Locação",
     "Pedro Ribamar de Sousa Santos Júnior"),
])
def test_uma_palavra_de_fora_ja_e_cliente_de_verdade(titulo, tipo_evento, esperado):
    assert ag.nome_no_titulo(titulo, tipo_evento, EQUIPE) == esperado


# --------------------------------------------------- quando NÃO há nome nenhum

@pytest.mark.parametrize("titulo", [
    "VISITA TÉCNICA", "ANIVERSÁRIO", "REUNIÃO COM ENGENHEIRA",
    "Reunião Política -",                 # separador sem nada depois
    "aniversario Leda Lopes",             # sem separador e sem tipo pra ancorar
    "Locação — AB",                       # curto demais pra ser gente
    "", "   ", None,
])
def test_nao_inventa_quando_nao_da_pra_afirmar(titulo):
    assert ag.nome_no_titulo(titulo, None, EQUIPE) is None


def test_hifen_colado_nao_e_separador():
    """"Ana-Maria" é um nome só. Só hífen cercado de espaço separa."""
    assert ag.nome_no_titulo("Locação — Ana-Maria Costa", "Locação",
                             EQUIPE) == "Ana-Maria Costa"
    assert ag.nome_no_titulo("Aniversário-surpresa", None, EQUIPE) is None


def test_sem_a_equipe_a_protecao_fica_desligada():
    """Documenta o contrato: quem chama PRECISA passar os nomes do time. Com a
    lista vazia, o nome do vendedor volta a passar — e é por isso que o relatório
    faz a consulta dos membros antes de montar as linhas."""
    assert ag.nome_no_titulo("VISITA TÉCNICA - PEDRO", None, []) == "PEDRO"
    assert ag.nome_no_titulo("VISITA TÉCNICA - PEDRO", None, EQUIPE) is None


def test_o_placar_dos_51_titulos_reais():
    """A medida que justificou a régua, com os títulos que estavam no banco em
    31/08/2026: 36 recuperados, 6 descartados por serem da equipe, 9 sem nome.
    Se alguém afrouxar a régua, o número de descartados cai e este teste avisa."""
    reais = [
        ("VISITA TÉCNICA COM PEDRO", None), ("VISITA TÉCNICA - PEDRO", None),
        ("VISITA THIAGO PINHEIRO", None), ("VISITA TÉCNICA", None),
        ("VISITA TÉCNICA - PEDRO", None), ("VISITA TÉCNICA - PEDRO/JACQUE", None),
        ("VISITA TÉCNICA - PEDRO", None), ("VISITA TÉCNICA - PEDRO", None),
        ("VISITA TÉCNICA - PEDRO", None), ("Visita técnica Nayara", None),
        ("Locação — Jonas Barreto Castro Neto", "Locação"),
        ("REUNIÃO COM ENGENHEIRA", None), ("aniversario Leda Lopes", None),
        ("Reunião Política -", None), ("Reunião Política - Bianca - Pedro", None),
        ("Reunião Política - Bianca - Pedro", None),
        ("Casamento — Eva da Silva Fontoura", "Casamento"),
        ("Casamento — Maria Alice Sousa de Oliveira", "Casamento"),
        ("Aniversário — Francisca Nilda Carvalho Souza", "Aniversário"),
        ("RESERVA MANOEL NETO", None),
        ("Locação — Nágera Raquel Sousa dos Santos", "Locação"),
        ("ANIVERSÁRIO", None),
        ("Locação — Maria Gardênia Fernandes da Silva", "Locação"),
        ("Locação — Thayla Téssia Duarte de Moura", "Locação"),
        ("Locação — Pedro Ribamar de Sousa Santos Júnior", "Locação"),
        ("Locação — Josipio Gonçalves Martins Júnior", "Locação"),
        ("Locação — Ocilene Silva Cabral", "Locação"),
        ("Locação — Raquel Gomes Andrade", "Locação"),
        ("Buffet — Denise dos Santos Oliveira", "Buffet"),
        ("Casamento — Montanna Santiago Benevídio", "Casamento"),
        ("Casamento — Max Nícolas de Oliveira Lima", "Casamento"),
        ("Festa de aniversário — Joao Pedro Monteiro", None),
        ("Locação — Rita Helena Barros de Miranda", "Locação"),
        ("Formatura — Maria Madalena Santos Sampaio", "Formatura"),
        ("Formatura — Maria Madalena Santos Sampaio", "Formatura"),
        ("Locação — Marcelo Rios (Ponto X)", "Locação"),
        ("Buffet — Edlla Machado Thê", "Buffet"),
        ("Locação — Bianca Lopes Cacau", "Locação"),
        ("Confraternização — Manoel Soares Vilanova Jr", "Confraternização"),
        ("Confraternização — Manoel Soares Vilanova Jr", "Confraternização"),
        ("Buffet — Erlane de Barros Pinho", "Buffet"), ("Formatura - Beatriz", None),
        ("Locação — Zenilda Rosa Silva Andrade", "Locação"),
        ("Casamento — Denise dos Santos Oliveira", "Casamento"),
        ("15 Anos - Fernanda", None),
        ("Locação — Antônia Bruna Santos Noleto", "Locação"),
        ("Locação — Allef Jorge da Silva Macêdo", "Locação"),
        ("Locação — Márcia Lopes de Nascimento", "Locação"),
        ("Casamento — Doutor Manoel Soares Vilanova Jr", "Casamento"),
        ("Aniversário — Marilene da Silva Damasceno", "Aniversário"),
        ("Locação — Vanessa Maria da Silva Oliveira", "Locação"),
    ]
    assert len(reais) == 51
    lidos = [ag.nome_no_titulo(t, tp, EQUIPE) for t, tp in reais]
    recuperados = [n for n in lidos if n]
    assert len(recuperados) == 36
    # nenhum nome recuperado pode ser só gente do time
    for n in recuperados:
        assert not ag._e_da_equipe(n, EQUIPE), n
