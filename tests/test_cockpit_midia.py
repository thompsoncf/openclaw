"""A foto também aparece no Cockpit — que é a tela que mais importa pra isso.

O QUE ACONTECEU (28/08/2026)
Os passos 1 a 3 subiram e a foto passou a aparecer no painel de Comunicação, no
desktop: `GET /painel/prospeccao/midia/139779` respondeu 200 com 145.063 bytes em
739 ms. Mas o dono abriu primeiro o COCKPIT — o app do vendedor no celular — e lá
viu só `📷 Foto`, texto puro.

Foi falha de escopo minha. O Cockpit é outra tela, com outro renderizador
(`d.innerHTML = rot(m.who) + txt(m.texto)`), e eu não tinha olhado. E é justamente
a tela que sustenta o objetivo do trabalho todo: tirar o vendedor do WhatsApp
pessoal. Foto que não aparece no celular dele é exatamente o motivo de ele voltar
pro aparelho.

O PORTÃO AQUI É OUTRO, e é a parte séria
No painel de prospecção quem autoriza é o `_acesso` (sessão do painel, papel com
'vendas'). No Cockpit é o `lead_do_vendedor`, que revalida a posse do lead. São
dois mundos de permissão diferentes — por isso a rota de mídia do Cockpit é
própria, e não um atalho pra do outro lado.

O id da mensagem é sequencial e adivinhável. O que impede um vendedor de ler a
foto do cliente de outro é a combinação: a mensagem tem que pertencer à conversa
DAQUELE lead, e o lead tem que ser DAQUELE vendedor.
"""
import inspect
from pathlib import Path

RAIZ_WAQR = Path(__file__).resolve().parent.parent / "services" / "wa-qr"

from finance import cockpit as ck
from web import painel_cockpit as pc


# --------------------------------------------------------------- o portão

def test_a_rota_de_midia_existe_e_e_do_cockpit():
    rotas = [r.path for r in pc.router.routes if "midia" in r.path]
    assert rotas == ["/cockpit/lead/{lead_id}/midia/{mensagem_id}"], \
        "a mídia do Cockpit não pode depender da rota do painel: os portões são outros"


def test_a_mensagem_tem_que_ser_da_conversa_daquele_lead():
    """Sem o `cv.prospeccao_id=%s`, o id da mensagem — sequencial e adivinhável —
    seria a única coisa entre um vendedor e a foto do cliente de outro."""
    fonte = inspect.getsource(pc.cockpit_midia)
    assert "cv.prospeccao_id=%s" in fonte
    assert "cv.conta_id=%s" in fonte


def test_e_o_lead_tem_que_ser_daquele_vendedor():
    """A consulta diz que a mensagem é daquele lead; o `lead_do_vendedor` diz que o
    lead é daquele vendedor. As duas coisas, não uma."""
    fonte = inspect.getsource(pc.cockpit_midia)
    assert "ck.lead_do_vendedor(" in fonte
    assert "_sessao(request)" in fonte


def test_sem_sessao_nao_passa():
    fonte = inspect.getsource(pc.cockpit_midia)
    assert "status_code=401" in fonte


def test_expirado_e_falha_continuam_separados():
    fonte = inspect.getsource(pc.cockpit_midia)
    assert "except _wm.Expirou" in fonte and "status_code=410" in fonte
    assert "status_code=502" in fonte


# ------------------------------------------------------ o ponteiro fica no servidor

def test_o_cockpit_manda_tipo_e_tamanho_mas_nunca_o_ponteiro():
    fonte = inspect.getsource(ck.lead_do_vendedor)
    assert 'item["midia"] = {"tipo": midia_tipo, **(midia_meta or {})}' in fonte
    depois = fonte.split('item["midia"]')[1][:250]
    assert "directPath" not in depois and "mediaKey" not in depois


def test_o_polling_repassa_a_midia():
    """Sem isto, a foto que chega com a tela aberta só apareceria ao recarregar."""
    fonte = inspect.getsource(pc.cockpit_lead_mensagens)
    assert '"midia": m["midia"]' in fonte


