"""O perfil `obras` do Raio-X e do funil: construtora não é consultoria.

POR QUE ESTE PERFIL EXISTE: em 25/09/2026 a PX2 (conta 33) estava no nicho
`construcao` e via o funil de quem vende mensalidade pra empresa — "Reunião
marcada", filtro de porte e segmento do CNPJ, bloco de MRR —, porque todo nicho
de serviço sem perfil próprio cai em `recorrente`.

Ela vende CASA de Minha Casa Minha Vida e faz REFORMA. O desenho aprovado pelo
dono no mesmo dia (docs/mockups/nicho_construcao.html, seção 08) pôs os dois
negócios num quadro só, com uma coluna que nenhum outro ramo tem: "Crédito em
análise" — a casa esperando a Caixa, a reforma esperando o Reforma Casa Brasil.

O teste que guarda a razão do perfil é
`test_a_construcao_nao_cai_mais_em_recorrente`: se alguém tirar o nicho de
`_PERFIL_DO_NICHO`, `vende_servico` a pega de volta calado.
"""
import pytest

from finance import funil_modelo as fm, raio_x_perfil as rxp


# ── o roteamento ──────────────────────────────────────────────────────────
def test_a_construcao_nao_cai_mais_em_recorrente():
    """A RAZÃO DO PERFIL. Ver o docstring do módulo."""
    assert rxp.perfil_por_nicho("construcao") == "obras"
    assert rxp.perfil("construcao")["chave"] == "obras"


def test_os_outros_nichos_nao_se_mexeram():
    assert rxp.perfil_por_nicho("seguros") == "seguros"
    assert rxp.perfil_por_nicho("clinica") == "clinica"
    assert rxp.perfil_por_nicho("eventos") == "eventos"
    for s in ("advocacia", "consultoria", "arquitetura", "servicos_gerais", "suplementos"):
        assert rxp.perfil_por_nicho(s) == "recorrente", s
    assert rxp.perfil_por_nicho(None) == "recorrente"
    assert rxp.perfil_por_nicho("beleza") == "produto"


def test_obras_esta_na_lista_de_perfis():
    assert "obras" in rxp.PERFIS


def test_a_obra_recebe_raio_x():
    assert rxp.perfil("construcao")["aplica"] is True


def test_o_nome_do_perfil_na_tela():
    assert rxp.rotulo_do_perfil("obras") == "construção e reforma"


def test_obra_nao_tem_contrato_de_locacao():
    """Contrato de empreitada é o PR da reforma; aqui nada muda do recorrente."""
    assert rxp.perfil("construcao")["contrato"] is False


# ── o vocabulário ─────────────────────────────────────────────────────────
def test_o_compromisso_e_visita_e_o_pedido_e_obra():
    v = rxp.perfil("construcao")["vocab"]
    assert v["compromisso"] == "visita"
    assert v["compromissos"] == "visitas"
    assert v["compromisso_kpi"] == "visitas que aconteceram"
    assert v["pedido"] == "obra"
    assert v["data"] is False


def test_nenhuma_palavra_de_mensalidade_de_festa_ou_de_clinica_vaza_pra_obra():
    v = " ".join(str(x) for x in rxp.perfil("construcao")["vocab"].values()).lower()
    for palavra in ("reunião", "festa", "mensal", "apólice", "cotação", "avaliação",
                    "tratamento"):
        assert palavra not in v


def test_a_obra_nao_contaminou_o_recorrente():
    assert rxp.perfil("arquitetura")["vocab"]["compromisso"] == "reunião"
    assert rxp.etapas_padrao("recorrente")[3][1] == "Reunião marcada"


# ── o que a tela mostra ───────────────────────────────────────────────────
def test_filtro_de_empresa_sai_porque_quem_compra_casa_e_pessoa_fisica():
    """segmento, porte e UF vêm do CNPJ do lead. Comprador de casa popular e
    cliente de reforma não têm CNPJ."""
    p = rxp.perfil("construcao")
    assert not {"segmento", "porte", "uf"} & set(p["filtros"])
    assert "segmentos" not in p["blocos"]


