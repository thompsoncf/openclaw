"""O perfil `clinica` do Raio-X e do funil: clínica não é consultoria.

POR QUE ESTE PERFIL EXISTE: em 22/09/2026 a Espaço Pelle (conta 39, clínica
dermatológica) salvou o nicho `clinica` e o funil não mudou — porque não tinha
pra onde mudar. Sem nicho ela caía em `recorrente`; com o nicho, por vender
serviço, caía em `recorrente` de novo. Continuou vendo "Reunião marcada", filtro
de porte e segmento do CNPJ e proposta em "/mês".

As duas respostas do dono, no mesmo dia:
  · as colunas do meio são "Consulta agendada" e "Plano de tratamento";
  · o compromisso, nas telas, se chama AVALIAÇÃO.

O teste que guarda a razão do perfil é
`test_a_clinica_nao_cai_mais_em_recorrente`: se alguém tirar o nicho de
`_PERFIL_DO_NICHO`, `vende_servico` a pega de volta calado.
"""
import pytest

from finance import funil_modelo as fm, raio_x_perfil as rxp


# ── o roteamento ──────────────────────────────────────────────────────────
def test_a_clinica_nao_cai_mais_em_recorrente():
    """A RAZÃO DO PERFIL. Ver o docstring do módulo."""
    assert rxp.perfil_por_nicho("clinica") == "clinica"
    assert rxp.perfil("clinica")["chave"] == "clinica"


def test_os_outros_nichos_nao_se_mexeram():
    assert rxp.perfil_por_nicho("seguros") == "seguros"
    assert rxp.perfil_por_nicho("eventos") == "eventos"
    for s in ("advocacia", "consultoria", "salao", "salao_completo", "suplementos"):
        assert rxp.perfil_por_nicho(s) == "recorrente", s
    assert rxp.perfil_por_nicho(None) == "recorrente"
    assert rxp.perfil_por_nicho("beleza") == "produto"


def test_clinica_esta_na_lista_de_perfis():
    assert "clinica" in rxp.PERFIS


def test_a_clinica_recebe_raio_x():
    assert rxp.perfil("clinica")["aplica"] is True


# ── o vocabulário ─────────────────────────────────────────────────────────
def test_o_compromisso_da_clinica_e_avaliacao():
    v = rxp.perfil("clinica")["vocab"]
    assert v["compromisso"] == "avaliação"
    assert v["compromissos"] == "avaliações"
    assert v["compromisso_kpi"] == "avaliações que aconteceram"
    assert v["data"] is False


def test_nenhuma_palavra_de_mensalidade_nem_de_festa_vaza_pra_clinica():
    v = " ".join(str(x) for x in rxp.perfil("clinica")["vocab"].values()).lower()
    for palavra in ("reunião", "festa", "visita", "mensal", "apólice", "cotação"):
        assert palavra not in v


def test_a_clinica_nao_contaminou_o_recorrente():
    assert rxp.perfil("advocacia")["vocab"]["compromisso"] == "reunião"
    assert rxp.etapas_padrao("recorrente")[3][1] == "Reunião marcada"


# ── o que a tela mostra ───────────────────────────────────────────────────
def test_filtro_de_empresa_sai_porque_paciente_e_pessoa_fisica():
    """segmento, porte e UF vêm do CNPJ do lead. Paciente não tem CNPJ."""
    p = rxp.perfil("clinica")
    assert not {"segmento", "porte", "uf"} & set(p["filtros"])
    assert "segmentos" not in p["blocos"]


def test_valor_nao_e_mensalidade_nem_comissao():
    """`mrr` soma mensalidade e `comissao` fala de apólice. Sem os dois, o placar
    mostra o valor total proposto e fechado — o ramo `else` do template."""
    b = rxp.perfil("clinica")["blocos"]
    assert "mrr" not in b and "comissao" not in b


def test_o_bloco_da_agenda_reusa_a_chave_do_recorrente():
    """`reunioes` é chave, não rótulo: o bloco lê o nome ('avaliações') de vocab."""
    assert "reunioes" in rxp.perfil("clinica")["blocos"]


# ── o funil ───────────────────────────────────────────────────────────────
def test_as_colunas_do_meio_sao_as_que_o_dono_escolheu():
    rot = {ch: r for ch, r, *_ in rxp.etapas_padrao("clinica")}
    assert rot["qualificado"] == "Consulta agendada"
    assert rot["proposta"] == "Plano de tratamento"


def test_as_chaves_das_etapas_nao_mudaram():
    """A chave é o que fica em `prospeccao.status`; o modelo muda só o rótulo."""
    chaves = [e[0] for e in rxp.etapas_padrao("clinica")]
    assert chaves == [e[0] for e in rxp.etapas_padrao("recorrente")]


def test_fechado_sai_do_quadro_mas_nao_agenda():
    ganho = [e for e in rxp.etapas_padrao("clinica") if e[0] == "ganho"][0]
    assert ganho[4] is True and ganho[5] is False


def test_os_numeros_do_funil_sao_os_do_recorrente_ate_haver_dado():
    assert rxp.funil_padrao("clinica") == rxp.funil_padrao("recorrente")


def test_a_conta_semeada_pelo_recorrente_recebe_a_proposta_marcada():
    """É o caso da conta 39: as colunas vieram da semente `recorrente`, ninguém
    as renomeou, então a troca de rótulo vem PRÉ-ACEITA na faixa do funil."""
    assert not fm.foi_o_dono({"semeado_de": "recorrente"})


# ── os motivos de perda ───────────────────────────────────────────────────
def test_a_semente_tem_o_que_so_a_clinica_perde():
    chaves = {k for k, _r, _e in rxp.semente_motivos("clinica")}
    assert {"adiou_tratamento", "nao_indicado", "fechou_concorrente"} <= chaves


def test_a_semente_fala_a_lingua_dela():
    rot = {k: r for k, r, _e in rxp.semente_motivos("clinica")}
    assert rot["fechou_concorrente"] == "Fez em outra clínica"
    assert "plano de tratamento" in rot["sumiu_apos_proposta"]


def test_so_outro_exige_descricao():
    assert [k for k, _r, e in rxp.semente_motivos("clinica") if e] == ["outro"]


@pytest.mark.parametrize("perfil", ["eventos", "recorrente", "seguros"])
def test_as_sementes_dos_outros_nao_mudaram(perfil):
    chaves = {k for k, _r, _e in rxp.semente_motivos(perfil)}
    assert "adiou_tratamento" not in chaves and "nao_indicado" not in chaves