# ------------------------------------------------- as duas cópias da bolha

def test_a_bolha_existe_nas_duas_cargas():
    """O Cockpit desenha a conversa DUAS vezes: no HTML da primeira carga e no JS do
    polling. Uma cópia só faz a foto aparecer ao abrir e sumir ao chegar (ou o
    contrário) — e ninguém entende por quê."""
    fonte = inspect.getsource(pc)
    assert "_midia_html(lead_id, m)" in fonte, "primeira carga (servidor)"
    assert "rot(m.who)+mid(m)+txt(m.texto)" in fonte, "polling (JS)"


def test_as_duas_copias_carregam_do_mesmo_jeito():
    """`loading=lazy` e `preload=none` são o que faz o custo ser zero pra foto que
    ninguém abre. Valer só numa das cópias é o mesmo que não valer."""
    fonte = inspect.getsource(pc)
    servidor = inspect.getsource(pc._midia_html)
    assert "loading=lazy" in servidor and "preload=none" in servidor
    i = fonte.index("function mid(m)")
    js = fonte[i:i + 1200]
    assert "loading=lazy" in js and "preload=none" in js


def test_os_quatro_tipos_nas_duas_copias():
    servidor = inspect.getsource(pc._midia_html)
    fonte = inspect.getsource(pc)
    js = fonte[fonte.index("function mid(m)"):][:1200]
    for pedaco in ("documento", "video", "figurinha", "<img"):
        assert pedaco in servidor, f"faltou {pedaco} no servidor"
        assert pedaco in js, f"faltou {pedaco} no JS"


def test_mensagem_sem_midia_nao_ganha_nada():
    """É a esmagadora maioria das linhas do Cockpit."""
    assert pc._midia_html(760, {"id": 1, "texto": "oi"}) == ""
    assert pc._midia_html(760, {"id": 1, "midia": {}}) == ""
    assert pc._midia_html(760, {"midia": {"tipo": "imagem"}}) == "", "sem id não dá src"


# ------------------------------------------------------------- o que a bolha mostra

def test_a_foto_aponta_pra_rota_do_cockpit():
    h = pc._midia_html(760, {"id": 139779, "midia": {"tipo": "imagem"}})
    assert "/cockpit/lead/760/midia/139779" in h
    assert "<img" in h and "loading=lazy" in h


def test_o_documento_mostra_nome_e_tamanho():
    h = pc._midia_html(760, {"id": 9, "midia": {"tipo": "documento",
                                                "nome": "contrato.pdf", "bytes": 491520}})
    assert "contrato.pdf" in h and "480 KB" in h and "target=_blank" in h


def test_o_video_nao_baixa_sozinho():
    h = pc._midia_html(760, {"id": 9, "midia": {"tipo": "video", "segundos": 74}})
    assert "preload=none" in h and "autoplay" not in h


def test_tamanho_legivel():
    assert pc._tam_br(0) == "0 B"
    assert pc._tam_br(491520) == "480 KB"
    assert pc._tam_br(1572864) == "1,5 MB"


def test_nome_de_arquivo_nao_escapa_do_html():
    """O nome vem do cliente. Sem escapar, um `<img onerror=...>` no nome do arquivo
    viraria script rodando na tela do vendedor."""
    h = pc._midia_html(760, {"id": 9, "midia": {
        "tipo": "documento", "nome": '<img src=x onerror=alert(1)>'}})
    assert "<img src=x" not in h
    assert "&lt;img" in h


# ---------------------------------------------------------------- a lupa
#
# "Apareceu, show de bola — agora quando clico na foto ela não amplia." A foto na
# bolha é pequena de propósito (a conversa não pode virar um álbum), então ver
# direito precisa de tela cheia. E ampliar DE VERDADE, com pinça e "salvar imagem",
# é coisa que o visualizador do próprio celular já faz melhor do que qualquer coisa
# que a gente reimplementasse — por isso o "abrir original".

