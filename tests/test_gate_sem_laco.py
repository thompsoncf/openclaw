"""O membro de equipe nunca pode ficar preso num laço de redirect ao entrar.

O bug real: vendedor de empresa que só vende PRODUTO não conseguia entrar.
O gate (web/app.py) manda quem tem caps.vendas pra /painel/servicos; a tela
via que a conta não vende serviço e desviava pra /painel/empresa; o gate barra
/painel/empresa pra vendedor e devolvia pra /painel/servicos. Laço infinito.

A regra que amarra isso: **todo destino que uma tela escolhe pra desviar um
membro tem que estar na whitelist do papel dele** (contas.equipe.rotas_do_papel),
senão o gate devolve e o vai-e-vem recomeça.

Funções puras: sem banco.
"""
import pytest

from contas.equipe import caps_do_papel, home_do_papel, rotas_do_papel
from web.painel_servicos import _saida


class _Req:
    """Request só com o que _saida lê."""
    def __init__(self, papel):
        self.session = {"papel": papel} if papel else {}


def _conta(*, tem_pj=True, acesso_pj=True, vende_servico=False):
    """conta_logada devolve uma tupla; só os índices 11/12/14 importam aqui
    ([11]=tem_pj, [12]=acesso_pj, [14]=vende_servico)."""
    row = [None] * 16
    row[11], row[12], row[14] = tem_pj, acesso_pj, vende_servico
    return tuple(row)


def _permitido(papel, destino):
    """O gate aceita a rota exata ou qualquer coisa abaixo dela."""
    return any(destino == a or destino.startswith(a + "/") for a in rotas_do_papel(papel))


MEMBROS = ["gestor", "vendedor", "financeiro", "membro", "restrito"]


# ------------------------------------------------------------------ o bug em si
def test_vendedor_de_empresa_so_de_produto_nao_fica_preso():
    # exatamente o caso que travava: vende produto, não vende serviço
    destino = _saida(_Req("vendedor"), _conta(vende_servico=False))
    assert destino != "/painel/empresa"          # era pra cá que ia, e o gate devolvia
    assert _permitido("vendedor", destino), destino
    assert destino == "/painel/prospeccao"       # o funil não depende de vender serviço


@pytest.mark.parametrize("papel", MEMBROS)
@pytest.mark.parametrize("tem_pj", [True, False])
@pytest.mark.parametrize("acesso_pj", [True, False])
def test_destino_do_membro_sempre_passa_no_gate(papel, tem_pj, acesso_pj):
    """A invariante: qualquer combinação de papel × conta cai num lugar que o
    próprio gate libera. Se um dia alguém mudar o desvio pra uma rota fora da
    whitelist, este teste quebra antes de o vendedor ficar trancado do lado de fora."""
    conta = _conta(tem_pj=tem_pj, acesso_pj=acesso_pj, vende_servico=False)
    destino = _saida(_Req(papel), conta)
    assert _permitido(papel, destino), f"{papel} × pj={tem_pj}/{acesso_pj} → {destino}"


@pytest.mark.parametrize("papel", MEMBROS)
def test_membro_sem_pj_cai_no_trocar(papel):
    """Sem módulo PJ não há funil nem financeiro da empresa pra abrir: a saída é
    /trocar, que o gate libera pra todo mundo."""
    destino = _saida(_Req(papel), _conta(tem_pj=False, acesso_pj=False))
    assert destino == "/trocar"


def test_dono_nao_muda_de_comportamento():
    # o dono passa em tudo; o desvio dele continua sendo o de sempre
    assert _saida(_Req("dono"), _conta(acesso_pj=True)) == "/painel/empresa"
    assert _saida(_Req("dono"), _conta(acesso_pj=False)) == "/painel"


# ------------------------------------------------------------------ a whitelist
def test_whitelist_bate_com_as_capacidades():
    for papel in MEMBROS:
        caps, rotas = caps_do_papel(papel), rotas_do_papel(papel)
        assert "/trocar" in rotas and "/sair" in rotas       # saída sempre existe
        assert ("/painel/servicos" in rotas) is caps["vendas"]
        assert ("/painel/prospeccao" in rotas) is caps["vendas"]
        assert ("/painel/empresa" in rotas) is caps["financeiro"]
        assert ("/painel/relatorios" in rotas) is caps["financeiro"]
        assert ("/painel/equipe" in rotas) is caps["gerir"]


def test_painel_do_dono_nunca_entra_na_whitelist_de_membro():
    # /painel é a área pessoal do titular (plano, pessoas da conta)
    for papel in MEMBROS:
        assert "/painel" not in rotas_do_papel(papel)


# ------------------------------------------------------------------ casa de cada papel
def test_vendedor_entra_no_cockpit():
    """O app do vendedor é o Cockpit. Antes ele caía em /painel — que o gate barra —
    e o Cockpit só abria por link mágico: quem entrava por e-mail e senha nunca via."""
    assert home_do_papel("vendedor", membro_id=7) == "/cockpit"


def test_titular_continua_no_painel():
    assert home_do_papel("dono", membro_id=None) == "/painel"


@pytest.mark.parametrize("papel", ["gestor", "financeiro", "membro", "restrito"])
def test_os_outros_papeis_nao_mudaram(papel):
    assert home_do_papel(papel, membro_id=7) == "/painel"


def test_membro_com_papel_dono_nao_e_o_titular():
    """Membro de equipe pode ter papel 'dono'; o titular é quem NÃO tem membro_id."""
    assert home_do_papel("dono", membro_id=7) == "/painel"


def test_a_casa_do_vendedor_nao_depende_do_gate():
    """/cockpit fica fora de /painel*, então o gate (que só filtra /painel* e
    /membros*) não tem como devolver o vendedor — nada de laço aqui."""
    destino = home_do_papel("vendedor", membro_id=7)
    assert not destino.startswith("/painel")


@pytest.mark.parametrize("papel", MEMBROS)
def test_casa_de_membro_termina_em_algum_lugar(papel):
    """A casa de um membro ou já é liberada, ou é /painel — e /painel o gate
    resolve sozinho, mandando pra home do papel (que os testes acima provam ser
    terminal). O que não pode é apontar pra uma rota de painel FORA da whitelist,
    que o gate devolveria em looping."""
    destino = home_do_papel(papel, membro_id=7)
    guardado = destino == "/painel" or destino.startswith("/painel/") or destino.startswith("/membros")
    assert (not guardado                    # /cockpit: o gate nem olha
            or destino == "/painel"         # o gate resolve pra home do papel
            or _permitido(papel, destino)), destino
