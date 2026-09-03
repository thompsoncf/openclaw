"""O comprovante no bolso do vendedor.

O BURACO QUE ISTO FECHA
Quem recebe o PIX no WhatsApp é o vendedor, em campo, e desde 01/09 ele já confirma
o "Sinal recebido" pelo celular. Anexar o comprovante, não: era rota só do desktop
e gate de `financeiro` (dono e gestor). Ele fazia a parte difícil e deixava para
trás um selo coral "1 parcela sem comprovante" que ninguém em campo limpava.

A REGRA NOVA, decidida em 03/09: anexar não é mexer em dinheiro. Quem marca a
parcela como paga é o "Sinal recebido" — e isso o vendedor já fazia. Anexar é
juntar a prova do que ele mesmo registrou, e ele é quem tem o arquivo na mão.
Editar valor e dar baixa seguem com dono e gestor.

O QUE ESTE ARQUIVO PRENDE
1. o vendedor anexa, e anexa só na proposta DELE;
2. índice de parcela inventado não cria linha órfã — o comprovante do cliente não
   pode ir parar num canto que a tela não lê;
3. arquivo inválido/vazio é recusado ANTES de subir;
4. trocar o comprovante troca, não duplica.
"""
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import cockpit as ck

_SQL = """
create table nichos (id bigserial primary key, nome text, slug text unique, tipo text);
create table contas (id bigserial primary key, nome text, nicho_id bigint, chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table orcamentos (id bigserial primary key, conta_id bigint, cliente text,
  empresa text, itens jsonb, parcelas jsonb, setup_centavos bigint default 0,
  mensal_centavos bigint default 0, primeiro_ano_centavos bigint default 0,
  status text default 'rascunho', criado_por text, token text, numero int,
  modo text default 'evento', evento jsonb, sinal_centavos bigint,
  sinal_pago_em timestamptz, criado_em timestamptz default now());
create table orcamento_comprovantes (id bigserial primary key, conta_id bigint,
  orcamento_id bigint, parcela_idx int, caminho text, nome text, tipo text,
  bytes bigint, por text, criado_em timestamptz default now());
create unique index uq_orc_comp on orcamento_comprovantes (orcamento_id, parcela_idx);
"""

# um plano com entrada + uma parcela, como produção grava
_PLANO = json.dumps([
    {"obs": "Sinal — confirma a reserva da data", "venc": "2026-09-10",
     "forma": "Pix", "valor_centavos": 267000},
    {"obs": "Restante", "venc": "2026-12-01", "forma": "Pix",
     "valor_centavos": 623000}])

_PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


@pytest.fixture()
def pool():
    dbname = "zaq_ck_comprovante"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def cofre(monkeypatch):
    """O bucket, de mentira: guarda em memória e devolve o caminho.

    Sem isto o teste falaria com o Supabase de verdade. O que importa aqui é a
    REGRA (quem pode, qual parcela, o que é recusado), não o transporte."""
    from finance import comprovantes as cp
    guardado: dict[str, bytes] = {}

    def _subir(conteudo, content_type, *, conta_id, orcamento_id, parcela_idx):
        cp.validar(conteudo, content_type)          # a validação de verdade
        caminho = f"{conta_id}/{orcamento_id}/{parcela_idx}"
        guardado[caminho] = conteudo
        return caminho

    monkeypatch.setattr(cp, "configurado", lambda: True)
    monkeypatch.setattr(cp, "subir", _subir)
    monkeypatch.setattr(cp, "apagar", lambda caminho: guardado.pop(caminho, None))
    return guardado