def test_a_lupa_existe_nas_duas_telas():
    import inspect
    from web import painel_prospeccao as pp
    assert "class=lupa id=lupa" in inspect.getsource(pc), "Cockpit"
    assert "function cxZoom(" in inspect.getsource(pp), "painel"


def test_a_lupa_fica_fora_do_container_que_empilha():
    """`.chat` tem `position:relative;z-index:1` — um `position:fixed` lá dentro fica
    preso nesse contexto e pode ser pintado por baixo dos irmãos. Mesma razão de a
    lupa do painel nascer direto no <body>."""
    import inspect
    from web import painel_prospeccao as pp
    fonte = inspect.getsource(pc)
    assert '<div class=chat>{chat}</div>{lupa_html}' in fonte, \
        "no Cockpit a lupa é irmã do .chat, não filha"
    assert "document.body.appendChild(l)" in inspect.getsource(pp), \
        "no painel ela vai pro body"


def test_o_clique_e_delegado_e_nao_amarrado_na_imagem():
    """A bolha é redesenhada a cada poll: handler amarrado na criação morre junto com
    o innerHTML, e a foto que chega depois não abriria."""
    import inspect
    from web import painel_prospeccao as pp
    assert "chat.addEventListener('click'" in inspect.getsource(pc)
    assert "document.addEventListener('click'" in inspect.getsource(pp)


def test_so_foto_abre_a_lupa():
    """Vídeo tem controles próprios; abrir a lupa em cima deles atrapalharia."""
    import inspect
    from web import painel_prospeccao as pp
    assert "im.tagName!=='IMG'" in inspect.getsource(pc)
    assert "im.tagName==='IMG'" in inspect.getsource(pp)


def test_fecha_por_toque_e_por_esc():
    import inspect
    from web import painel_prospeccao as pp
    for fonte in (inspect.getsource(pc), inspect.getsource(pp)):
        assert "Escape" in fonte


def test_o_link_do_original_nao_e_engolido_pelo_clique_que_fecha():
    """Ele fica POR CIMA do fundo que fecha. Sem a exceção, tocar nele fecharia a
    lupa sem abrir nada — e é justamente ele que dá pinça e 'salvar imagem'."""
    import inspect
    from web import painel_prospeccao as pp
    assert "id==='lupaAbrir'" in inspect.getsource(pc)
    assert "id!=='cx-zoom-abrir'" in inspect.getsource(pp)


def test_a_lupa_nasce_vazia():
    """Sem `src` até alguém tocar: existir na página não pode custar um download."""
    import inspect
    fonte = inspect.getsource(pc)
    assert "<img id=lupaImg alt=''>" in fonte, "sem src no HTML"
    assert "removeAttribute('src')" in fonte, "e solta a imagem ao fechar"


def test_lupa_fecha_e_global_no_cockpit():
    """Quem chama é o onclick do HTML, que está fora do IIFE do script."""
    import inspect
    assert "window.lupaFecha=function" in inspect.getsource(pc)


# ------------------------------------------------- a cortina não pode pegar carona
#
# O QUE ACONTECEU (28/08/2026, meia hora depois de a lupa subir)
# A aba Comunicação ficou preta. Não a foto: a ABA inteira, com um ⌕ solto no meio.
# Só ali, e só no desktop.
#
# A causa é de uma banalidade constrangedora: `.cx-lupa` JÁ EXISTIA — é o ícone ⌕ do
# campo de busca, criado dias antes nesta mesma tela. Ao batizar a foto ampliada com
# o mesmo nome, e mais abaixo na folha, o ícone da busca herdou
# `position:fixed;inset:0;background:rgba(0,0,0,.93)` e virou uma cortina em cima de
# tudo. O ⌕ no meio da tela era o próprio glifo, centralizado pelo `display:flex`.
#
# Por isso o Cockpit passou ileso: lá a classe se chama `.lupa` e não colide com
# nada. E por isso nenhum teste pegou: todos olhavam a lupa nova, que estava certa.
# O defeito não estava nela — estava no VIZINHO que ela atropelou.
#
# O teste abaixo é genérico de propósito. Travar o nome `cx-zoom` só protegeria
# contra repetir este caso exato; o que se quer impedir é a FORMA do erro — uma
# classe existente ganhar `position:fixed` de uma regra posterior, que é como um
# elemento pequeno vira cortina de tela cheia.

