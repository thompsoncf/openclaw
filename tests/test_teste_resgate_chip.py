"""O teste do resgate no chip certo (27/09/2026, mockup aprovado pelo dono em
docs/mockups/teste_resgate_chip_certo.html) — a ligação na tela e no webhook.

O dono escreveu pro chip Thiago pra testar o ZAQ SDR e quem respondeu foi o CP Zarb:
o teste do resgate, ainda aberto, pegava a mensagem do supervisor em qualquer chip e
respondia sempre pelo principal. O motor está em tests/test_resgate.py; aqui, que o
webhook passa o chip adiante, e que a tela mostra o teste aberto, o Encerrar, o selo
🧪 e o alerta dos avisos que iam pra IA. Sem banco."""
import inspect
from datetime import datetime, timezone

from web import janela_lead as jl
from web import painel_prospeccao as pp
from web.portal import _env

FONTE = inspect.getsource(pp)


def test_o_webhook_passa_o_chip_pro_resgate():
    wh = inspect.getsource(pp._webhook_wa_qr_sync)
    assert "_rg.e_do_supervisor(c, empresa_id, sender, chip_id)" in wh
    assert 'payload.get("id") or None, chip_id)' in wh, "a resposta sai pelo chip onde ele escreveu"
    # o eco da saída também olha o chip: o que o ZAQ SDR responde ao supervisor no
    # chip Thiago é conversa de verdade, e tem que ser registrado
    saida = inspect.getsource(pp._webhook_wa_qr_saida_sync)
    assert "_rg.e_do_supervisor(c, empresa_id, destinatario, chip_id)" in saida
    # ...menos o que o teste mandou (🧪), que não é conversa em chip nenhum
    assert "texto.startswith(_rg.TESTE_MARCA) and _rg.e_o_supervisor(c, empresa_id, destinatario)" in saida


def test_a_regra_do_chip_adota_o_supervisor_antes_de_decidir_quem_atende():
    wh = inspect.getsource(pp._webhook_wa_qr_sync)
    adota = wh.index("_cr.adotar_do_supervisor(")
    assert adota < wh.index("atender = nova and _agente_atende("), \
        "a IA só acorda se a adoção vier antes da pergunta 'quem atende'"
    assert "if do_supervisor and nova:" in wh, "reentrega não adota de novo"


def test_a_rota_de_encerrar_o_teste():
    rotas = {r.path for r in pp.router.routes}
    assert "/painel/prospeccao/comunicacao/resgate/encerrar-teste" in rotas
    src = inspect.getsource(pp.comunicacao_resgate_encerrar_teste)
    assert 'ctx["gerencia"]' in src, "só a gerência encerra"


def _cartao_resgate():
    tpl = pp._COMUNICACAO_TPL
    ini = tpl.index('{% if resgate %}{% set rg = resgate.cfg %}')
    fim = tpl.index("</form>\n    </details>\n    {% endif %}", ini) + len("</form>\n    </details>\n    {% endif %}")
    return tpl[ini:fim]


def _render_resgate(teste):
    cfg = {"modo": "ensaio", "pausado_em": None, "dias": 7, "membro_id": 1, "aquecido_dias": 14,
           "teto_dia": 20, "hora_ini": 9, "hora_fim": 19, "dias_semana": [0, 1],
           "supervisor_whatsapp": "5511987654321", "incluir_perdidos": False,
           "aviso_vendedor": True}
    resgate = {"cfg": cfg, "membros": [{"id": 1, "nome": "ZAQ SDR"}], "fila": 3, "segurados": 0,
               "por_faixa": {}, "faixas": {}, "a_caminho": 0, "hoje": {"envios": 0},
               "com_ia": {"total": 0}, "regra_ok": True, "teste": teste}
    voc = {"lead": "lead", "leads": "leads", "cliente": "cliente"}
    return _env.from_string(_cartao_resgate()).render(resgate=resgate, voc=voc, regra_eventos=True)


def test_o_cartao_mostra_o_teste_aberto_com_encerrar_e_a_conversa():
    teste = {"lead": 595, "quem": "Gabriela", "chip": "CP Zarb", "n": 2,
             "falas": [{"quem": "ia", "texto": "Oi Gabriela!"}, {"quem": "cliente", "texto": "quero mais opções"}],
             "ultima": datetime.now(timezone.utc), "expira_em": datetime.now(timezone.utc),
             "ultima_txt": "10:48", "expira_txt": "12:48"}
    html = _render_resgate(teste)
    assert "Teste aberto · você é Gabriela (lead #595)" in html
    assert "pelo <b>CP Zarb</b>" in html and "fecha sozinho às <b>12:48</b>" in html
    assert 'formaction="/painel/prospeccao/comunicacao/resgate/encerrar-teste"' in html
    assert "Ver a conversa" in html and "quero mais opções" in html
    # com teste aberto, começar outro pergunta antes
    assert "Já tem um teste aberto com Gabriela. Começar outro?" in html


def test_sem_teste_o_cartao_nao_tem_a_faixa():
    html = _render_resgate(None)
    assert "Teste aberto" not in html and "encerrar-teste" not in html
    assert "Começar outro?" not in html
    assert "só é o \"cliente do teste\" no chip do teste" in html


def test_a_ia_sai_das_listas_de_quem_e_chamado():
    """A IA não tem celular: agenda, desconto, anfitriã e conferência não vão pra ela.
    A regra que ainda aponta pra IA mostra o valor (com o ⚠️) e o alerta no topo."""
    tpl = pp._COMUNICACAO_TPL
    for campo in ("aviso_agenda_membro_id", "aviso_dono_membro_id", "visita_anfitria_id",
                  "orc_conferente_id"):
        sel = tpl[tpl.index(f'name="{campo}"'):]
        sel = sel[:sel.index("</select>")]
        assert "m.id not in regra_ia_ids" in sel, campo
        assert "⚠️ é a IA" in sel, campo
    assert "Alguns avisos desta regra vão para a IA" in tpl
    # o dono da regra É a IA: essa lista continua com ela
    dono = tpl[tpl.index('name="membro_id"><option value="">escolha</option>'):]
    assert "regra_ia_ids" not in dono[:dono.index("</select>")]


def test_o_selo_do_supervisor_no_card_e_na_ficha():
    assert '{% if c.sup %}<div class="kbtri sup"' in pp._KANBAN_TPL
    assert 'cc["sup"] = cc["id"] in sup_ids' in FONTE
    js = inspect.getsource(jl)
    assert "if(d.supervisor)" in js and "número do supervisor do Resgate" in js
    assert '"supervisor": supervisor,' in inspect.getsource(pp.prospeccao_resumo)


def test_o_template_inteiro_compila():
    _env.parse(pp._COMUNICACAO_TPL)
    _env.parse(pp._KANBAN_TPL)


def test_o_lead_do_supervisor_sai_do_desafio_e_do_raio_x():
    """A condição em si é testada em tests/test_resgate.py (`sql_fora_do_supervisor`);
    aqui, que as duas telas a usam — o dono decidiu que o teste dele não vira placar."""
    from finance import desafio_ia, raio_x_dono
    lead = inspect.getsource(desafio_ia._por_lead)
    assert "_rg.sql_fora_do_supervisor(c, conta_id)" in lead and "*fora_v" in lead
    assert inspect.getsource(raio_x_dono.dono).count("_fora_do_supervisor(") == 1
    assert inspect.getsource(raio_x_dono.da_visita).count("_fora_do_supervisor(") == 1
