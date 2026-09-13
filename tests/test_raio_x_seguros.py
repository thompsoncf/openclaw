"""O perfil `seguros` do Raio-X: corretora não é consultoria.

POR QUE ESTE PERFIL EXISTE, medido em 13/09/2026 (o mockup
docs/mockups/raio_x_seguros_medido.html tem o levantamento inteiro): caindo em
`recorrente`, a corretora via "Mensalidade proposta × fechada" somando
`orcamentos.mensal_centavos`. Comissão de apólice vai em `setup_centavos`, então
o bloco lia R$ 0 e escrevia **"o gargalo é depois da proposta"** — num mês em que
ela pode ter fechado vinte apólices. Não era campo vazio: era diagnóstico errado,
que manda o dono cobrar a equipe pelo motivo que não existe.

As duas respostas do dono que moldaram o resto, no mesmo dia:
  · o compromisso dela é COTAÇÃO, não reunião;
  · vende pra pessoa FÍSICA e JURÍDICA — por isso segmento/porte/UF FICAM.

O teste que guarda a razão do perfil é
`test_o_bloco_de_valor_soma_a_coluna_do_perfil`: se alguém "simplificar" fazendo
seguros voltar a somar mensalidade, o R$ 0 volta calado.
"""
import pytest

from finance import raio_x_perfil as rxp


# ── o roteamento ──────────────────────────────────────────────────────────
def test_seguros_tem_perfil_proprio_e_nao_cai_em_recorrente():
    assert rxp.perfil_por_nicho("seguros") == "seguros"
    assert rxp.perfil("seguros")["rotulo"] == "corretora de seguros"


def test_os_outros_nichos_nao_se_mexeram():
    """`seguros` entrou ANTES do teste de vende_servico em perfil_por_nicho —
    este é o teste de que passar na frente não roubou ninguém."""
    assert rxp.perfil_por_nicho("eventos") == "eventos"
    for s in ("consultoria", "tecnologia", "contabilidade", "advocacia", "agencia"):
        assert rxp.perfil_por_nicho(s) == "recorrente", s
    for s in ("hortifruti", "farmacia"):
        assert rxp.perfil_por_nicho(s) == "produto", s
    assert rxp.perfil_por_nicho(None) == "recorrente"


# ── o vocabulário ─────────────────────────────────────────────────────────
def test_o_compromisso_da_corretora_e_cotacao():
    """Resposta do dono em 13/09. O bloco da agenda lê tudo de `vocab`, então
    trocar estas quatro palavras é o que renomeia a tela inteira."""
    v = rxp.perfil("seguros")["vocab"]
    assert v["compromisso"] == "cotação" and v["compromissos"] == "cotações"
    assert v["compromisso_kpi"] == "cotações que aconteceram"
    assert v["pedido"] == "apólice" and v["oferta"] == "ramo"


def test_corretora_nao_vende_data():
    """Apólice tem vigência; não é data reservada esperando sinal. `data=False`
    é o que tira a barra de fixado/segurado e o vocabulário de festa."""
    assert rxp.perfil("seguros")["vocab"]["data"] is False


def test_nenhuma_palavra_de_festa_nem_de_mensalidade_vaza_pra_corretora():
    sg = rxp.perfil("seguros")
    festa = {"tipo", "mes", "dia", "conv", "demanda_agenda", "dia_festa", "tipos", "festa"}
    tudo = set(sg["filtros"]) | set(sg["blocos"]) | set(sg["faixas"])
    assert festa & tudo == set()
    assert "mrr" not in sg["blocos"], "mensalidade é do recorrente, não da corretora"


def test_a_corretora_nao_contaminou_o_recorrente():
    """O caminho contrário: a ZAQ (consultoria) tem que continuar exatamente como
    estava — é onde o perfil recorrente está CERTO."""
    rc = rxp.perfil("consultoria")
    assert rc["vocab"]["compromisso"] == "reunião"
    assert "mrr" in rc["blocos"] and "comissao" not in rc["blocos"]
    assert "cotação" not in str(rc["vocab"].values())


# ── os blocos ─────────────────────────────────────────────────────────────
def test_o_bloco_de_valor_e_comissao_e_nao_mensalidade():
    """A RAZÃO DO PERFIL. Ver o docstring do módulo."""
    sg = rxp.perfil("seguros")
    assert "comissao" in sg["blocos"] and "mrr" not in sg["blocos"]


def test_o_bloco_de_valor_soma_a_coluna_do_perfil():
    """O motor tem UMA função de proposto × fechado e ela recebe a coluna. Se
    alguém apontar seguros pra mensalidade de novo, o R$ 0 volta calado."""
    import inspect
    from finance import raio_x_dono as rxd
    assert "setup_centavos" in inspect.getsource(rxd._comissao)
    assert "mensal_centavos" in inspect.getsource(rxd._mrr)
    # a coluna nunca vem de fora
    assert 'assert coluna in ("mensal_centavos", "setup_centavos")' in \
        inspect.getsource(rxd._proposto_x_fechado)