def _classes_repetidas_virando_cortina(css: str) -> list[str]:
    """Classes definidas em regra base mais de uma vez, onde só ALGUMAS trazem
    `position:fixed` — o que quer dizer que uma regra posterior transformou um
    elemento comum em camada de tela cheia."""
    import re
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    regras: dict[str, list[str]] = {}
    for sel, corpo in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        for parte in sel.split(","):
            m = re.fullmatch(r"\.([A-Za-z0-9_-]+)", parte.strip())
            if m:
                regras.setdefault(m.group(1), []).append(corpo.replace(" ", ""))
    return sorted(nome for nome, corpos in regras.items()
                  if len(corpos) > 1
                  and any("position:fixed" in c for c in corpos)
                  and not all("position:fixed" in c for c in corpos))


def _css_da_comunicacao() -> str:
    import re
    from web import painel_prospeccao as pp
    return "\n".join(re.findall(r"<style>(.*?)</style>", pp._COMUNICACAO_TPL, re.S))


def test_nenhuma_classe_da_comunicacao_vira_cortina_sem_querer():
    assert _classes_repetidas_virando_cortina(_css_da_comunicacao()) == []


def test_o_detector_pegaria_o_incidente_de_verdade():
    """Sem esta prova o teste acima passa até quando está cego — e um teste cego é
    pior que nenhum, porque dá a impressão de que a área está coberta."""
    css = _css_da_comunicacao().replace("cx-zoom", "cx-lupa")
    assert _classes_repetidas_virando_cortina(css) == ["cx-lupa"], \
        "o detector precisa acusar exatamente o caso que derrubou a aba"


def test_o_icone_da_busca_continua_sendo_so_um_icone():
    """O sintoma, dito na língua do que se vê: o ⌕ do campo de busca não pode ter
    virado camada de tela cheia."""
    import re
    css = re.sub(r"/\*.*?\*/", "", _css_da_comunicacao(), flags=re.S)
    for corpo in re.findall(r"\.cx-lupa\s*\{([^{}]*)\}", css):
        assert "position:fixed" not in corpo.replace(" ", "")
        assert "inset:0" not in corpo.replace(" ", "")


def test_o_cockpit_tambem_esta_limpo():
    assert _classes_repetidas_virando_cortina(pc._CSS_TEXTO) == []


# ------------------------------------------------- PASSO 4: mandar pelo Zaq
#
# A metade que faltava. Receber já funcionava; pra MANDAR a foto do salão ou o PDF
# do orçamento o vendedor ainda pegava o celular — e o que sai do celular chega sem
# nome, não entra no histórico e mantém viva a conexão paralela que o trabalho todo
# veio fechar.

def test_a_rota_de_anexo_existe_e_e_do_cockpit():
    rotas = [r.path for r in pc.router.routes if "anexo" in r.path]
    assert rotas == ["/cockpit/lead/{lead_id}/anexo"]


def test_sem_sessao_nao_anexa():
    fonte = inspect.getsource(pc.cockpit_anexo)
    assert "_sessao(request)" in fonte and "status_code=401" in fonte


def test_o_corpo_e_binario_e_nao_multipart():
    """Multipart custaria uma cópia a mais dos dois lados — e é a cópia que importa
    quando o arquivo tem 16 MB. Mesmo desenho da rota de voz."""
    fonte = inspect.getsource(pc.cockpit_anexo)
    assert "await request.body()" in fonte
    assert "UploadFile" not in fonte and "File(" not in fonte