@pytest.fixture()
def cena(pool):
    """Uma conta, dois vendedores, uma proposta de cada — com sinal pago."""
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome) values ('Prime') returning id").fetchone()[0]
        ana = c.execute("insert into membros (conta_id, nome, papel) values "
                        "(%s,'Ana','vendedor') returning id", (conta,)).fetchone()[0]
        bruno = c.execute("insert into membros (conta_id, nome, papel) values "
                          "(%s,'Bruno','vendedor') returning id", (conta,)).fetchone()[0]

        def prop(autor):
            return c.execute(
                """insert into orcamentos (conta_id, cliente, empresa, parcelas,
                     setup_centavos, status, criado_por, modo, sinal_pago_em)
                   values (%s,'Claudia','Claudia',%s::jsonb,890000,'aprovada',%s,'evento',
                     now()) returning id""",
                (conta, _PLANO, str(autor))).fetchone()[0]
        da_ana, do_bruno = prop(ana), prop(bruno)
        c.commit()
    return {"conta": conta, "ana": ana, "bruno": bruno,
            "da_ana": da_ana, "do_bruno": do_bruno}


def _anexos(pool, orc):
    with pool.connection() as c:
        return c.execute("select parcela_idx, nome, por from orcamento_comprovantes "
                         "where orcamento_id=%s order by parcela_idx", (orc,)).fetchall()


# ═════════════════════ a lista ═════════════════════

def test_vendedor_ve_o_plano_da_proposta_dele(pool, cena, cofre):
    d = ck.pagamentos(pool, cena["conta"], cena["da_ana"], membro_id=cena["ana"])
    assert d is not None
    assert [p["idx"] for p in d["parcelas"]] == [0, 1]
    assert d["parcelas"][0]["pago"] is True, "o sinal está pago e a tela precisa saber"
    assert d["pode_anexar"] is True


def test_vendedor_nao_ve_o_plano_da_proposta_de_outro(pool, cena, cofre):
    """O id vem da tela, e tela não é fonte confiável — é a mesma régua que
    `confirmar_sinal` e `fechar_contrato` já aplicam."""
    assert ck.pagamentos(pool, cena["conta"], cena["do_bruno"], membro_id=cena["ana"]) is None


def test_gestao_ve_a_de_todo_mundo(pool, cena, cofre):
    assert ck.pagamentos(pool, cena["conta"], cena["do_bruno"]) is not None


def test_sem_bucket_a_tela_diz_que_nao_da_pra_anexar(pool, cena, monkeypatch):
    """Botão que engole o comprovante do cliente é pior que botão nenhum."""
    from finance import comprovantes as cp
    monkeypatch.setattr(cp, "configurado", lambda: False)
    d = ck.pagamentos(pool, cena["conta"], cena["da_ana"], membro_id=cena["ana"])
    assert d["pode_anexar"] is False


# ═════════════════════ anexar ═════════════════════

def test_o_vendedor_anexa_o_comprovante(pool, cena, cofre):
    """O CASO DA MUDANÇA: antes de 03/09 isto era gate de `financeiro`."""
    r = ck.anexar_comprovante(pool, cena["conta"], cena["da_ana"], 0,
                              _PNG, "image/png", "pix.png", membro_id=cena["ana"])
    assert r["ok"] is True and r["trocou"] is False
    assert _anexos(pool, cena["da_ana"]) == [(0, "pix.png", str(cena["ana"]))]


def test_a_lista_passa_a_mostrar_o_comprovante(pool, cena, cofre):
    ck.anexar_comprovante(pool, cena["conta"], cena["da_ana"], 0,
                          _PNG, "image/png", "pix.png", membro_id=cena["ana"])
    d = ck.pagamentos(pool, cena["conta"], cena["da_ana"], membro_id=cena["ana"])
    assert d["parcelas"][0]["comprovante_id"] is not None
    assert d["parcelas"][0]["comprovante_nome"] == "pix.png"
    assert d["parcelas"][1]["comprovante_id"] is None