def test_segmento_e_porte_ficam_porque_ela_vende_pra_empresa_tambem():
    """A medição de 13/09 supunha que sumissem (auto é pessoa física, e a Prime
    prova o padrão: 349 leads, ZERO com segmento). O dono corrigiu: ela vende pros
    dois. Valem pra metade PJ da carteira, e metade é melhor que nada."""
    sg = rxp.perfil("seguros")
    assert {"segmento", "porte", "uf"} <= set(sg["filtros"])
    assert "segmentos" in sg["blocos"]


def test_o_bloco_da_agenda_reusa_a_chave_do_recorrente():
    """`reunioes` é chave, não rótulo: o bloco lê o nome de `vocab`. Uma chave
    `cotacoes` só criaria um segundo bloco idêntico pra manter."""
    assert "reunioes" in rxp.perfil("seguros")["blocos"]


# ── o funil ───────────────────────────────────────────────────────────────
def test_a_coluna_do_funil_e_cotacao_enviada():
    rot = {c: r for c, r, *_ in rxp.etapas_padrao("seguros")}
    assert rot["qualificado"] == "Cotação enviada"
    assert rot["ganho"] == "Fechado" and rot["perdido"] == "Perdido"


def test_a_chave_da_etapa_nao_mudou():
    """Rótulo é o que a pessoa lê, chave é o que está em `prospeccao.status`.
    Inventar `cotacao_enviada` deixaria os leads apontando pra etapa que sumiu."""
    chaves = [c for c, *_ in rxp.etapas_padrao("seguros")]
    assert chaves == ["novo", "contatado", "follow_up", "qualificado",
                      "proposta", "ganho", "perdido"]


def test_fechado_sai_do_quadro_mas_nao_agenda():
    """Sem vigência guardada em lugar nenhum, a ponte pra Agenda não teria data
    pra ler — marcar `agenda_ao_entrar` criaria compromisso sem quando."""
    ganho = [e for e in rxp.etapas_padrao("seguros") if e[0] == "ganho"][0]
    _, _, _, _, sai, agenda = ganho
    assert sai is True and agenda is False


def test_o_segundo_relogio_fica_nulo_ate_a_vigencia_existir():
    """Ela TEM segundo relógio — o fim da vigência, o melhor de todos. Mas o dado
    não é guardado ainda, e 30 dias fixos disparariam contando do nada."""
    assert rxp.funil_padrao("seguros")["fu_festa_dias"] is None


def test_a_temperatura_e_a_do_recorrente_ate_haver_dado():
    """Palpite não vira padrão: não há um lead de corretora na base pra medir, e
    a conta sobrescreve sem deploy."""
    sg, rc = rxp.funil_padrao("seguros"), rxp.funil_padrao("recorrente")
    for k in ("temp_quente_h", "temp_morno_dias", "temp_frio_tentativas"):
        assert sg[k] == rc[k]


# ── os motivos de perda ───────────────────────────────────────────────────
def test_a_semente_tem_o_que_so_a_corretora_perde():
    chaves = {c for c, _, _ in rxp.semente_motivos("seguros")}
    # o cliente que PULA o corretor não é o mesmo que foi pro concorrente
    assert "renovou_direto" in chaves
    # cobertura insuficiente não é preço nem escopo
    assert "cobertura_nao_atendeu" in chaves
    # e a seguradora pode simplesmente recusar o risco
    assert "nao_tem_perfil" in chaves


def test_a_semente_fala_a_lingua_dela():
    rot = {c: r for c, r, _ in rxp.semente_motivos("seguros")}
    assert rot["achou_caro"] == "Preço do prêmio — acima do que ele queria pagar"
    assert rot["sumiu_apos_proposta"] == "Sumiu depois da cotação"
    assert rot["ficou_com_atual"] == "Ficou com o corretor atual"


def test_so_outro_exige_descricao():
    assert [c for c, _, ex in rxp.semente_motivos("seguros") if ex] == ["outro"]


def test_a_semente_de_eventos_e_a_do_recorrente_nao_mudaram():
    assert "renovou_direto" not in {c for c, _, _ in rxp.semente_motivos("eventos")}
    assert "renovou_direto" not in {c for c, _, _ in rxp.semente_motivos("recorrente")}
    assert "data_indisponivel" in {c for c, _, _ in rxp.semente_motivos("eventos")}


# ── o perfil se comporta como os outros ───────────────────────────────────
def test_a_corretora_recebe_raio_x():
    assert rxp.perfil("seguros")["aplica"] is True
    assert rxp.perfil("seguros")["nicho_escolhido"] is True


def test_seguros_esta_na_lista_de_perfis():
    assert "seguros" in rxp.PERFIS


@pytest.mark.parametrize("chave", rxp.PERFIS)
def test_todo_perfil_tem_funil_ou_declara_que_nao_tem(chave):
    """Guarda contra perfil novo entrar pela metade: ou herda os números, ou é
    'produto', que declara não ter funil."""
    f = rxp.funil_padrao(chave)
    assert f or chave == "produto"


@pytest.mark.parametrize("chave", rxp.PERFIS)
def test_todo_perfil_tem_vocabulario_completo(chave):
    v = rxp._PERFIS[chave]["vocab"]
    assert {"data", "compromisso", "compromissos", "compromisso_kpi",
            "pedido", "oferta"} <= set(v)