def test_o_nome_do_arquivo_viaja_em_base64():
    """Cabeçalho HTTP é latin-1 por especificação, e nome de arquivo brasileiro tem
    acento: cru, ou dá erro no meio do caminho, ou o cliente recebe um PDF chamado
    'OrÃ§amento'."""
    fonte = inspect.getsource(pc.cockpit_anexo)
    assert "x-nome" in fonte and "b64decode" in fonte


def test_o_clipe_so_aparece_onde_o_zaq_manda():
    """Mesmo portão do microfone. Twilio e Cloud API mandam mídia por outros
    caminhos, e nenhum está construído — oferecer o botão faria o vendedor escolher
    o arquivo, esperar, e receber erro."""
    fonte = inspect.getsource(pc)
    i = fonte.index('id=clipe')
    assert 'if pode_voz else ""' in fonte[i:i + 700]


def test_o_anexo_nao_lista_tipos_permitidos():
    """`accept` fechado envelhece contra o vendedor: PDF, planilha e comprovante
    são justamente o que ele mais precisa mandar."""
    fonte = inspect.getsource(pc)
    i = fonte.index("<input type=file id=arq")
    assert "accept=" not in fonte[i:i + 120]


def test_a_tela_confere_o_tamanho_antes_de_subir():
    """Mandar 40 MB pela rede do celular pra ouvir 'grande demais' no fim gasta o
    pacote de dados do vendedor à toa. O servidor confere de novo — tela não é
    fonte confiável."""
    js = pc._ANEXO_JS
    assert "TETO" in js and "f.size>TETO[t]" in js
    from finance import cockpit as ck2
    assert ck2._ANEXO_TETO["imagem"] == 5 * 1048576, "os dois lados têm que combinar"
    assert ck2._ANEXO_TETO["video"] == 32 * 1048576
    assert "5*MB" in js and "32*MB" in js and "16*MB" in js


def test_a_legenda_sai_da_caixa_de_resposta():
    """No WhatsApp a legenda chega colada na foto — é assim que as pessoas mandam."""
    assert 'input[name=texto]' in pc._ANEXO_JS
    assert "legenda" in pc._ANEXO_JS


def test_depois_de_enviar_a_conversa_nao_recarrega():
    """Recarregar custaria ~1s de tela branca logo depois de enviar. O áudio resolve
    chamando `__puxa`; o anexo faz melhor — a bolha JÁ está na tela desde antes do
    upload, então ela só vira definitiva no lugar."""
    js = pc._ANEXO_JS
    assert "location.reload" not in js
    assert "window.__viu" in js, "avisa o polling em vez de buscar de novo"


def test_o_js_do_anexo_e_raw_string():
    """A armadilha desta base: template Python não-raw come a barra do `\\n` e o
    script chega quebrado na página. O _VOZ_JS já é raw pela mesma razão."""
    fonte = inspect.getsource(pc)
    i = fonte.index("_ANEXO_JS = ")
    assert fonte[i:i + 20].startswith('_ANEXO_JS = r"""')


# ------------------------------------------------ o progresso dentro da conversa
#
# O QUE ACONTECEU (28/08/2026, com print junto)
# "Tem como tirar essa janela e deixar um progresso lá dentro do chat, sem
# atrapalhar o envio de mensagem?"
#
# A primeira versão avisava com `alert()`. E `alert` é MODAL: trava a página
# inteira até alguém fechar. O vendedor não conseguia digitar, não via a conversa,
# e a janela ficava por cima da foto que ele tinha acabado de escolher — no meio de
# um atendimento. Pior: num vídeo de 16 MB ela ficaria minutos ali.
#
# Agora o feedback é a própria bolha, que nasce ANTES do upload com a imagem do
# aparelho e uma barra que anda. Nada bloqueia: dá pra anexar outro arquivo e
# continuar conversando enquanto sobe.

