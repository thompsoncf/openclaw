"""A ordem entre o sinal e a assinatura vira escolha da empresa (migração 194).

O QUE ACONTECEU
Em 02/09 um cliente da Prime quis LER o contrato antes de mandar a entrada. O
produto já permitia: o contrato nasce na aprovação do orçamento e, desde 01/09, o
sinal deixou de travar a assinatura — o comentário em `contrato_publico` é literal,
"O sinal deixou de ser porteiro em 01/09/2026".

Quem empurrava para a outra ordem era o FUNIL. O botão verde é um só por linha, e
`_ORDEM_ACAO` põe `sinal` na frente de `assinar`: com a data pré-reservada ele
mostra "Sinal recebido", e mandar o contrato antes virava um caminho que ninguém
via. (Até 01/09 nem existia — o e-mail do contrato só chegou ao menu no #601.)

O QUE ESTE ARQUIVO PRENDE
1. o parâmetro inverte o BOTÃO, e só ele — os selos não mudam, porque pendência
   continua sendo pendência nas duas ordens;
2. `comprovante` fica na frente das duas em ambas: papel de parcela já paga é
   dívida com o passado, não concorre com o próximo passo do negócio;
3. desligado, nada muda — é o default e é a ordem de hoje;
4. a folha do cliente muda o que PROMETE depois de assinar, sem mexer na 4.1;
5. o prazo da pré-reserva NÃO se move — decisão do dono em 03/09.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import contrato as ctr
from finance import vendas

CONTA = 34

_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table contrato_modelo (conta_id bigint primary key, clausulas jsonb not null
  default '[]'::jsonb, regras jsonb not null default '{}'::jsonb,
  atualizado_em timestamptz default now(), atualizado_por text default '',
  assinar_antes_do_sinal boolean not null default false);
"""


@pytest.fixture()
def pool():
    dbname = "zaq_assina_antes"
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
        c.execute("insert into contas (id, nome) values (%s,'Prime')", (CONTA,))
        c.commit()
    yield p
    p.close()


# a linha da Prime no dia do caso: aprovada, data segurada esperando o sinal, e o
# contrato já existe (nasce na aprovação) mas ainda não foi assinado.
def _linha(**over):
    base = dict(status="aprovada", enviado_em="20/08", nunca_enviada=False,
                tem_contrato=True, contrato_numero=5,
                data_estado={"estado": vendas.DATA_SEGURADA, "texto": "Data segurada",
                             "dica": "vence em 2 dias"})
    base.update(over)
    return vendas.linha_do_funil(**base)


def _acao(**over):
    a = _linha(**over)["acao"]
    return a["chave"] if a else None


# ═══════════════════ o botão verde ═══════════════════

def test_hoje_o_funil_pede_o_sinal_primeiro(pool):
    """O comportamento de sempre, que continua sendo o default."""
    assert _acao() == "sinal"


def test_ligado_o_funil_pede_a_assinatura_primeiro(pool):
    """O CASO DO CLIENTE DE 02/09: ele queria ler antes de pagar, e o funil só
    oferecia "Sinal recebido"."""
    assert _acao(assinar_antes_do_sinal=True) == "assinar"


def test_o_texto_do_botao_diz_qual_documento(pool):
    """O funil tem DOIS documentos correndo — orçamento e contrato. "Mandar" sem
    dizer qual deixa o vendedor sem saber o que está pendente."""
    a = _linha(assinar_antes_do_sinal=True)["acao"]
    assert "contrato" in a["texto"].lower()


def test_o_parametro_muda_o_botao_e_nao_os_selos(pool):
    """Some o BOTÃO, não o aviso — a mesma regra que já vale quando duas pendências
    concorrem. O sinal continua pendente e continua aparecendo."""
    normal, antes = _linha(), _linha(assinar_antes_do_sinal=True)
    assert [s["texto"] for s in normal["selos"]] == [s["texto"] for s in antes["selos"]]
    assert any("segurada" in s["texto"].lower() for s in antes["selos"])


def test_a_troca_e_posicional(pool):
    """A PROPRIEDADE QUE IMPEDE O PARÂMETRO DE CRESCER. Ele promete uma coisa —
    trocar quem vem primeiro entre o sinal e a assinatura. Reordenar mais alguma
    coisa junto seria uma segunda mudança de comportamento escondida na primeira.

    Primeira versão minha errava aqui: eu tinha subido o `comprovante` junto, e o
    teste pegou."""
    a, b = vendas._ORDEM_ACAO, vendas._ORDEM_ACAO_ASSINA_ANTES
    assert set(a) == set(b), "a fila ganhou ou perdeu uma ação"
    trocou = {a[i] for i in range(len(a)) if a[i] != b[i]}
    assert trocou == {"sinal", "assinar"}, f"mexeu em mais do que os dois: {trocou}"