def test_trocar_troca_e_nao_duplica(pool, cena, cofre):
    """Dois papéis pra mesma parcela é pior que nenhum: na hora da discussão
    ninguém sabe qual vale."""
    ck.anexar_comprovante(pool, cena["conta"], cena["da_ana"], 0,
                          _PNG, "image/png", "velho.png", membro_id=cena["ana"])
    r = ck.anexar_comprovante(pool, cena["conta"], cena["da_ana"], 0,
                              _PNG, "image/png", "novo.png", membro_id=cena["ana"])
    assert r["trocou"] is True
    assert _anexos(pool, cena["da_ana"]) == [(0, "novo.png", str(cena["ana"]))]


def test_nao_anexa_na_proposta_de_outro_vendedor(pool, cena, cofre):
    r = ck.anexar_comprovante(pool, cena["conta"], cena["do_bruno"], 0,
                              _PNG, "image/png", "pix.png", membro_id=cena["ana"])
    assert r["ok"] is False
    assert _anexos(pool, cena["do_bruno"]) == [], "anexou na proposta de outro"


def test_parcela_inventada_nao_cria_linha_orfa(pool, cena, cofre):
    """Índice fora do plano criaria um anexo que a tela nunca mostra — o
    comprovante do cliente sumiria sem ninguém saber."""
    r = ck.anexar_comprovante(pool, cena["conta"], cena["da_ana"], 99,
                              _PNG, "image/png", "pix.png", membro_id=cena["ana"])
    assert r["ok"] is False and "parcela" in r["erro"].lower()
    assert _anexos(pool, cena["da_ana"]) == []


def test_arquivo_vazio_e_recusado(pool, cena, cofre):
    """Quem recusa é `comprovantes.validar`, e a mensagem dele vai direto pra tela."""
    r = ck.anexar_comprovante(pool, cena["conta"], cena["da_ana"], 0,
                              b"", "image/png", "vazio.png", membro_id=cena["ana"])
    assert r["ok"] is False and r["erro"] == "Arquivo vazio."
    assert cofre == {}, "subiu arquivo vazio pro bucket"


def test_tipo_que_nao_e_comprovante_e_recusado(pool, cena, cofre):
    """`validar` é a mesma do painel — executável não vira comprovante."""
    r = ck.anexar_comprovante(pool, cena["conta"], cena["da_ana"], 0,
                              b"MZ\x90\x00", "application/x-msdownload", "x.exe",
                              membro_id=cena["ana"])
    assert r["ok"] is False
    assert _anexos(pool, cena["da_ana"]) == []


def test_sem_bucket_diz_o_que_ha_de_errado(pool, cena, monkeypatch):
    """A MENSAGEM importa, não só o `ok=False`. Sem esta checagem o `subir` falha
    com erro de transporte e o vendedor lê "tente de novo" — para uma coisa que
    nunca vai funcionar por tentativa."""
    from finance import comprovantes as cp
    monkeypatch.setattr(cp, "configurado", lambda: False)
    r = ck.anexar_comprovante(pool, cena["conta"], cena["da_ana"], 0,
                              _PNG, "image/png", "pix.png", membro_id=cena["ana"])
    assert r["ok"] is False
    assert "não está configurado" in r["erro"]
    assert "Tente de novo" not in r["erro"], "mandou tentar de novo o que não adianta"
    assert _anexos(pool, cena["da_ana"]) == []


def test_gestao_anexa_em_qualquer_uma(pool, cena, cofre):
    """Trilho: abrir pro vendedor não pode ter fechado pra quem já podia."""
    r = ck.anexar_comprovante(pool, cena["conta"], cena["do_bruno"], 0,
                              _PNG, "image/png", "pix.png")
    assert r["ok"] is True
    assert _anexos(pool, cena["do_bruno"])[0][2] == "dono"


def test_proposta_de_outra_conta_nao_e_alcancada(pool, cena, cofre):
    r = ck.anexar_comprovante(pool, cena["conta"] + 999, cena["da_ana"], 0,
                              _PNG, "image/png", "pix.png")
    assert r["ok"] is False
    assert _anexos(pool, cena["da_ana"]) == []
