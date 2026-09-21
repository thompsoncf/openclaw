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
        monkeypatch.setattr(al.apdf, "ler", lambda b: leitura)


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
    monkeypatch.setattr(al.apdf, "ler", lambda b: (_ for _ in ()).throw(ValueError("PDF vazio")))
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
    monkeypatch.setattr(al.apdf, "ler", lambda b: (_ for _ in ()).throw(ValueError("PDF vazio")))
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