def test_valor_nao_e_mensalidade_nem_comissao():
    b = rxp.perfil("construcao")["blocos"]
    assert "mrr" not in b and "comissao" not in b


def test_o_bloco_da_agenda_reusa_a_chave_do_recorrente():
    """`reunioes` é chave, não rótulo: o bloco lê o nome ('visitas') de vocab."""
    assert "reunioes" in rxp.perfil("construcao")["blocos"]


# ── o funil ───────────────────────────────────────────────────────────────
def test_as_colunas_do_meio():
    rot = {ch: r for ch, r, *_ in rxp.etapas_padrao("obras")}
    assert rot["qualificado"] == "Visita"
    assert rot["proposta"] == "Proposta"
    assert rot["credito"] == "Crédito em análise"


def test_credito_em_analise_fica_entre_proposta_e_fechado():
    etapas = rxp.etapas_padrao("obras")
    assert [e[0] for e in etapas] == ["novo", "contatado", "follow_up", "qualificado",
                                      "proposta", "credito", "ganho", "perdido"]
    ordem = {e[0]: e[2] for e in etapas}
    assert ordem["proposta"] < ordem["credito"] < ordem["ganho"]


def test_credito_e_coluna_comum():
    """Não é fixa (o dono pode renomear ou tirar), fica no quadro e não agenda."""
    cred = next(e for e in rxp.etapas_padrao("obras") if e[0] == "credito")
    assert cred[3:] == (False, False, False)


def test_as_outras_chaves_sao_as_do_recorrente():
    """A chave é o que fica em `prospeccao.status`; só `credito` é nova."""
    chaves = [e[0] for e in rxp.etapas_padrao("obras") if e[0] != "credito"]
    assert chaves == [e[0] for e in rxp.etapas_padrao("recorrente")]


def test_credito_so_existe_em_obras():
    for p in rxp.PERFIS:
        if p != "obras":
            assert "credito" not in {e[0] for e in rxp.etapas_padrao(p)}, p


def test_fechado_sai_do_quadro_mas_nao_agenda():
    ganho = next(e for e in rxp.etapas_padrao("obras") if e[0] == "ganho")
    assert ganho[4] is True and ganho[5] is False


def test_os_numeros_do_funil_sao_os_do_recorrente_ate_haver_dado():
    assert rxp.funil_padrao("obras") == rxp.funil_padrao("recorrente")


def test_os_rotulos_do_modelo_contam_como_semente():
    """Sem isto, a conta semeada por `obras` teria "Visita" e "Crédito em
    análise" tratados como nome que o DONO deu (ver funil_modelo.carimbar)."""
    assert "Crédito em análise" in fm._rotulos_de_semente("credito")
    assert "Visita" in fm._rotulos_de_semente("qualificado")


# ── os motivos de perda ───────────────────────────────────────────────────
def test_a_semente_tem_os_motivos_da_caixa():
    chaves = {k for k, _r, _e in rxp.semente_motivos("obras")}
    assert {"credito_reprovado", "restricao_cpf", "renda_insuficiente",
            "ja_tem_imovel", "avaliacao_abaixo"} <= chaves


def test_a_semente_tem_o_concorrente_da_reforma():
    rot = {k: r for k, r, _e in rxp.semente_motivos("obras")}
    assert rot["fechou_concorrente"] == "Fechou com outro construtor ou pedreiro"


def test_so_outro_exige_descricao():
    assert [k for k, _r, e in rxp.semente_motivos("obras") if e] == ["outro"]


def test_as_chaves_dos_motivos_nao_repetem():
    chaves = [k for k, _r, _e in rxp.semente_motivos("obras")]
    assert len(chaves) == len(set(chaves))


@pytest.mark.parametrize("perfil", ["eventos", "recorrente", "seguros", "clinica"])
def test_as_sementes_dos_outros_nao_mudaram(perfil):
    chaves = {k for k, _r, _e in rxp.semente_motivos(perfil)}
    assert not {"credito_reprovado", "restricao_cpf", "avaliacao_abaixo"} & chaves
