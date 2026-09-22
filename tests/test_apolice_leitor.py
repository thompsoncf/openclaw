"""O PRÉ-CADASTRO LÊ O DOCUMENTO — o gêmeo do leitor de eventos no nicho seguros.

Pedido do dono em 21/09/2026, depois de eu levantar que nem o agente do WhatsApp
nem o bot do Telegram liam apólice: "pode fazer o leitor automatico".

O que este arquivo segura, em ordem de importância:

1. ELE NÃO CADASTRA. Gravar sozinho transformaria erro de leitura em dado errado
   no banco, e vigência lida errada é alerta que não dispara.
2. OS DOIS PORTÕES. Conta do nicho seguros, remetente liberado. Sem eles o leitor
   baixaria os boletos que o fornecedor manda pro mesmo número.
3. NÃO RELÊ. Uma leitura por mensagem, inclusive quando o wa-qr reentrega.
4. A FALHA FICA GRAVADA. Sem isso ele tentaria pra sempre o PDF que o CDN apagou.
"""
import json
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import apolice_leitor as al

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 37          # a Liberal, nicho seguros
OUTRA = 39          # uma conta que não é corretora

_BASE = """
create table contas (id bigint primary key, tipo text, nome text, nicho_id bigint);
create table nichos (id bigint primary key, slug text, nome text);
create table conversas (id bigserial primary key, conta_id bigint, contato_ref text,
  contato_nome text, canal text default 'whatsapp', ultima_msg_em timestamptz);
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  texto text, provider_sid text, criado_em timestamptz not null default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
create table clientes (id bigserial primary key, dono_id bigint, nome text);
-- o que a migração 278 exige pra criar `apolices`: o corretor e o dedup do alerta
create table membros (id bigserial primary key, conta_id bigint, nome text,
  papel text default 'membro', ativo boolean default true);
create table lembretes_enviados (id bigserial primary key,
  conta_id bigint not null references contas(id) on delete cascade,
  tipo text not null check (tipo in ('resumo','aviso')),
  chave text not null, enviado_em timestamptz not null default now(),
  unique (conta_id, tipo, chave));
insert into nichos values (23,'corretora-seguros','Corretora de Seguros'),
                          (7,'buffet','Buffet');
insert into contas values (37,'pj','Liberal Neto',23), (39,'pj','Festa Boa',7);
"""


