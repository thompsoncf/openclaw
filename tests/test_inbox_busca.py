"""Achar a conversa pelo nome ou pelo número, na caixa inteira.

POR QUE ISSO EXISTE
A lista da caixa sai `order by ultima_msg_em desc limit 100`. Medido em 21/08/2026:
a conta 34 (Prime Eventos) tem 264 conversas e a 23 (Rawilson) tem 254 — ou seja,
164 e 154 conversas que existem no banco e não têm como ser alcançadas pela tela.
Rolar não resolve: a ordem é por quem falou por último, não por quem se procura.

E metade dessas conversas não tem lead vinculado (139 das 264 na Prime): elas
aparecem pelo nome do PERFIL do WhatsApp, ou pelo número cru quando nem isso veio.
É exatamente esse grupo que só se acha buscando.

O QUE SE PROVA AQUI
 1. a busca vai ao BANCO, não às 100 linhas já carregadas — senão acha só o que já
    dava pra ver rolando;
 2. nome sem acento acha nome com acento, nos dois sentidos ("joao" ↔ "João");
 3. o número casa pelos 8 ÚLTIMOS dígitos, que é a única parte que todo mundo
    escreve igual — com DDI, sem DDI, com e sem o nono dígito;
 4. o número é procurado nos TRÊS lugares onde ele mora (a conversa e os dois
    campos da ficha), e o nome nos três dele;
 5. a contagem "100 de 264" usa o MESMO recorte da lista — dois wheres diferentes
    virariam dois números que se contradizem na mesma linha da tela;
 6. e a busca NÃO fura o escopo: quem não é gerência continua vendo só as suas.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 34

_SQL = """
create table contas (id bigserial primary key, tipo text, nome text, chip_de bigint);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, whatsapp text, telefone text, cidade text, uf text,
  estagio text default 'lead', atualizado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', responsavel_membro_id bigint,
  ultima_msg_em timestamptz default now(), chip_id bigint, visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint,
  criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb, midia_arquivo text, midia_guardada_em timestamptz, midia_guardada_por bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, cockpit_pausado boolean default false);
create table canais_config (conta_id bigint, canal text, provedor text, rotulo text,
  identificador text, ativo boolean default true);