def test_comprovante_continua_entre_os_dois_nas_duas_filas(pool):
    """Consequência da troca posicional, dita com o comportamento: papel de
    parcela paga vem depois do primeiro passo e antes do segundo, nas duas ordens."""
    pg = {"total": 2, "pagas": 1, "sem_comprovante": 1}
    assert _acao(pagamentos=pg) == "sinal"
    assert _acao(pagamentos=pg, assinar_antes_do_sinal=True) == "assinar"

    # sem o primeiro passo pendente, o comprovante é quem sobra nas duas
    reservada = {"estado": vendas.DATA_RESERVADA, "texto": "Data reservada", "dica": ""}
    assert _acao(pagamentos=pg, data_estado=reservada, contrato_assinado=True) == "comprovante"
    assert _acao(pagamentos=pg, data_estado=reservada, contrato_assinado=True,
                 assinar_antes_do_sinal=True) == "comprovante"


def test_data_fora_da_agenda_continua_na_frente_de_tudo(pool):
    """Trilho: inverter dois degraus não pode ter reordenado a fila inteira."""
    fora = {"estado": vendas.DATA_FORA, "dica": "sem data"}
    assert _acao(data_estado=fora) == "marcar"
    assert _acao(data_estado=fora, assinar_antes_do_sinal=True) == "marcar"


def test_assinado_e_pago_o_proximo_passo_e_fechar_nos_dois(pool):
    """Depois que as duas coisas aconteceram não há mais ordem a escolher.

    A data entra como RESERVADA de propósito: com o sinal pago ela já firmou, e
    deixá-la "segurada" descreveria um estado que produção não tem — o teste
    passaria medindo uma linha que não existe."""
    reservada = {"estado": vendas.DATA_RESERVADA, "texto": "Data reservada", "dica": ""}
    for antes in (False, True):
        assert _acao(contrato_assinado=True, sinal_pago=True, data_estado=reservada,
                     assinar_antes_do_sinal=antes) == "fechar"


def test_sem_contrato_no_nicho_a_ordem_nao_inventa_passo(pool):
    """Conta que não tem documento nenhum não pode ganhar um "assinar" do nada."""
    a = _acao(contrato_numero=None, tem_contrato=False, assinar_antes_do_sinal=True)
    assert a != "assinar"


# ═══════════════════ o parâmetro, no banco ═══════════════════

def test_conta_sem_modelo_fica_na_ordem_de_hoje(pool):
    assert ctr.assina_antes_do_sinal(pool, CONTA) is False


def test_o_dono_liga_e_desliga(pool):
    ctr.salvar_modelo(pool, CONTA, [{"titulo": "C1", "corpo": "x"}], {},
                      por="1", assinar_antes_do_sinal=True)
    assert ctr.assina_antes_do_sinal(pool, CONTA) is True

    ctr.salvar_modelo(pool, CONTA, [{"titulo": "C1", "corpo": "x"}], {},
                      por="1", assinar_antes_do_sinal=False)
    assert ctr.assina_antes_do_sinal(pool, CONTA) is False


def test_salvar_as_clausulas_nao_desliga_a_ordem_sem_querer(pool):
    """O default do corpo é False, então a TELA tem que mandar o valor atual
    sempre. Aqui isso é dito do jeito que dói: se alguém salvar o contrato
    passando o parâmetro, ele manda — e é por isso que a tela nunca omite."""
    ctr.salvar_modelo(pool, CONTA, [{"titulo": "C1", "corpo": "x"}], {},
                      por="1", assinar_antes_do_sinal=True)
    ctr.salvar_modelo(pool, CONTA, [{"titulo": "C2", "corpo": "y"}], {},
                      por="1", assinar_antes_do_sinal=True)
    assert ctr.assina_antes_do_sinal(pool, CONTA) is True


def test_o_parametro_sobrevive_ao_modelo_em_branco(pool):
    """Quem ligou a ordem nova e ainda não escreveu cláusula nenhuma continua com
    a ordem que escolheu — `carregar_modelo` devolve o padrão, não o parâmetro."""
    ctr.salvar_modelo(pool, CONTA, [], {}, por="1", assinar_antes_do_sinal=True)
    m = ctr.carregar_modelo(pool, CONTA)
    assert m["novo"] is True, "sem cláusulas, o modelo é o padrão"
    assert m["assinar_antes_do_sinal"] is True
    assert ctr.assina_antes_do_sinal(pool, CONTA) is True


def test_carregar_modelo_traz_a_ordem_pra_tela(pool):
    ctr.salvar_modelo(pool, CONTA, [{"titulo": "C1", "corpo": "x"}], {},
                      por="1", assinar_antes_do_sinal=True)
    assert ctr.carregar_modelo(pool, CONTA)["assinar_antes_do_sinal"] is True


def test_uma_conta_nao_muda_a_ordem_da_outra(pool):
    with pool.connection() as c:
        c.execute("insert into contas (id, nome) values (99,'Outra')")
        c.commit()
    ctr.salvar_modelo(pool, CONTA, [{"titulo": "C1", "corpo": "x"}], {},
                      por="1", assinar_antes_do_sinal=True)
    assert ctr.assina_antes_do_sinal(pool, 99) is False


def test_banco_fora_cai_na_ordem_de_hoje(pool):
    """FALHA FECHADA. Um parâmetro que não pôde ser lido não pode inverter a ordem
    em que a empresa cobra o cliente."""
    class _Explode:
        def connection(self):
            raise RuntimeError("sem banco")
    assert ctr.assina_antes_do_sinal(_Explode(), CONTA) is False
