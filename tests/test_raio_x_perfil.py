"""O perfil do Raio-X (finance/raio_x_perfil): o que a empresa vende decide o que
o Raio-X mede. Puro, sem banco — é o lugar onde se garante que nenhuma palavra
de festa vaza pra quem vende mensalidade, e que conta de produto fica de fora.
"""
from finance import raio_x_perfil as rxp


def test_perfil_por_nicho_le_os_portoes_que_ja_existem():
    assert rxp.perfil_por_nicho("eventos") == "eventos"
    for s in ("consultoria", "tecnologia", "contabilidade", "advocacia", "agencia", "educacao"):
        assert rxp.perfil_por_nicho(s) == "recorrente", s
    for s in ("hortifruti", "vestuario", "minimercado", "farmacia"):
        assert rxp.perfil_por_nicho(s) == "produto", s
    # misto (produto + serviço) vende serviço: recorrente
    assert rxp.perfil_por_nicho("salao_completo") == "recorrente"


def test_conta_sem_nicho_cai_em_recorrente_e_a_tela_pede_pra_escolher():
    """Decisão do dono (05/09): sem nicho é o perfil sem festa, e a tela avisa."""
    for s in (None, "", "nicho_que_nao_existe"):
        p = rxp.perfil(s)
        assert p["chave"] == "recorrente" and p["nicho_escolhido"] is False and p["aplica"] is True
    assert rxp.perfil("eventos")["nicho_escolhido"] is True


def test_o_perfil_de_produto_nao_se_aplica():
    p = rxp.perfil("hortifruti")
    assert p["aplica"] is False and p["filtros"] == () and p["blocos"] == () and p["motivos"] == ()


def test_eventos_e_recorrente_nao_compartilham_o_que_e_de_festa():
    ev, rc = rxp.perfil("eventos"), rxp.perfil("consultoria")
    assert ev["vocab"]["data"] is True and rc["vocab"]["data"] is False
    assert ev["vocab"]["compromisso"] == "visita" and rc["vocab"]["compromisso"] == "reunião"
    festa = {"tipo", "mes", "dia", "conv", "demanda_agenda", "dia_festa", "tipos", "festa"}
    assert festa & (set(rc["filtros"]) | set(rc["blocos"]) | set(rc["faixas"])) == set()
    assert {"tipo", "mes", "dia", "conv"} <= set(ev["filtros"])
    assert {"segmento", "porte", "uf", "servico"} <= set(rc["filtros"])
    assert {"mrr", "segmentos", "servicos", "reunioes"} <= set(rc["blocos"])
    # a proposta parada entra nos dois; a festa perto só em eventos
    assert "proposta" in ev["faixas"] and "proposta" in rc["faixas"]
    assert "festa" in ev["faixas"] and "festa" not in rc["faixas"]


def test_cada_perfil_oferece_seis_motivos_e_troca_so_um():
    ev = [k for k, _ in rxp.perfil("eventos")["motivos"]]
    rc = [k for k, _ in rxp.perfil("consultoria")["motivos"]]
    assert len(ev) == 6 and len(rc) == 6
    assert "data_indisponivel" in ev and "ficou_com_atual" not in ev
    assert "ficou_com_atual" in rc and "data_indisponivel" not in rc
    assert set(ev) - {"data_indisponivel"} == set(rc) - {"ficou_com_atual"}
    assert rxp.rotulo_motivo("ficou_com_atual") == "Ficou com o fornecedor atual"
    assert rxp.rotulo_motivo(None) == "sem motivo"


def test_familia_do_segmento_encurta_o_cnae():
    f = rxp.familia_segmento
    assert f("Atividades de estética e outros serviços de cuidados com a beleza") == ("estetica", "Estética / beleza")
    assert f("Atividade odontológica") == ("clinica", "Clínica / saúde")
    assert f("Loja") == ("loja", "Loja / comércio")
    assert f("Comércio varejista de roupas") == ("loja", "Loja / comércio")
    assert f("Serviços de organização de feiras, congressos, exposições e festas") == ("eventos", "Eventos")
    assert f("Restaurantes e similares") == ("alimentacao", "Alimentação")
    assert f("Atividades de contabilidade") == ("escritorio", "Escritório / serviços")
    assert f("Produção de espetáculos circenses") == ("eventos", "Eventos")
    assert f("") == ("sem", "sem segmento") and f(None) == ("sem", "sem segmento")
    assert f("Cultivo de soja") == ("outro", "Outro")
    assert ("loja", "Loja / comércio") in rxp.familias() and rxp.familias()[-1] == ("sem", "sem segmento")


def test_porte_em_tres_faixas():
    assert rxp.chave_porte("Microempresa") == "me"
    assert rxp.chave_porte("Empresa de Pequeno Porte") == "epp"
    assert rxp.chave_porte("Demais") == "demais"
    assert rxp.chave_porte("") == "sem" and rxp.chave_porte(None) == "sem"


def test_equipe_esconde_o_raio_x_de_segunda_pra_conta_de_produto():
    import web.painel_equipe  # noqa: F401 — registra o template
    from web.portal import _env
    t = _env.get_template("equipe")
    base = dict(tem_pj=True, membros=[], papeis=[], convites=[], comissoes={}, request=None,
                raiox=None, grupos=[], raiox_qr=False)
    bloco = t.blocks["conteudo"]
    com = "".join(bloco(t.new_context(dict(base, raio_x_perfil=rxp.perfil("eventos")))))
    sem = "".join(bloco(t.new_context(dict(base, raio_x_perfil=rxp.perfil("hortifruti")))))
    assert "Raio-X de segunda" in com and "Raio-X de segunda" not in sem