def _js_do_anexo() -> str:
    """O JS do anexo SEM os comentários.

    Testar o texto cru dava falso positivo nos dois sentidos: `arrayBuffer` e
    `alert` aparecem justamente nos comentários que explicam POR QUE não são
    usados. Um teste que lê comentário não testa o programa."""
    import re
    js = pc._ANEXO_JS
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return "\n".join(l.split("//")[0] if "//" in l and not l.strip().startswith("*")
                     else l for l in js.splitlines())


def test_nao_existe_mais_janela_bloqueando_a_tela():
    """`alert` e `confirm` são modais — travam a página até alguém fechar. No meio
    de um atendimento isso é o pior lugar possível pra pôr um aviso."""
    js = _js_do_anexo()
    assert "alert(" not in js
    assert "confirm(" not in js


def test_a_bolha_nasce_antes_do_upload_com_a_imagem_do_aparelho():
    """`createObjectURL` aponta pro arquivo no celular: aparece instantâneo, não
    custa download nenhum, e continua servindo depois do envio — a foto que ele
    mandou nunca precisa ser baixada de volta."""
    js = pc._ANEXO_JS
    assert "createObjectURL" in js
    i, k = js.index("function bolha"), js.index("function subir")
    assert i < k, "a bolha é desenhada antes de o envio começar"


def test_a_memoria_do_preview_e_devolvida():
    """Num vídeo de 16 MB isso não é detalhe."""
    assert "revokeObjectURL" in pc._ANEXO_JS


def test_o_arquivo_sobe_sem_passar_pela_memoria_do_navegador():
    """A otimização que mais importa no celular: com `arrayBuffer()` os 16 MB
    inteiros iam pra memória do JS antes de o upload começar. Passando o File pro
    `send`, o navegador lê do disco enquanto sobe."""
    js = _js_do_anexo()
    assert "xhr.send(f)" in js
    assert "arrayBuffer" not in js


def test_o_progresso_e_real_e_nao_uma_animacao():
    """Barra que anda sozinha mente quando a rede cai. Esta vem do upload."""
    js = pc._ANEXO_JS
    assert "xhr.upload.onprogress" in js
    assert "e.loaded" in js and "e.total" in js


def test_cem_por_cento_do_upload_nao_e_o_fim():
    """Depois do último byte o servidor ainda espera o WhatsApp cifrar e receber.
    Mostrar '100%' e ficar parado parece travado."""
    assert "quase lá" in pc._ANEXO_JS


def test_nada_e_desabilitado_enquanto_sobe():
    """O pedido em uma frase: 'sem atrapalhar o envio de msn'. O upload corre por
    baixo e o vendedor continua conversando."""
    js = pc._ANEXO_JS
    assert "clipe.disabled" not in js


def test_erro_tambem_aparece_na_conversa_e_deixa_repetir():
    """O arquivo continua no aparelho — repetir não pode exigir escolher tudo de
    novo. E o recado vai na bolha, pelo mesmo motivo de não haver mais janela."""
    js = pc._ANEXO_JS
    assert "function falhou" in js and "tentar de novo" in js
    assert "subir(f, tipo, legenda, d)" in js, "repete com o MESMO arquivo"


def test_arquivo_grande_avisa_na_conversa_sem_janela():
    js = _js_do_anexo()
    i = js.index("f.size>TETO[t]")
    trecho = js[i:i + 500]
    assert "chat.appendChild" in trecho and "alert" not in trecho


def test_a_bolha_ganha_o_id_e_avisa_o_polling():
    """Sem isso o polling traria a mesma mensagem de novo e o vendedor veria a foto
    que acabou de mandar duas vezes."""
    js = pc._ANEXO_JS
    assert 'd.setAttribute("data-id", j.id)' in js
    assert "window.__viu(j.id)" in js
    assert "window.__viu=function" in inspect.getsource(pc), "e o polling expõe o gancho"


def test_a_rota_devolve_o_id_da_mensagem():
    """É ele que deixa a bolha otimista virar a definitiva."""
    from finance import cockpit as ck2
    fonte = inspect.getsource(ck2.enviar_anexo)
    assert "returning id" in fonte
    assert '"id": msg_id' in fonte