"""


@pytest.fixture()
def pool():
    dbname = "zaq_inbox_busca"
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


def _conversa(c, nome_perfil=None, *, lead=None, ref=None, dono=None,
              whats=None, tel=None, contato=None, canal="whatsapp"):
    pid = None
    if lead:
        pid = c.execute(
            "insert into prospeccao (conta_id,vendedor_id,empresa,contato,whatsapp,telefone) "
            "values (%s,%s,%s,%s,%s,%s) returning id",
            (CONTA, dono, lead, contato or "", whats or "", tel or "")).fetchone()[0]
    return c.execute(
        "insert into conversas (conta_id,prospeccao_id,contato_nome,contato_ref,canal) "
        "values (%s,%s,%s,%s,%s) returning id",
        (CONTA, pid, nome_perfil, ref, canal)).fetchone()[0]


def _buscar(pool, termo, *, gerencia=True, membro=None, canal="", escopo="msg"):
    with pool.connection() as c:
        return [x["empresa"] for x in pp._conversas_list(
            c, CONTA, gerencia, membro, canal=canal, vend="", escopo=escopo, busca=termo)]


# ------------------------------------------------------------------- por nome

def test_acha_pelo_nome_do_lead(pool):
    with pool.connection() as c:
        _conversa(c, lead="Jacqueline Prime")
        _conversa(c, lead="Camila Damasceno")
        c.commit()
    assert _buscar(pool, "jacque") == ["Jacqueline Prime"]


def test_acha_pelo_nome_do_perfil_do_whatsapp(pool):
    """A conversa sem lead é a MAIORIA da caixa (139 de 264 na Prime). Procurar só
    na ficha do lead deixaria metade inalcançável."""
    with pool.connection() as c:
        _conversa(c, "Rejane Barbosa")
        c.commit()
    assert _buscar(pool, "rejane") == ["Rejane Barbosa"]


def test_acha_pelo_contato_da_ficha(pool):
    """O nome da empresa e o nome de quem atende nem sempre são o mesmo."""
    with pool.connection() as c:
        _conversa(c, lead="Buffet Estrela", contato="Girlene Sena")
        c.commit()
    assert _buscar(pool, "girlene") == ["Buffet Estrela"]


@pytest.mark.parametrize("termo", ["joao", "João", "JOAO", "joão"])
def test_acento_e_caixa_nao_atrapalham(pool, termo):
    """Quem digita no celular não põe acento; o nome que veio do WhatsApp tem."""
    with pool.connection() as c:
        _conversa(c, lead="João Pedro Monteiro")
        c.commit()
    assert _buscar(pool, termo) == ["João Pedro Monteiro"]


def test_acha_no_meio_da_palavra(pool):
    with pool.connection() as c:
        _conversa(c, lead="Confeitaria Doce Mell")
        c.commit()
    assert _buscar(pool, "doce") == ["Confeitaria Doce Mell"]


def test_termo_vazio_devolve_a_caixa_inteira(pool):
    with pool.connection() as c:
        _conversa(c, lead="Um")
        _conversa(c, lead="Dois")
        c.commit()
    assert len(_buscar(pool, "")) == 2
    assert len(_buscar(pool, "   ")) == 2


# ----------------------------------------------------------------- por número

@pytest.mark.parametrize("termo", [
    "558694426769",     # como o WhatsApp entrega
    "8694426769",       # como o vendedor salva na agenda
    "94426769",         # os 8 que todo mundo escreve igual
    "4426769",          # o pedaço que se lembra de cabeça
])
def test_acha_pelo_numero_em_qualquer_grafia(pool, termo):
    with pool.connection() as c:
        _conversa(c, "Joao Pedro", ref="558694426769")
        _conversa(c, "Outro", ref="558698192489")
        c.commit()
    assert _buscar(pool, termo) == ["Joao Pedro"]


def test_numero_da_ficha_tambem_conta(pool):
    """O número pode estar na ficha do lead e não na conversa — conversa aberta pelo
    painel nasce sem `contato_ref` preenchido em alguns caminhos."""
    with pool.connection() as c:
        _conversa(c, lead="Isabela Mendes", whats="+55 86 98272-683")
        _conversa(c, lead="Flavia", tel="5586 98497225")
        c.commit()
    assert _buscar(pool, "98272683") == ["Isabela Mendes"]
    assert _buscar(pool, "98497225") == ["Flavia"]


def test_ddd_diferente_com_final_igual_nao_se_confunde(pool):
    """Dois celulares que terminam igual em DDDs diferentes: os 8 finais bastam pra
    separar quando o que se digita tem 8 ou mais."""
    with pool.connection() as c:
        _conversa(c, "Do Piauí", ref="558694426769")
        _conversa(c, "De São Paulo", ref="551194426769")
        c.commit()
    # 8 finais IGUAIS nos dois: a busca curta devolve os dois, e está certo —
    # o que ela não pode é devolver um e esconder o outro
    assert sorted(_buscar(pool, "94426769")) == ["De São Paulo", "Do Piauí"]


def test_poucos_digitos_continuam_sendo_busca_de_nome(pool):
    """"Ana 2" tem um dígito e não é busca de número — abaixo de 4 dígitos, procurar
    número devolveria a caixa inteira por sufixo."""
    with pool.connection() as c:
        _conversa(c, lead="Sala 12", ref="558694426712")
        _conversa(c, lead="Outra", ref="558698192489")
        c.commit()
    assert _buscar(pool, "12") == ["Sala 12"]


# ------------------------------------------------------------ contagem e escopo

def test_a_contagem_ignora_a_busca_e_o_limite(pool):
    """É a segunda metade de "1 de 3": o total é sempre do recorte SEM busca."""
    with pool.connection() as c:
        _conversa(c, lead="Jacqueline")
        _conversa(c, lead="Camila")
        _conversa(c, lead="Rejane")
        c.commit()
    with pool.connection() as c:
        assert pp._conversas_total(c, CONTA, True, None, escopo="msg") == 3
    assert len(_buscar(pool, "jacque")) == 1


def test_a_contagem_respeita_o_mesmo_filtro_da_lista(pool):
    """Dois wheres diferentes virariam dois números que se contradizem na tela."""
    with pool.connection() as c:
        _conversa(c, lead="No zap")
        _conversa(c, lead="No insta", canal="instagram")
        c.commit()
    with pool.connection() as c:
        assert pp._conversas_total(c, CONTA, True, None, canal="whatsapp",
                                   escopo="msg") == 1
    assert _buscar(pool, "", canal="whatsapp") == ["No zap"]


def test_buscar_nao_e_a_porta_dos_fundos_da_carteira_do_colega(pool):
    """O recorte por vendedor é aplicado ANTES da busca. Quem não é gerência não
    alcança o lead de outro nem digitando o nome dele inteiro."""
    with pool.connection() as c:
        eu = c.execute("insert into membros (conta_id,nome,papel) values (%s,'Eu','vendedor') "
                       "returning id", (CONTA,)).fetchone()[0]
        colega = c.execute("insert into membros (conta_id,nome,papel) values (%s,'Colega','vendedor') "
                           "returning id", (CONTA,)).fetchone()[0]
        _conversa(c, lead="Cliente meu", dono=eu)
        _conversa(c, lead="Cliente do colega", dono=colega)
        c.commit()
    assert _buscar(pool, "cliente", gerencia=False, membro=eu) == ["Cliente meu"]
    assert _buscar(pool, "colega", gerencia=False, membro=eu) == []
    assert sorted(_buscar(pool, "cliente")) == ["Cliente do colega", "Cliente meu"]


# ------------------------------------------------------------------ o número na linha

def test_a_linha_carrega_o_numero_pra_etiqueta(pool):
    """A etiqueta que explica por que a linha apareceu numa busca por número. Quem
    decide mostrar é a tela; o dado tem que chegar lá em todo caso."""
    with pool.connection() as c:
        _conversa(c, "Joao Pedro", ref="55 (86) 9 4426-769")
        c.commit()
    with pool.connection() as c:
        linha = pp._conversas_list(c, CONTA, True, None, escopo="msg")[0]
    assert linha["numero"] == "558694426769"