@pytest.fixture(scope="module")
def pool():
    """BANCO PRÓPRIO, como `tests/test_apolices.py` faz. Derrubar tabela no banco de
    teste compartilhado levou 485 erros em outros módulos na primeira tentativa —
    `contas`, `mensagens` e `conversas` são de todo mundo."""
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_apolice_leitor_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE)
        c.execute((MIG / "278_apolices.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "286_apolices_pdf.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "287_apolice_perdida.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "289_apolice_remetentes.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "304_apolice_lida.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "305_apolice_lida_telegram.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "306_apolice_lida_hash.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "307_apolice_lida_descartada.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "308_apolice_excluida.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture
def limpo(pool):
    with pool.connection() as c:
        for t in ("apolice_lida", "apolice_remetentes", "apolices", "mensagens", "conversas"):
            c.execute(f"delete from {t}")
        c.commit()
    return pool


def _pdf(limpo, conta_id, *, nome="PROPOSTA.pdf", ref="5586911111111",
         liberado=True, mime="application/pdf", direcao="in", tipo="documento"):
    with limpo.connection() as c:
        cv = c.execute("insert into conversas (conta_id, contato_ref, contato_nome) "
                       "values (%s,%s,'Cássio') returning id", (conta_id, ref)).fetchone()[0]
        mid = c.execute(
            "insert into mensagens (conversa_id, direcao, texto, midia_ref, midia_tipo, midia_meta) "
            "values (%s,%s,'documento',%s,%s,%s) returning id",
            (cv, direcao, json.dumps({"directPath": "/x", "mediaKey": "aa", "mimetype": mime}),
             tipo, json.dumps({"nome": nome, "bytes": 1000}))).fetchone()[0]
        if liberado:
            c.execute("insert into apolice_remetentes (conta_id, contato_ref, rotulo) "
                      "values (%s,%s,'Cássio') on conflict do nothing", (conta_id, ref))
        c.commit()
    return cv, mid


def _finge(monkeypatch, *, conteudo=b"%PDF-1.4 fake", leitura=None, expira=False,
           seguros=True, cofre=True):
    """Troca o que sai da máquina: CDN, leitor de PDF e cofre."""
    from finance import wa_midia as wm

    def buscar(ref, tipo, **kw):
        if expira:
            raise wm.Expirou(404)
        yield conteudo
    monkeypatch.setattr(wm, "buscar", buscar)
    monkeypatch.setattr(al, "_e_seguros", lambda pool, cid: seguros)
    monkeypatch.setattr(al._cofre, "configurado", lambda: cofre)
    monkeypatch.setattr(al, "_guardar", lambda cid, b, n: (f"apolice/{cid}/x.pdf", len(b)))
    if leitura is not None:
        # a assinatura ESPELHA a de verdade (`ler(conteudo, proibidos)`): stub com
        # menos parâmetros que a função vira TypeError engolido pelo `except` do
        # `ler_bytes`, e o teste falha dizendo "não leu" em vez de "chamou errado".
        monkeypatch.setattr(al.apdf, "ler", lambda b, proibidos=(): leitura)


def _leitura(**kw):
    from finance import apolice_pdf as apdf
    L = apdf.Leitura()
    L.seguradora = kw.get("seguradora", "Allianz")
    L.reconhecida = kw.get("reconhecida", True)
    L.e_apolice = kw.get("e_apolice", L.reconhecida)
    L.paginas = 7
    L.campos = kw.get("campos", {"nome": "MARIA DE FATIMA", "numero_proposta": "139041981"})
    L.checagens = [("CPF", True, "dígito bate")]
    L.nao_achou, L.avisos = [], []
    return L


# ───────────────────────────── 1. ele não cadastra ─────────────────────────────

def test_o_leitor_nao_cadastra_apolice(limpo, monkeypatch):
    _pdf(limpo, CONTA)
    _finge(monkeypatch, leitura=_leitura())
    assert al.ler_pendentes(limpo, CONTA)["lidas"] == 1
    with limpo.connection() as c:
        assert c.execute("select count(*) from apolices").fetchone()[0] == 0
        assert c.execute("select count(*) from apolice_lida").fetchone()[0] == 1


def test_o_que_ele_guarda_e_o_bastante_pra_lista_e_pro_formulario(limpo, monkeypatch):
    from datetime import date
    _pdf(limpo, CONTA)
    _finge(monkeypatch, leitura=_leitura(campos={"nome": "MARIA DE FATIMA",
                                                 "seguradora": "Allianz",
                                                 "vigencia_fim": date(2027, 7, 23)}))
    al.ler_pendentes(limpo, CONTA)
    with limpo.connection() as c:
        mid = c.execute("select mensagem_id from apolice_lida").fetchone()[0]
    d = al.uma(limpo, CONTA, mid)
    assert d["seguradora"] == "Allianz" and d["segurado"] == "MARIA DE FATIMA"
    assert d["vigencia_fim"] == date(2027, 7, 23)
    assert d["form"]["vigencia_fim"] == "2027-07-23", "o formulário já vem pronto"
    assert "Allianz" in al.resumo(d) and "23/07/2027" in al.resumo(d)


# ───────────────────────────── 2. os dois portões ──────────────────────────────

def test_conta_que_nao_e_corretora_nao_entra(limpo, monkeypatch):
    _pdf(limpo, OUTRA)
    _finge(monkeypatch, leitura=_leitura(), seguros=False)
    r = al.ler_pendentes(limpo, OUTRA)
    assert r["lidas"] == 0 and r["motivo"] == "conta não é de seguros"


def test_remetente_nao_liberado_nao_e_baixado(limpo, monkeypatch):
    _pdf(limpo, CONTA, nome="invoice-200017.pdf", ref="5586922222222", liberado=False)
    _finge(monkeypatch, leitura=_leitura())
    assert al.ler_pendentes(limpo, CONTA)["lidas"] == 0
    with limpo.connection() as c:
        assert c.execute("select count(*) from apolice_lida").fetchone()[0] == 0


def test_sem_cofre_ele_nao_tenta(limpo, monkeypatch):
    """Ler sem ter onde guardar o PDF perderia o arquivo quando o CDN expirasse."""
    _pdf(limpo, CONTA)
    _finge(monkeypatch, leitura=_leitura(), cofre=False)
    r = al.ler_pendentes(limpo, CONTA)
    assert r["lidas"] == 0 and r["motivo"] == "cofre não configurado"


def test_so_entrada_e_so_pdf(limpo, monkeypatch):
    _pdf(limpo, CONTA, nome="mandei eu.pdf", ref="5586911111111", direcao="out")
    _pdf(limpo, CONTA, nome="foto.jpg", ref="5586911111111", tipo="imagem", mime="image/jpeg")
    _finge(monkeypatch, leitura=_leitura())
    assert al.ler_pendentes(limpo, CONTA)["lidas"] == 0


# ───────────────────────────── 3. não relê ─────────────────────────────────────

def test_a_mesma_mensagem_nao_e_lida_duas_vezes(limpo, monkeypatch):
    cv, _ = _pdf(limpo, CONTA)
    _finge(monkeypatch, leitura=_leitura())
    assert al.ler_pendentes(limpo, CONTA, cv)["lidas"] == 1
    assert al.ler_pendentes(limpo, CONTA, cv)["lidas"] == 0, "reentrega não relê"
    with limpo.connection() as c:
        assert c.execute("select count(*) from apolice_lida").fetchone()[0] == 1


def test_le_so_a_conversa_que_recebeu(limpo, monkeypatch):
    cv1, _ = _pdf(limpo, CONTA, ref="5586911111111")
    _pdf(limpo, CONTA, ref="5586911111111")
    _finge(monkeypatch, leitura=_leitura())
    assert al.ler_pendentes(limpo, CONTA, cv1)["lidas"] == 1
    with limpo.connection() as c:
        assert c.execute("select count(*) from apolice_lida").fetchone()[0] == 1


# ───────────────────────────── 4. a falha fica gravada ─────────────────────────

def test_arquivo_que_o_whatsapp_apagou_vira_erro_gravado(limpo, monkeypatch):
    _pdf(limpo, CONTA)
    _finge(monkeypatch, expira=True)
    r = al.ler_pendentes(limpo, CONTA)
    assert r["falhas"] == 1 and r["lidas"] == 0
    with limpo.connection() as c:
        erro = c.execute("select erro from apolice_lida").fetchone()[0]
    assert "apagou" in erro
    # e não tenta de novo: repetir só gastaria banda pra receber 404
    assert al.ler_pendentes(limpo, CONTA)["falhas"] == 0


def test_pdf_ilegivel_tambem_fica_registrado(limpo, monkeypatch):
    _pdf(limpo, CONTA)
    _finge(monkeypatch)
    monkeypatch.setattr(al.apdf, "ler", lambda b, proibidos=(): (_ for _ in ()).throw(ValueError("PDF vazio")))
    assert al.ler_pendentes(limpo, CONTA)["falhas"] == 1
    with limpo.connection() as c:
        assert c.execute("select erro from apolice_lida").fetchone()[0] == "PDF vazio"


def test_o_resumo_de_uma_falha_e_a_propria_falha(limpo):
    assert al.resumo({"erro": "o WhatsApp já apagou este arquivo"}) == \
        "o WhatsApp já apagou este arquivo"


def test_sem_seguradora_reconhecida_o_resumo_nao_inventa(limpo):
    assert al.resumo({"seguradora": None, "segurado": None, "vigencia_fim": None}) == ""


# ───────────────────────────── 5. nunca estoura pro webhook ────────────────────

def test_o_caminho_do_webhook_engole_a_falha(limpo, monkeypatch):
    def explode(*a, **kw):
        raise RuntimeError("banco caiu")
    monkeypatch.setattr(al, "ler_pendentes", explode)
    al.ler_pendentes_bg(limpo, CONTA, 1)      # não levanta


# ═══════════════ 6. a segunda porta: o Telegram (migração 305) ═══════════════
#
# O portão aqui é MAIS forte que o do WhatsApp: lá o remetente é um número que
# alguém liberou; aqui é um membro autenticado da conta. Por isso `ler_bytes` não
# pede remetente liberado — quem chama é que já provou quem é.


def test_o_telegram_grava_pre_cadastro_sem_mensagem(limpo, monkeypatch):
    _finge(monkeypatch, leitura=_leitura())
    r = al.ler_bytes(limpo, CONTA, b"%PDF fake", "APOLICE.pdf",
                     origem="telegram", de="Cássio")
    assert r["ok"] and r["id"]
    with limpo.connection() as c:
        origem, msg, de = c.execute(
            "select origem, mensagem_id, de from apolice_lida").fetchone()
    assert origem == "telegram" and msg is None and de == "Cássio"


def test_o_telegram_nao_cadastra_apolice(limpo, monkeypatch):
    _finge(monkeypatch, leitura=_leitura())
    al.ler_bytes(limpo, CONTA, b"%PDF fake", "APOLICE.pdf", origem="telegram", de="Cássio")
    with limpo.connection() as c:
        assert c.execute("select count(*) from apolices").fetchone()[0] == 0


def test_dois_pdfs_do_telegram_convivem(limpo, monkeypatch):
    """Sem mensagem os dois têm `mensagem_id` nulo; o índice é parcial justamente
    pra isso não virar colisão."""
    _finge(monkeypatch, leitura=_leitura())
    al.ler_bytes(limpo, CONTA, b"%PDF a", "A.pdf", origem="telegram", de="Cássio")
    al.ler_bytes(limpo, CONTA, b"%PDF b", "B.pdf", origem="telegram", de="Cássio")
    assert len(al.sem_mensagem(limpo, CONTA)) == 2


def test_o_que_veio_do_telegram_aparece_na_lista_com_o_resumo(limpo, monkeypatch):
    from datetime import date
    _finge(monkeypatch, leitura=_leitura(campos={"nome": "JOSE ALVES",
                                                 "vigencia_fim": date(2027, 1, 30)}))
    al.ler_bytes(limpo, CONTA, b"%PDF a", "A.pdf", origem="telegram", de="Cássio")
    itens = al.sem_mensagem(limpo, CONTA)
    assert len(itens) == 1 and itens[0]["de"] == "Cássio"
    assert "JOSE ALVES" in al.resumo(itens[0]) and "30/01/2027" in al.resumo(itens[0])


def test_depois_de_cadastrada_some_da_lista_do_telegram(limpo, monkeypatch):
    """A marca é o CAMINHO DO PDF: a apólice confirmada guarda o mesmo arquivo."""
    _finge(monkeypatch, leitura=_leitura())
    al.ler_bytes(limpo, CONTA, b"%PDF a", "A.pdf", origem="telegram", de="Cássio")
    with limpo.connection() as c:
        caminho = c.execute("select pdf_caminho from apolice_lida").fetchone()[0]
        c.execute("""insert into apolices (conta_id, seguradora, ramo, vigencia_fim,
                                           situacao, pdf_caminho)
                     values (%s,'Allianz','auto',date '2027-01-30','vigente',%s)""",
                  (CONTA, caminho))
        c.commit()
    assert al.sem_mensagem(limpo, CONTA) == []


def test_por_id_devolve_a_leitura_guardada(limpo, monkeypatch):
    _finge(monkeypatch, leitura=_leitura())
    r = al.ler_bytes(limpo, CONTA, b"%PDF a", "A.pdf", origem="telegram", de="Cássio")
    d = al.por_id(limpo, CONTA, r["id"])
    assert d and d["seguradora"] == "Allianz" and d["form"]
    assert al.por_id(limpo, OUTRA, r["id"]) is None, "não vaza entre contas"


def test_pdf_que_o_leitor_nao_entende_nao_some(limpo, monkeypatch):
    """A falha fica gravada também pelo Telegram — quem chamou decide o que fazer."""
    _finge(monkeypatch)
    monkeypatch.setattr(al.apdf, "ler", lambda b, proibidos=(): (_ for _ in ()).throw(ValueError("PDF vazio")))
    r = al.ler_bytes(limpo, CONTA, b"xx", "A.pdf", origem="telegram", de="Cássio")
    assert r["ok"] is False and r["erro"] == "PDF vazio"


# ────────── 5. quem fica com o documento (21/09/2026) ──────────
#
# O caso: o corretor mandou a apólice da Azul pro número do assistente e o sistema
# marcou um LEMBRETE DE PAGAR PARCELA. Duas causas, e estes testes seguram as duas:
# a porta escolhia pelo LAYOUT (e o único medido é o da Allianz), e o assistente
# não tinha porta nenhuma.


def test_a_porta_pega_apolice_de_seguradora_que_eu_nao_sei_ler():
    """`tomou` olha `e_apolice`, não `reconhecida`. Ler pouco de uma apólice é um
    formulário pela metade; ler ela como cupom é o dado na gaveta errada."""
    L = _leitura(reconhecida=False, seguradora=None, e_apolice=True)
    assert al.tomou({"ok": True, "leitura": L})


def test_a_porta_devolve_o_comprovante_pro_caixa():
    """O que NÃO pode quebrar: a corretora usa o mesmo número pras duas coisas."""
    L = _leitura(reconhecida=False, seguradora=None, e_apolice=False)
    assert not al.tomou({"ok": True, "leitura": L})


def test_leitura_que_falhou_nao_e_assumida():
    assert not al.tomou({"ok": False, "leitura": None, "erro": "não é PDF"})
    assert not al.tomou({"ok": True, "leitura": None})


def test_o_aviso_diz_a_seguradora_achada_sem_layout():
    from datetime import date
    L = _leitura(reconhecida=False, seguradora=None, e_apolice=True,
                 campos={"seguradora": "Azul Seguros", "nome": "LUZIA AUREA",
                         "vigencia_fim": date(2027, 9, 26), "placa": "QRQ4H54"})
    pares = dict(al.campos_do_aviso(L))
    assert pares["Seguradora"] == "Azul Seguros"
    assert pares["Segurado"] == "LUZIA AUREA"
    assert pares["Vence"] == "26/09/2027"
    assert pares["Placa"] == "QRQ4H54"


def test_o_rodape_avisa_quando_o_modelo_e_desconhecido():
    """A diferença entre 'confira' e 'preencha' é o que a pessoa precisa saber."""
    assert "preencher" in al.rodape_do_aviso(_leitura(reconhecida=False, e_apolice=True))
    assert "conferir" in al.rodape_do_aviso(_leitura(reconhecida=True))


def test_a_apolice_que_veio_pelo_assistente_aparece_na_lista(limpo, monkeypatch):
    """O recorte de `sem_mensagem` é `mensagem_id is null`, e não a origem: origem
    'whatsapp' sem mensagem é exatamente a terceira porta, e por origem ela ficaria
    invisível nas duas listas."""
    _finge(monkeypatch, leitura=_leitura())
    al.ler_bytes(limpo, CONTA, b"%PDF a", "APOLICE.pdf", origem="whatsapp", de="Cássio")
    itens = al.sem_mensagem(limpo, CONTA)
    assert len(itens) == 1
    assert itens[0]["de"] == "Cássio" and itens[0]["porta"] == "assistente"


def test_cada_porta_tem_o_proprio_rotulo_na_lista(limpo, monkeypatch):
    _finge(monkeypatch, leitura=_leitura())
    al.ler_bytes(limpo, CONTA, b"%PDF a", "A.pdf", origem="telegram", de="Cássio")
    al.ler_bytes(limpo, CONTA, b"%PDF b", "B.pdf", origem="whatsapp", de="Cássio")
    assert {i["porta"] for i in al.sem_mensagem(limpo, CONTA)} == {"Telegram", "assistente"}


def test_a_seguradora_achada_sem_layout_e_guardada(limpo, monkeypatch):
    """Sem isto a lista mostraria o nome do arquivo no lugar de 'Azul Seguros'."""
    _finge(monkeypatch, leitura=_leitura(reconhecida=False, seguradora=None,
                                         e_apolice=True,
                                         campos={"seguradora": "Azul Seguros",
                                                 "nome": "LUZIA AUREA"}))
    al.ler_bytes(limpo, CONTA, b"%PDF a", "A.pdf", origem="whatsapp", de="Cássio")
    itens = al.sem_mensagem(limpo, CONTA)
    assert itens[0]["seguradora"] == "Azul Seguros"
    assert "Azul Seguros" in al.resumo(itens[0])


# ────────── 6. a mesma apólice duas vezes (21/09/2026) ──────────
#
# Pedido do dono: "faz a checagem de duplicidade caso mande o mesma apolice nao
# salvar e avisar". O `mensagem_id` já impedia reler a mesma MENSAGEM, mas as três
# portas de conversa gravam `mensagem_id` nulo — reenviar o arquivo criava uma
# linha nova a cada vez. Ele mandou o PDF da Mapfre três vezes testando, e ficaram
# três pré-cadastros do mesmo documento na fila. Conferir em duplicata é o caminho
# mais curto pra cadastrar em duplicata.


def test_o_mesmo_arquivo_nao_vira_dois_pre_cadastros(limpo, monkeypatch):
    _finge(monkeypatch, leitura=_leitura())
    a = al.ler_bytes(limpo, CONTA, b"%PDF igual", "A.pdf", origem="whatsapp", de="Cássio")
    b = al.ler_bytes(limpo, CONTA, b"%PDF igual", "outro-nome.pdf",
                     origem="whatsapp", de="Cássio")
    assert a.get("repetida") is None and b.get("repetida")
    with limpo.connection() as c:
        assert c.execute("select count(*) from apolice_lida").fetchone()[0] == 1


def test_a_chave_e_o_conteudo_e_nao_o_nome(limpo, monkeypatch):
    """O assistente não recebe nome de arquivo do WhatsApp, e o corretor renomeia."""
    _finge(monkeypatch, leitura=_leitura())
    al.ler_bytes(limpo, CONTA, b"%PDF x", "apolice-recebida.pdf")
    r = al.ler_bytes(limpo, CONTA, b"%PDF x", "APOLICE MARIA 2026.pdf")
    assert r["repetida"]["onde"] == "fila"


def test_arquivo_diferente_continua_entrando(limpo, monkeypatch):
    _finge(monkeypatch, leitura=_leitura())
    al.ler_bytes(limpo, CONTA, b"%PDF um", "A.pdf")
    al.ler_bytes(limpo, CONTA, b"%PDF dois", "B.pdf")
    with limpo.connection() as c:
        assert c.execute("select count(*) from apolice_lida").fetchone()[0] == 2


def test_a_repetida_nao_sobe_nada_pro_cofre(limpo, monkeypatch):
    """Não é só não gravar: reenviar dez vezes não pode encher o cofre."""
    subiu = []
    _finge(monkeypatch, leitura=_leitura())
    monkeypatch.setattr(al, "_guardar",
                        lambda cid, b, n: (subiu.append(n), (f"apolice/{cid}/x.pdf", len(b)))[1])
    al.ler_bytes(limpo, CONTA, b"%PDF x", "A.pdf")
    al.ler_bytes(limpo, CONTA, b"%PDF x", "A.pdf")
    assert subiu == ["A.pdf"], "a segunda passada não toca no cofre"


def test_o_numero_da_apolice_pega_o_pdf_reemitido(limpo, monkeypatch):
    """O hash não pega: a seguradora reemite, os bytes mudam, a apólice é a mesma."""
    from datetime import date
    _finge(monkeypatch, leitura=_leitura(campos={"numero_apolice": "0531092835980"}))
    with limpo.connection() as c:
        c.execute("""insert into apolices (conta_id, seguradora, ramo, vigencia_fim,
                                           situacao, numero_apolice)
                     values (%s,'Porto Seguro','auto',date '2027-08-13','vigente',
                             '0531 09 2835980')""", (CONTA,))
        c.commit()
    r = al.ler_bytes(limpo, CONTA, b"%PDF reemitido", "A.pdf")
    assert r["repetida"]["onde"] == "carteira"
    with limpo.connection() as c:
        assert c.execute("select count(*) from apolice_lida").fetchone()[0] == 0


def test_o_numero_compara_so_os_digitos(limpo):
    """'0531 09 2835980' na carteira e '0531092835980' no papel são a mesma."""
    with limpo.connection() as c:
        c.execute("""insert into apolices (conta_id, seguradora, ramo, vigencia_fim,
                                           situacao, numero_apolice)
                     values (%s,'Porto Seguro','auto',date '2027-08-13','vigente',
                             '0531 09 2835980')""", (CONTA,))
        c.commit()
    assert al.ja_conheco(limpo, CONTA, "", "0531092835980")["onde"] == "carteira"
    assert al.ja_conheco(limpo, CONTA, "", "999") is None


def test_a_conta_vizinha_nao_e_afetada(limpo, monkeypatch):
    """A trava é por conta: duas corretoras podem ter o mesmo documento."""
    _finge(monkeypatch, leitura=_leitura())
    al.ler_bytes(limpo, CONTA, b"%PDF x", "A.pdf")
    r = al.ler_bytes(limpo, OUTRA, b"%PDF x", "A.pdf")
    assert r.get("repetida") is None


def test_a_porta_assume_a_repetida_em_vez_de_devolver_pro_caixa(limpo, monkeypatch):
    """Se `tomou` dissesse não, a apólice repetida viraria despesa no caixa."""
    _finge(monkeypatch, leitura=_leitura())
    al.ler_bytes(limpo, CONTA, b"%PDF x", "A.pdf")
    r = al.ler_bytes(limpo, CONTA, b"%PDF x", "A.pdf")
    assert al.tomou(r) is True


def test_o_recado_da_repetida_diz_onde_ela_esta():
    from datetime import datetime, timezone
    q = datetime(2026, 9, 21, 17, 8, tzinfo=timezone.utc)
    fila = al.aviso_de_repetida({"onde": "fila", "quando": q,
                                 "resumo": "Mapfre · MARIA · vence 10/09/2027"})
    assert "já li" in fila and "21/09" in fila and "Renovações" in fila
    carteira = al.aviso_de_repetida({"onde": "carteira", "quando": q, "resumo": "Porto · JOSE"})
    assert "já tenho cadastrada" in carteira and "Renovações" not in carteira


# ────────── 7. dá pra sair da fila sem cadastrar (21/09/2026) ──────────
#
# "quando abri e clico na apólice para aprovar não consigo sair caso não guarde, e
# como tem apólice repetida lá tenho que ver uma forma de resolver isso".
#
# A fila só esvaziava CADASTRANDO. Naquele dia a conta 37 tinha quatro linhas:
# três eram o mesmo PDF da Mapfre (11:08, 13:56, 14:39, lidas enquanto o leitor
# era consertado) e a quarta era a Porto lida antes do layout dela existir. Só uma
# prestava, e não havia como tirar as outras.


def test_descartado_some_da_fila(limpo, monkeypatch):
    _finge(monkeypatch, leitura=_leitura())
    r = al.ler_bytes(limpo, CONTA, b"%PDF a", "A.pdf", origem="whatsapp", de="Cássio")
    assert len(al.sem_mensagem(limpo, CONTA)) == 1
    assert al.descartar(limpo, CONTA, r["id"], membro_id=45) is True
    assert al.sem_mensagem(limpo, CONTA) == []


def test_descartar_nao_apaga(limpo, monkeypatch):
    """Regra 0: a linha fica, o PDF fica no cofre, a leitura fica auditável."""
    _finge(monkeypatch, leitura=_leitura())
    r = al.ler_bytes(limpo, CONTA, b"%PDF a", "A.pdf")
    al.descartar(limpo, CONTA, r["id"], membro_id=45)
    with limpo.connection() as c:
        linha = c.execute("""select pdf_caminho, descartado_por, descartado_em is not null
                               from apolice_lida where id=%s""", (r["id"],)).fetchone()
    assert linha[0] and linha[1] == 45 and linha[2] is True


def test_desfazer_traz_de_volta(limpo, monkeypatch):
    """Descartar é um toque, e errar também."""
    _finge(monkeypatch, leitura=_leitura())
    r = al.ler_bytes(limpo, CONTA, b"%PDF a", "A.pdf")
    al.descartar(limpo, CONTA, r["id"])
    assert al.voltar_da_lixeira(limpo, CONTA, r["id"]) is True
    assert len(al.sem_mensagem(limpo, CONTA)) == 1


def test_descartar_duas_vezes_nao_mente(limpo, monkeypatch):
    _finge(monkeypatch, leitura=_leitura())
    r = al.ler_bytes(limpo, CONTA, b"%PDF a", "A.pdf")
    assert al.descartar(limpo, CONTA, r["id"]) is True
    assert al.descartar(limpo, CONTA, r["id"]) is False


def test_nao_se_descarta_documento_de_outra_conta(limpo, monkeypatch):
    _finge(monkeypatch, leitura=_leitura())
    r = al.ler_bytes(limpo, CONTA, b"%PDF a", "A.pdf")
    assert al.descartar(limpo, OUTRA, r["id"]) is False
    assert len(al.sem_mensagem(limpo, CONTA)) == 1


def test_o_descartado_nao_bloqueia_o_reenvio(limpo, monkeypatch):
    """Descartei por engano e mandei de novo: a trava de duplicidade não pode
    transformar o descarte numa porta fechada."""
    _finge(monkeypatch, leitura=_leitura())
    r = al.ler_bytes(limpo, CONTA, b"%PDF a", "A.pdf")
    al.descartar(limpo, CONTA, r["id"])
    de_novo = al.ler_bytes(limpo, CONTA, b"%PDF a", "A.pdf")
    assert de_novo.get("repetida"), "ele avisa em vez de criar uma segunda linha"
    assert de_novo["repetida"]["onde"] == "fila"


def test_cadastrar_uma_tira_as_irmas_da_fila(limpo, monkeypatch):
    """O caso exato: três leituras do MESMO papel, cada uma com seu caminho no
    cofre. Casar por `pdf_caminho` tira só a confirmada e deixa duas convidando a
    cadastrar de novo."""
    _finge(monkeypatch, leitura=_leitura(campos={"numero_apolice": "0330433570731"}))
    a = al.ler_bytes(limpo, CONTA, b"%PDF um", "A.pdf")
    al.ler_bytes(limpo, CONTA, b"%PDF dois", "B.pdf")
    al.ler_bytes(limpo, CONTA, b"%PDF tres", "C.pdf")
    assert len(al.sem_mensagem(limpo, CONTA)) == 3
    assert al.descartar_irmas(limpo, CONTA, "0330 433 570731", menos=a["id"]) == 2
    resta = al.sem_mensagem(limpo, CONTA)
    assert [i["lida_id"] for i in resta] == [a["id"]]


def test_irmas_sem_numero_nao_varrem_a_fila(limpo, monkeypatch):
    """Número vazio casaria com toda leitura que também não tem número — e limparia
    a fila inteira ao cadastrar uma apólice sem número."""
    _finge(monkeypatch, leitura=_leitura(campos={}))
    al.ler_bytes(limpo, CONTA, b"%PDF um", "A.pdf")
    assert al.descartar_irmas(limpo, CONTA, "") == 0
    assert len(al.sem_mensagem(limpo, CONTA)) == 1


def test_irmas_de_outra_apolice_ficam(limpo, monkeypatch):
    _finge(monkeypatch, leitura=_leitura(campos={"numero_apolice": "111"}))
    al.ler_bytes(limpo, CONTA, b"%PDF um", "A.pdf")
    assert al.descartar_irmas(limpo, CONTA, "999") == 0
    assert len(al.sem_mensagem(limpo, CONTA)) == 1


# ────────── 8. reler com o leitor de hoje (22/09/2026) ──────────
#
# "de uma olhada na lista tem 2 da mapfre repetida, se não é a mesma apólice checa
# se é duplicada já apaga uma, e ver a questão do layout logo".
#
# Duas coisas no mesmo problema. As três linhas Mapfre da Liberal tinham a MESMA
# placa (PIR5077), o MESMO chassi e 11 páginas — a mesma apólice —, mas bytes
# diferentes (276.050 e 273.567): o hash não via. E o número também não, porque as
# duas primeiras foram lidas ANTES do layout da Mapfre existir e saíram sem número.
#
# O chassi é o que sobrevive aos dois casos. E reler é o que conserta a leitura
# velha sem pedir pro corretor reenviar nada: o documento certo já está no cofre.


def _no_cofre(monkeypatch, conteudo=b"%PDF guardado"):
    monkeypatch.setattr(al._cofre, "ler", lambda caminho: (conteudo, "application/pdf"))


def test_o_chassi_pega_o_reenvio_que_o_hash_nao_pega(limpo, monkeypatch):
    """Bytes diferentes, mesma apólice — o caso exato das duas Mapfre."""
    _finge(monkeypatch, leitura=_leitura(campos={"chassi": "9BFZH55L8J8038259"}))
    al.ler_bytes(limpo, CONTA, b"%PDF exportado de um jeito", "A.pdf")
    r = al.ler_bytes(limpo, CONTA, b"%PDF exportado de outro", "B.pdf")
    assert r["repetida"]["onde"] == "fila"
    with limpo.connection() as c:
        assert c.execute("select count(*) from apolice_lida").fetchone()[0] == 1


def test_chassi_curto_nao_vira_trava(limpo, monkeypatch):
    """Lixo de leitura casaria com lixo de leitura e prenderia a fila."""
    _finge(monkeypatch, leitura=_leitura(campos={"chassi": "ABC"}))
    al.ler_bytes(limpo, CONTA, b"%PDF um", "A.pdf")
    r = al.ler_bytes(limpo, CONTA, b"%PDF dois", "B.pdf")
    assert r.get("repetida") is None


def test_chassi_descartado_nao_bloqueia(limpo, monkeypatch):
    """Descartei e mandei de novo: o descarte não pode virar porta fechada."""
    _finge(monkeypatch, leitura=_leitura(campos={"chassi": "9BFZH55L8J8038259"}))
    r = al.ler_bytes(limpo, CONTA, b"%PDF um", "A.pdf")
    al.descartar(limpo, CONTA, r["id"])
    de_novo = al.ler_bytes(limpo, CONTA, b"%PDF dois", "B.pdf")
    assert de_novo.get("repetida") is None
    assert len(al.sem_mensagem(limpo, CONTA)) == 1


def test_reler_troca_a_leitura_velha_sem_criar_linha(limpo, monkeypatch):
    """O id é o mesmo e o caminho no cofre é o mesmo: a fila não ganha uma quarta
    cópia do que já estava lá."""
    from datetime import date
    _finge(monkeypatch, leitura=_leitura(reconhecida=False, seguradora=None,
                                         campos={"nome": "MAPFRE SEGUROS GERAIS S/A"}))
    r = al.ler_bytes(limpo, CONTA, b"%PDF x", "A.pdf")
    _no_cofre(monkeypatch)
    monkeypatch.setattr(al.apdf, "ler", lambda b, proibidos=(): _leitura(
        seguradora="Mapfre", campos={"nome": "SOLANGE MARIA LIMA MELO",
                                     "numero_apolice": "0330433570731",
                                     "vigencia_fim": date(2027, 9, 10)}))
    assert al.reler(limpo, CONTA, r["id"])["ok"] is True
    itens = al.sem_mensagem(limpo, CONTA)
    assert len(itens) == 1 and itens[0]["lida_id"] == r["id"]
    assert itens[0]["segurado"] == "SOLANGE MARIA LIMA MELO"
    assert itens[0]["vigencia_fim"] == date(2027, 9, 10)


def test_reler_atualiza_o_formulario_tambem(limpo, monkeypatch):
    """Sem isso a lista mostraria o novo e a conferência abriria com o velho."""
    from datetime import date
    _finge(monkeypatch, leitura=_leitura(campos={"nome": "ERRADO"}))
    r = al.ler_bytes(limpo, CONTA, b"%PDF x", "A.pdf")
    _no_cofre(monkeypatch)
    monkeypatch.setattr(al.apdf, "ler", lambda b, proibidos=(): _leitura(
        campos={"nome": "CERTO", "vigencia_fim": date(2027, 9, 10)}))
    al.reler(limpo, CONTA, r["id"])
    assert al.por_id(limpo, CONTA, r["id"])["form"]["nome"] == "CERTO"


def test_reler_sem_pdf_guardado_nao_estoura(limpo, monkeypatch):
    """A leitura que falhou não tem arquivo no cofre — e é justamente a que alguém
    vai querer reler."""
    _finge(monkeypatch, expira=True)
    _pdf(limpo, CONTA)
    al.ler_pendentes(limpo, CONTA)
    with limpo.connection() as c:
        lid = c.execute("select id from apolice_lida").fetchone()[0]
    r = al.reler(limpo, CONTA, lid)
    assert r["ok"] is False and "cofre" in r["erro"] or "PDF guardado" in r["erro"]


def test_reler_o_cofre_fora_do_ar_nao_apaga_a_leitura(limpo, monkeypatch):
    """Falhar relendo não pode deixar a linha pior do que estava."""
    _finge(monkeypatch, leitura=_leitura())
    r = al.ler_bytes(limpo, CONTA, b"%PDF x", "A.pdf")

    def explode(caminho):
        raise RuntimeError("cofre fora do ar")
    monkeypatch.setattr(al._cofre, "ler", explode)
    assert al.reler(limpo, CONTA, r["id"])["ok"] is False
    assert al.sem_mensagem(limpo, CONTA)[0]["seguradora"] == "Allianz"


def test_reler_a_fila_devolve_o_placar(limpo, monkeypatch):
    _finge(monkeypatch, leitura=_leitura())
    al.ler_bytes(limpo, CONTA, b"%PDF um", "A.pdf")
    al.ler_bytes(limpo, CONTA, b"%PDF dois", "B.pdf")
    _no_cofre(monkeypatch)
    assert al.reler_a_fila(limpo, CONTA) == {"relidas": 2, "falhas": 0}


def test_reler_nao_toca_no_que_foi_descartado(limpo, monkeypatch):
    _finge(monkeypatch, leitura=_leitura())
    r = al.ler_bytes(limpo, CONTA, b"%PDF um", "A.pdf")
    al.descartar(limpo, CONTA, r["id"])
    _no_cofre(monkeypatch)
    assert al.reler_a_fila(limpo, CONTA) == {"relidas": 0, "falhas": 0}