# --------------------------------------------- o teto do vídeo, e o que ele é
#
# O QUE ACONTECEU (28/08/2026): o dono mandou um `IMG_4600.mov` do iPhone e viu
# "não enviado — passa de 16 MB". O código fez o certo; o problema era o número.
#
# E ERA PIOR QUE UM NÚMERO BAIXO: o comentário ao lado dele dizia que 16 MB era "o
# teto do próprio WhatsApp". Não é — a conferência no Baileys não achou limite de
# bytes nenhum, nem constante nem checagem. O teto sempre foi NOSSO, escolhido pela
# memória do processo que segura as sessões. Vestido de restrição externa, ele
# parecia inegociável; sabendo que é nosso, dá pra medir e mudar.

def test_o_teto_do_video_cobre_video_de_celular():
    """16 MB não cobria: um `.mov` de iPhone passa disso com poucos segundos."""
    from finance import cockpit as ck2
    assert ck2._ANEXO_TETO["video"] == 32 * 1048576


def test_os_tres_lugares_combinam():
    """Node, Python e tela. Divergindo, o vendedor ouve um número da tela e outro
    do servidor — ou pior, sobe 30 MB pra ser recusado no fim."""
    import re
    from finance import cockpit as ck2
    node = (RAIZ_WAQR / "server.js").read_text(encoding="utf-8")
    m = re.search(r"const LIMITE_MIDIA = \{([^}]+)\}", node)
    assert m, "LIMITE_MIDIA sumiu do server.js"
    for tipo, mb in (("imagem", 5), ("video", 32), ("documento", 16)):
        assert f"{tipo}: {mb} * 1024 * 1024" in m.group(1), f"{tipo} divergiu no Node"
        assert ck2._ANEXO_TETO[tipo] == mb * 1048576, f"{tipo} divergiu no Python"
        assert f"{tipo}:{mb}*MB" in pc._ANEXO_JS.replace(" ", ""), f"{tipo} divergiu na tela"


def test_o_comentario_diz_de_quem_e_o_teto():
    """A afirmação errada custou uma decisão: enquanto o limite parecia do WhatsApp,
    subir nem era considerado.

    O teste checa o que o comentário AFIRMA, não a ausência de uma frase — o texto
    novo cita a afirmação velha justamente pra registrar o erro, e um teste que
    procurasse a frase solta acusaria a correção como se fosse o defeito. (Foi o
    que aconteceu na primeira versão deste teste.)"""
    import re
    node = (RAIZ_WAQR / "server.js").read_text(encoding="utf-8")
    i = node.index("const LIMITE_MIDIA")
    # junta as linhas do comentário numa só: a frase que importa atravessa a quebra,
    # e procurar texto cru num comentário quebrado testa a largura da linha, não o
    # que está escrito
    trecho = re.sub(r"\s*\n\s*//\s*", " ", node[max(0, i - 2200):i])
    assert "O TETO É NOSSO, NÃO DO WHATSAPP" in trecho
    assert "não achou limite de bytes nenhum" in trecho, "e diz como se sabe disso"


def test_a_recusa_diz_o_tamanho_do_arquivo_e_nao_so_o_limite():
    """"passa de 16 MB" não deixa decidir nada: passou por pouco ou pelo dobro?
    Com os dois números dá pra saber se vale mandar um trecho menor."""
    js = _js_do_anexo()
    i = js.index("f.size>TETO[t]")
    trecho = js[i:i + 500]
    assert "tam(f.size)" in trecho, "o tamanho real do arquivo"
    assert "o limite é" in trecho


def test_o_nome_do_arquivo_nao_gruda_no_recado():
    """No print de 28/08 apareceu 'IMG_4600.movnão enviado'."""
    js = _js_do_anexo()
    i = js.index("f.size>TETO[t]")
    trecho = js[i:i + 500]
    assert "esc(f.name)+'</b>'" in trecho, "o nome fecha num <b> antes do recado"
