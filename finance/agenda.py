"""Agenda PRÓPRIA do Zaq (sem OAuth). Eventos por CONTA, com lembrete opt-in.

Desenho:
- O horário que a pessoa fala é interpretado em HORÁRIO DE BRASÍLIA (UTC-3, sem
  horário de verão desde 2019). Guardamos timestamptz (aware).
- O agente (Claude) converte "amanhã 15h" -> uma data/hora concreta e chama a
  ferramenta; aqui a gente parseia formatos comuns com tolerância.
- "Adicionar ao Google/Apple/Outlook": link do Google Calendar (1 toque) e .ics
  (universal). A sincronização por feed .ics assinável vem na etapa 3.

Tudo escopado por conta_id (multi-tenant sagrado).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

_log = logging.getLogger(__name__)

# Brasília: UTC-3 fixo (o Brasil não usa mais horário de verão).
BRT = timezone(timedelta(hours=-3))


def frase_nomes(nomes: list[str]) -> str:
    """Junta nomes em português: 'Ana', 'Ana e Carlos', 'Ana, Carlos e Bia'."""
    nomes = [n for n in nomes if n]
    if not nomes:
        return ""
    if len(nomes) == 1:
        return nomes[0]
    return ", ".join(nomes[:-1]) + " e " + nomes[-1]


def agora_brt() -> datetime:
    return datetime.now(BRT)


def parse_datahora(s: str | None) -> datetime | None:
    """Converte texto de data/hora em datetime AWARE (Brasília). Tolerante.
    Aceita 'dd/mm/aaaa HH:MM', 'dd/mm HH:MM', ISO, com/sem hora e com/sem ano."""
    s = (s or "").strip().replace("T", " ")
    if not s:
        return None
    fmts = [
        ("%d/%m/%Y %H:%M", True, True), ("%d/%m/%y %H:%M", True, True),
        ("%d/%m %H:%M", False, True), ("%Y-%m-%d %H:%M", True, True),
        ("%d/%m/%Y %H", True, True), ("%d/%m %H", False, True),
        ("%d/%m/%Y", True, False), ("%d/%m", False, False), ("%Y-%m-%d", True, False),
    ]
    for fmt, tem_ano, tem_hora in fmts:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        hoje = agora_brt()
        if not tem_hora:
            dt = dt.replace(hour=9, minute=0)     # sem hora -> 09:00 (default)
        if not tem_ano:
            dt = dt.replace(year=hoje.year)
        dt = dt.replace(tzinfo=BRT)
        # sem ano e já passou neste ano -> rola pro ano que vem
        if not tem_ano and dt < hoje - timedelta(hours=1):
            dt = dt.replace(year=hoje.year + 1)
        return dt
    return None


def parse_data(data) -> date | None:
    """Data (sem hora): aceita date/datetime, ISO ('2025-11-18') ou 'dd/mm/aaaa'.
    Irmã tolerante da parse_datahora, pra quando só a data importa."""
    if isinstance(data, datetime):
        return data.date()
    if isinstance(data, date):
        return data
    s = str(data or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _minutos(hora: str | None) -> int | None:
    """Hora escrita como o cliente fala -> minutos desde a meia-noite.
    Aceita '19', '19:00', '19h', '19h30', '00:30'. 24:00 vale 1440 (meia-noite
    do dia seguinte) — é assim que orçamento de festa escreve 'encerramento 24'."""
    s = str(hora or "").strip().lower().replace("h", ":").rstrip(":")
    if not s:
        return None
    partes = s.split(":")
    try:
        h = int(partes[0])
        m = int(partes[1]) if len(partes) > 1 and partes[1] else 0
    except ValueError:
        return None
    if not (0 <= h <= 24 and 0 <= m < 60):
        return None
    return h * 60 + m


def janela_evento(data, inicio: str | None,
                  fim: str | None) -> tuple[datetime | None, datetime | None]:
    """(início, fim) AWARE de uma festa, a partir da data e dos horários do orçamento.

    A festa que "encerra às 24" acaba 00:00 DO DIA SEGUINTE — e virar a noite é a
    regra do ramo, não a exceção (19h→02h). Por isso, fim <= início rola pro dia
    seguinte: sem isso a agenda ganharia um compromisso terminando antes de
    começar. Sem data ou sem hora de início devolve (None, None) — quem chama
    trata como "não dá pra marcar ainda" e segue sem compromisso.
    """
    dia = parse_data(data)
    mi = _minutos(inicio)
    if dia is None or mi is None:
        return None, None
    ini = datetime(dia.year, dia.month, dia.day, tzinfo=BRT) + timedelta(minutes=mi)
    mf = _minutos(fim)
    if mf is None:
        return ini, None
    f = datetime(dia.year, dia.month, dia.day, tzinfo=BRT) + timedelta(minutes=mf)
    if f <= ini:
        f += timedelta(days=1)
    return ini, f


# Categorias do compromisso (pro portal colorir e separar pessoal/empresa).
TIPOS = ("pessoal", "empresa", "fornecedor")


def _fmt_evento(row) -> dict:
    return {"id": row[0], "titulo": row[1], "inicio": row[2], "fim": row[3],
            "local": row[4], "descricao": row[5], "lembrete_min": row[6],
            "criado_em": row[7] if len(row) > 7 else None,
            "tipo": (row[8] if len(row) > 8 else None) or "pessoal",
            "desfecho": row[9] if len(row) > 9 else None,
            "link_online": row[10] if len(row) > 10 else None,
            "status": (row[11] if len(row) > 11 else None) or "ativo",
            "pre_reserva_ate": row[12] if len(row) > 12 else None,
            "sinal_centavos": row[13] if len(row) > 13 else None,
            # quem marcou. Numa agenda de um dono só isso era ruído; agora que ela é
            # compartilhada (gestor e vendedor também veem), é a primeira pergunta de
            # quem olha um compromisso que não marcou. A coluna sempre existiu (098);
            # só não era lida.
            "membro_id": row[14] if len(row) > 14 else None,
            # nicho eventos (migração 179): que festa é, quanta gente, e se o
            # horário é palpite do sistema esperando confirmação.
            "tipo_evento": row[15] if len(row) > 15 else None,
            "convidados": row[16] if len(row) > 16 else None,
            "hora_sugerida": bool(row[17]) if len(row) > 17 else False}


_COLS = ("id, titulo, inicio, fim, local, descricao, lembrete_min, criado_em, tipo, "
        "desfecho, link_online, status, pre_reserva_ate, sinal_centavos, membro_id, "
        "tipo_evento, convidados, hora_sugerida")

# Data SEGURADA, ainda não vendida: o cliente aprovou o orçamento mas o sinal não
# entrou. Fica de fora de tudo que trata compromisso como certo — lembrete, resumo
# do dia, feed .ics, cockpit — porque essas consultas filtram `status='ativo'` e
# este status não é ativo. Só a tela da Agenda e as ações sobre ela abrem exceção.
PRE_RESERVADO = "pre_reservado"
# Quantos dias a data fica segurada quando a empresa não escolheu. Curto de
# propósito: prazo longo trava o calendário de quem vende data.
PRE_RESERVA_DIAS = 3
_ATIVO_OU_PRE = ("ativo", PRE_RESERVADO)


# ---------------------------------------------------------------------------
# O nome do cliente escondido dentro do título
# ---------------------------------------------------------------------------
#
# Até a migração 192 não havia campo de cliente no formulário, e o nome ia parar
# no texto do título. Medido na Prime em 31/08/2026: 51 dos 60 compromissos sem
# vínculo nenhum, e a esmagadora maioria com o nome ali, em "Locação — Fulano".
#
# Isto aqui LÊ esse nome pra mostrar; não grava nada. É palpite, e a tela mostra
# como palpite — o vínculo só nasce quando o dono confirma. Contra os 51 títulos
# reais: 36 nomes recuperados, 6 descartados por serem da equipe, 9 sem nome.

#: Onde o título separa o tipo do nome. O travessão é o que o sistema usa quando
#: monta sozinho ("Locação — Fulano"); o hífen cercado de espaço é como a equipe
#: digita na mão ("Formatura - Beatriz"). Hífen COLADO não conta: "Ana-Maria" é
#: um nome só.
_SEPARADORES = ("—", " - ", " – ")

#: Piso pra chamar de nome. Dois caracteres é sobra de digitação, não gente.
_MIN_NOME_TITULO = 3


def _sem_acento(s: str) -> str:
    t = unicodedata.normalize("NFKD", (s or "").strip())
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _palavras(s: str) -> set:
    """Palavras de um nome, partindo por QUALQUER não-letra.

    A barra importa: "PEDRO/JACQUE" são dois nomes, não um — e partindo só por
    espaço a dupla de vendedores passava batida pela checagem de equipe."""
    return set(re.findall(r"[a-zà-ÿ]+", _sem_acento(s)))


#: Ligações de nome português. Não identificam ninguém, então não contam como
#: "palavra de fora" na checagem de equipe — sem isto, "Pedro e Jacqueline"
#: escapava por causa do "e", e o nome de dois vendedores ia pra coluna Cliente.
_LIGACOES = frozenset(("e", "de", "da", "do", "das", "dos"))


def _e_da_equipe(nome: str, equipe) -> bool:
    """O nome extraído não acrescenta NENHUMA palavra que já não seja de alguém
    da equipe? Então é o vendedor, não o cliente.

    Por que a união de todos, e não membro a membro: metade das visitas da Prime
    se chama "VISITA TÉCNICA - PEDRO", e há "- PEDRO/JACQUE" — dois vendedores
    diferentes, que nenhum membro sozinho explica. Testando um de cada vez, esse
    par escapava e o nome do VENDEDOR ia parar na coluna Cliente. Isso é pior que
    deixar em branco, porque erra com confiança.

    E não pode ser cega ao contrário: basta UMA palavra de fora pra ser cliente
    de verdade. Na Prime isso preserva "Pedro Ribamar de Sousa Santos Júnior" e
    "Manoel Soares Vilanova Jr" — o dono chama-se MANOEL SOARES. Ligações ("e",
    "de", "da"...) não valem como palavra de fora: elas não identificam ninguém."""
    palavras = _palavras(nome) - _LIGACOES
    if not palavras:
        return False
    da_equipe = set()
    for m in (equipe or ()):
        da_equipe |= _palavras(m)
    if not da_equipe:
        return False
    return all(any(x == y or (len(x) >= 4 and y.startswith(x)) for y in da_equipe)
               for x in palavras)


def nome_no_titulo(titulo: str, tipo_evento: str | None = None,
                   equipe=()) -> str | None:
    """O nome do cliente lido do título, ou None quando não dá pra afirmar.

    `equipe` são os nomes dos membros da conta — sem eles a leitura devolveria o
    nome do vendedor nas visitas. Passe sempre; a lista vazia desliga a proteção.
    """
    t = (titulo or "").strip()
    if not t:
        return None
    cauda = None
    for sep in _SEPARADORES:
        if sep in t:
            cauda = t.split(sep, 1)[1].strip()
            break
    if cauda is None and tipo_evento:
        # "Locação Fulano", sem separador nenhum, mas com o tipo na frente
        tp = _sem_acento(tipo_evento)
        if tp and _sem_acento(t).startswith(tp + " "):
            cauda = t[len(tipo_evento):].strip()
    if not cauda or len(cauda) < _MIN_NOME_TITULO:
        return None
    if _e_da_equipe(cauda, equipe):
        return None
    return cauda


def criar_evento(pool, conta_id: int, titulo: str, inicio: datetime, *,
                 membro_id: int | None = None, fim: datetime | None = None,
                 local: str | None = None, descricao: str | None = None,
                 lembrete_min: int | None = None, tipo: str = "pessoal",
                 link_online: str | None = None,
                 prospeccao_id: int | None = None,
                 pre_reserva_ate: datetime | None = None,
                 sinal_centavos: int | None = None,
                 segurar: bool = False,
                 tipo_evento: str | None = None,
                 convidados: int | None = None,
                 hora_sugerida: bool = False,
                 cliente_id: int | None = None) -> dict:
    """prospeccao_id liga o evento a um lead (ex.: retorno de contato) — some da
    Agenda pro cliente, mas fica clicável a partir da ficha do lead (migração 136).

    `pre_reserva_ate` nasce o compromisso PROVISÓRIO: a data aparece na agenda mas
    não vale como compromisso até o sinal ser confirmado, e some sozinha se o prazo
    vencer (ver `expirar_pre_reservas`).

    `segurar` também nasce pré-reservado, mas SEM PRAZO CORRENDO. Existe porque
    prazo e pré-reserva não são a mesma coisa: uma negociação de casamento pra daqui
    a nove meses é data segurada e não tem prazo nenhum. Sem esta porta, a única
    forma de segurar uma data era dar um prazo — e `expirar_pre_reservas` cancelaria
    a negociação sozinha quando ele vencesse. Pôr prazo continua possível depois,
    numa data específica, quando o dono quiser apertar o cliente.

    `sinal_centavos` é o valor que se está esperando pra firmar — só pra MOSTRAR na
    agenda. O dinheiro (título a receber, baixa) sai de `orcamentos.sinal_centavos`;
    ver migração 163.

    `tipo_evento`, `convidados` e `hora_sugerida` são do nicho eventos (migração
    179) — que festa é, quanta gente, e se a hora foi chutada pelo sistema.

    `cliente_id` é DE QUEM é o compromisso (migração 192): a relação em `clientes`,
    não o lead. Até 31/08/2026 não havia onde guardar isso, e o nome do cliente
    vivia dentro do texto do título — onde ele é texto, não dado. `prospeccao_id`
    continua existindo e é outra coisa: quem chegou pelo WhatsApp e ainda está no
    funil. Um compromisso pode ter os dois, um, ou nenhum."""
    tipo = tipo if tipo in TIPOS else "pessoal"
    # prospeccao_id só entra no INSERT quando informado — mantém compatível com
    # bancos/testes que ainda não rodaram a migração 136 (que criou a coluna).
    colunas = "conta_id, membro_id, titulo, inicio, fim, local, descricao, lembrete_min, tipo, link_online"
    valores = [conta_id, membro_id, titulo.strip(), inicio, fim, local, descricao,
               lembrete_min, tipo, link_online]
    if prospeccao_id is not None:
        colunas += ", prospeccao_id"
        valores.append(prospeccao_id)
    if pre_reserva_ate is not None:
        colunas += ", status, pre_reserva_ate"
        valores += [PRE_RESERVADO, pre_reserva_ate]
    elif segurar:
        colunas += ", status"
        valores.append(PRE_RESERVADO)
    if sinal_centavos:
        colunas += ", sinal_centavos"
        valores.append(int(sinal_centavos))
    if tipo_evento:
        colunas += ", tipo_evento"
        valores.append(tipo_evento.strip())
    if convidados:
        colunas += ", convidados"
        valores.append(int(convidados))
    if hora_sugerida:
        colunas += ", hora_sugerida"
        valores.append(True)
    # condicional pelo mesmo motivo de `prospeccao_id` logo acima: banco que ainda
    # não rodou a 192 continua marcando compromisso. E `cliente_id` de propósito
    # NÃO entra em `_COLS`: acrescentar coluna ao SELECT é dependência dura da
    # migração, e dez arquivos de teste montam `eventos_agenda` na mão. Quem
    # precisa ler o vínculo (hoje só o relatório) faz o join na própria consulta.
    if cliente_id:
        colunas += ", cliente_id"
        valores.append(int(cliente_id))
    with pool.connection() as c:
        row = c.execute(
            f"""insert into eventos_agenda ({colunas})
               values ({','.join(['%s'] * len(valores))})
               returning """ + _COLS,
            valores,
        ).fetchone()
        c.commit()
    return _fmt_evento(row)


def ligar_cliente(pool, conta_id: int, evento_id: int,
                  cliente_id: int | None) -> bool:
    """Liga (ou desliga) o compromisso a um cadastro. Devolve True se mudou algo.

    Ligar SEMPRE limpa o `sem_cliente`: dizer quem é o dono responde a pergunta
    que o "não tem cliente" tinha silenciado, e deixar as duas marcas de pé faria
    a linha mentir dos dois jeitos ao mesmo tempo."""
    with pool.connection() as c:
        cur = c.execute(
            "update eventos_agenda set cliente_id=%s, sem_cliente=false "
            "where id=%s and conta_id=%s",
            (int(cliente_id) if cliente_id else None, evento_id, conta_id))
        c.commit()
        return cur.rowcount > 0


def marcar_sem_cliente(pool, conta_id: int, evento_id: int,
                       sem: bool = True) -> bool:
    """"Este compromisso não tem cliente" (migração 193). Não apaga vínculo que
    exista — só declara que não há um a procurar, e é isso que tira a linha da
    lista de pendências sem inventar dado."""
    with pool.connection() as c:
        cur = c.execute(
            "update eventos_agenda set sem_cliente=%s where id=%s and conta_id=%s",
            (bool(sem), evento_id, conta_id))
        c.commit()
        return cur.rowcount > 0


def evento_por_id(pool, conta_id: int, evento_id: int) -> dict | None:
    """Um evento vivo da conta — ativo OU pré-reservado (pra montar o card de
    compartilhar convites e pra agir sobre a data segurada)."""
    with pool.connection() as c:
        r = c.execute(
            "select " + _COLS + " from eventos_agenda "
            "where id=%s and conta_id=%s and status = any(%s)",
            (evento_id, conta_id, list(_ATIVO_OU_PRE))).fetchone()
    return _fmt_evento(r) if r else None


def evento_por_id_qualquer_status(pool, conta_id: int, evento_id: int) -> dict | None:
    """Igual evento_por_id, mas também acha CANCELADO — pra reaproveitar
    (remarcar) um compromisso que não aconteceu."""
    with pool.connection() as c:
        r = c.execute(
            "select " + _COLS + " from eventos_agenda where id=%s and conta_id=%s",
            (evento_id, conta_id)).fetchone()
    return _fmt_evento(r) if r else None


def listar_eventos(pool, conta_id: int, de: datetime, ate: datetime,
                   incluir_pre_reserva: bool = False) -> list[dict]:
    """Os COMPROMISSOS da janela. Pré-reserva fica de fora por padrão, e o padrão é
    o que importa: os lembretes, o resumo do dia e o aviso ao convidado saem daqui,
    e data segurada não pode tocar o telefone de ninguém — ela ainda não é
    compromisso de nada.

    `incluir_pre_reserva=True` é pra quem precisa enxergar a data OCUPADA e não o
    compromisso: o calendário do painel (senão o dono vende a mesma data duas
    vezes) e o "o que eu tenho essa semana" do assistente."""
    status = list(_ATIVO_OU_PRE) if incluir_pre_reserva else ["ativo"]
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            "where conta_id=%s and status = any(%s) and inicio >= %s and inicio < %s "
            "order by inicio",
            (conta_id, status, de, ate),
        ).fetchall()
    return [_fmt_evento(r) for r in rows]


def proximos(pool, conta_id: int, limite: int = 20, tipos=None) -> list[dict]:
    """O que vem por aí. `tipos` limita as categorias — é como a tela de quem vende
    data separa VISITA de FESTA: a festa tem card próprio (`confirmadas`) e, sem o
    filtro, ela ocuparia as oito linhas e a visita da semana não apareceria."""
    where = "conta_id=%s and status='ativo' and inicio >= %s"
    args = [conta_id, agora_brt() - timedelta(hours=2)]
    if tipos:
        where += " and tipo = any(%s)"
        args.append(list(tipos))
    args.append(limite)
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            f"where {where} order by inicio limit %s", args).fetchall()
    return [_fmt_evento(r) for r in rows]


def achar_por_titulo(pool, conta_id: int, termo: str) -> list[dict]:
    """Eventos FUTUROS cujo título casa com o termo (pra remarcar/cancelar por nome)."""
    termo = (termo or "").strip()
    if not termo:
        return []
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            "where conta_id=%s and status='ativo' and inicio >= %s and titulo ilike %s "
            "order by inicio",
            (conta_id, agora_brt() - timedelta(hours=2), f"%{termo}%"),
        ).fetchall()
    return [_fmt_evento(r) for r in rows]


def achar_por_titulo_passado(pool, conta_id: int, termo: str, agora: datetime,
                             dias: int = 30) -> list[dict]:
    """Eventos ATIVOS e JÁ PASSADOS (últimos `dias`) cujo título casa com o termo
    — pra marcar desfecho (aconteceu/não aconteceu) por nome, já que o compromisso
    some de achar_por_titulo (só olha o futuro) assim que a data passa."""
    termo = (termo or "").strip()
    if not termo:
        return []
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            "where conta_id=%s and status='ativo' and inicio < %s and inicio >= %s and titulo ilike %s "
            "order by inicio desc",
            (conta_id, agora, agora - timedelta(days=dias), f"%{termo}%"),
        ).fetchall()
    return [_fmt_evento(r) for r in rows]


def vocabulario_stt(pool, conta_id: int, limite_chars: int = 200) -> str:
    """Nomes que aparecem na agenda da conta (títulos dos próximos + convidados)
    pra dar de DICA pro transcritor de voz — ajuda a acertar 'Mailson', 'Smart
    Center' etc. Curto (o prompt do Whisper é limitado) e best-effort."""
    palavras: list[str] = []
    try:
        for ev in proximos(pool, conta_id, limite=20):
            palavras.append(ev["titulo"])
    except Exception:  # noqa: BLE001
        pass
    try:
        with pool.connection() as c:
            rows = c.execute(
                "select distinct nome from evento_convidados "
                "where conta_id=%s and nome is not null limit 40", (conta_id,)).fetchall()
        palavras += [r[0] for r in rows]
    except Exception:  # noqa: BLE001
        pass
    vistos: set[str] = set()
    out: list[str] = []
    for p in palavras:
        p = (p or "").strip()
        k = p.lower()
        if p and k not in vistos:
            vistos.add(k)
            out.append(p)
    return ", ".join(out)[:limite_chars]


def _sem_acento(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")


def sugerir_por_titulo(pool, conta_id: int, termo: str, limite: int = 4) -> list[dict]:
    """Eventos futuros ORDENADOS por semelhança com o termo — pra SUGERIR quando
    não bate exato (a voz costuma trocar/mangar o nome). Combina sobreposição de
    palavras (peso maior) com similaridade de string (difflib)."""
    import difflib
    evs = proximos(pool, conta_id, limite=100)
    if not evs:
        return []
    alvo = _sem_acento(termo)
    if not alvo:
        return evs[:limite]
    palavras = {p for p in alvo.split() if len(p) >= 3}

    def score(ev: dict) -> float:
        t = _sem_acento(ev["titulo"])
        ratio = difflib.SequenceMatcher(None, alvo, t).ratio()
        overlap = len(palavras & {p for p in t.split() if len(p) >= 3})
        return overlap * 2.0 + ratio

    ranked = sorted(evs, key=score, reverse=True)
    bons = [e for e in ranked if score(e) >= 0.34]     # tem ALGUMA semelhança
    return (bons or ranked)[:limite]


def remarcar_evento(pool, conta_id: int, evento_id: int, inicio: datetime,
                    fim: datetime | None = None) -> bool:
    """Muda a data, PRESERVANDO o que o compromisso é. Limpa o desfecho — remarcar
    significa 'isso vai acontecer nessa data', não importa o que era antes.

    A DATA SEGURADA CONTINUA SEGURADA, e isto é um conserto.

    A versão anterior forçava `status='ativo'` sem exceção, e estava certa quando foi
    escrita: o mundo era ativo × cancelado, e reativar era a intenção. A pré-reserva
    chegou depois (migração 160) e esta função nunca foi revisitada — então mudar a
    data de uma negociação a transformava em RESERVA FIRME, calada. Reproduzido em
    21/08/2026: uma pré-reserva de R$ 6.000 cujo sinal ninguém pagou passava a
    parecer vendida só por mudar de sábado.

    E com prazo ficava pior: `ativo` com `pre_reserva_ate` pendurado, que
    `expirar_pre_reservas` nunca recolhe porque ela só olha `pre_reservado`. A data
    ficava num estado que nenhuma parte do sistema sabia ler.

    Agora só o CANCELADO é reativado, que era o ponto do 'ativo' original — é este o
    caminho de reaproveitar um compromisso que não aconteceu."""
    with pool.connection() as c:
        cur = c.execute(
            "update eventos_agenda "
            "   set inicio=%s, fim=%s, desfecho=null, "
            # case, e não um 'ativo' cru: pré-reserva continua pré-reserva (com o
            # prazo intacto), ativo continua ativo, e cancelado volta à vida.
            "       status = case when status='cancelado' then 'ativo' else status end "
            " where id=%s and conta_id=%s",
            (inicio, fim, evento_id, conta_id),
        )
        c.commit()
        return cur.rowcount > 0


def cancelar_evento(pool, conta_id: int, evento_id: int) -> bool:
    """Cancela um compromisso — inclusive uma data só PRÉ-RESERVADA, que é o
    caminho de desistir da reserva antes do sinal."""
    with pool.connection() as c:
        cur = c.execute(
            "update eventos_agenda set status='cancelado' "
            "where id=%s and conta_id=%s and status = any(%s)",
            (evento_id, conta_id, list(_ATIVO_OU_PRE)),
        )
        c.commit()
        return cur.rowcount > 0


def confirmar_pre_reserva(pool, conta_id: int, evento_id: int) -> bool:
    """O sinal caiu: a data segurada vira compromisso de verdade.

    Idempotente por natureza — só casa quem ainda está `pre_reservado`, então
    apertar duas vezes não faz nada na segunda."""
    with pool.connection() as c:
        cur = c.execute(
            "update eventos_agenda set status='ativo', pre_reserva_ate=null "
            "where id=%s and conta_id=%s and status=%s",
            (evento_id, conta_id, PRE_RESERVADO))
        c.commit()
        return cur.rowcount > 0


def expirar_pre_reservas(pool, agora: datetime) -> list[dict]:
    """Libera as datas cujo prazo venceu sem o sinal. Devolve o que expirou, pra
    quem chamou avisar o dono.

    Não apaga: vira `cancelado`, com o motivo na descrição. A data fica livre mas o
    histórico continua, e dá pra reabrir a pré-reserva num clique se o cliente
    aparecer depois."""
    with pool.connection() as c:
        rows = c.execute(
            "update eventos_agenda set status='cancelado' "
            " where status=%s and pre_reserva_ate is not null and pre_reserva_ate <= %s "
            "returning id, conta_id, titulo, inicio, pre_reserva_ate",
            (PRE_RESERVADO, agora)).fetchall()
        c.commit()
    return [{"id": r[0], "conta_id": r[1], "titulo": r[2], "inicio": r[3],
             "pre_reserva_ate": r[4]} for r in rows]


def liberar_pre_reserva(pool, conta_id: int, evento_id: int) -> bool:
    """Solta a data ANTES do prazo, porque o dono decidiu — o cliente desistiu, ou
    apareceu quem paga hoje.

    Só casa `pre_reservado`: um botão mal ligado não pode cancelar compromisso
    firme. Idempotente pelo mesmo motivo (apertar duas vezes não faz nada na 2ª)."""
    with pool.connection() as c:
        cur = c.execute(
            "update eventos_agenda set status='cancelado' "
            "where id=%s and conta_id=%s and status=%s",
            (evento_id, conta_id, PRE_RESERVADO))
        c.commit()
        return cur.rowcount > 0


def pre_reservas(pool, conta_id: int, limite: int = 12) -> list[dict]:
    """As datas SEGURADAS da conta, da que vence primeiro pra que vence por último.

    Existe porque `proximos()` não pode mostrá-las (ele é a fonte do lembrete e do
    resumo do dia, e data segurada não é compromisso) — e sem uma lista própria,
    uma data que vence amanhã só aparecia pra quem abrisse o mês certo e reparasse
    na linha.

    Traz junto o orçamento de origem, quando há: é o que liga o botão "Ver
    orçamento" e o valor do sinal que está sendo esperado. Pré-reserva criada na
    própria agenda (telefonema "segura o dia 20") não tem orçamento.

    A busca do orçamento é uma consulta SEPARADA, e tolerante: a agenda funciona
    em conta que nem tem o módulo de orçamentos, e um join obrigatório faria a
    tela inteira quebrar por causa de um enfeite."""
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            " where conta_id=%s and status=%s "
            " order by pre_reserva_ate nulls last, inicio limit %s",
            (conta_id, PRE_RESERVADO, limite)).fetchall()
    eventos = [_fmt_evento(r) for r in rows]
    return _com_orcamento(pool, conta_id, eventos)


def confirmadas(pool, conta_id: int, agora=None, limite: int = 12) -> list[dict]:
    """As datas FIRMES daqui pra frente, da mais próxima pra mais distante.

    A irmã de `pre_reservas`. Uma diz o que está por um fio; esta diz o que já é
    da casa — e até 19/08/2026 não existia em lugar nenhum da tela: quem quisesse
    saber quantas datas tinha vendidas contava no calendário, mês a mês.

    Só compromisso de EMPRESA: aniversário do sócio e reunião com fornecedor são
    agenda, não venda de data, e misturados no mesmo contador o número deixa de
    querer dizer alguma coisa.

    Corta no AGORA, não no início do dia: a festa de ontem não é data confirmada,
    é festa que já aconteceu — e sai da lista sozinha, sem ninguém arquivar nada."""
    agora = agora or agora_brt()
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            " where conta_id=%s and status='ativo' and tipo='empresa' and inicio >= %s "
            " order by inicio limit %s",
            (conta_id, agora, limite)).fetchall()
    return _com_orcamento(pool, conta_id, [_fmt_evento(r) for r in rows])


def _com_orcamento(pool, conta_id: int, eventos: list[dict]) -> list[dict]:
    """Cola o orçamento de origem em cada evento, numa CONSULTA SÓ.

    Era uma por evento — e cada uma pegava conexão nova do pool. Numa agenda com
    12 datas seguradas isso somava 12 idas ao banco a cada carregamento da tela,
    e a tela recarrega a cada clique nos nomes e nas setas do mês. Quanto melhor
    o mês da empresa, mais lenta ficava a Agenda: o pior jeito possível de uma
    lentidão crescer.

    Tolerante de propósito: a agenda funciona em conta que nem tem o módulo de
    orçamentos, e um erro aqui não pode derrubar a tela por causa de um enfeite.
    Compromisso marcado na própria agenda ("segura o dia 20", por telefone) não
    tem orçamento e simplesmente não ganha as chaves."""
    ids = [e["id"] for e in eventos]
    if not ids:
        return eventos
    try:
        with pool.connection() as c:
            rows = c.execute(
                "select evento_agenda_id, id, numero, sinal_centavos, "
                "       coalesce(primeiro_ano_centavos, setup_centavos, 0) "
                "  from orcamentos "
                " where conta_id=%s and evento_agenda_id = any(%s)",
                (conta_id, ids)).fetchall()
    except Exception:  # noqa: BLE001 — conta/instalação sem o módulo de orçamentos
        return eventos
    por_evento = {r[0]: r for r in rows}
    for ev in eventos:
        r = por_evento.get(ev["id"])
        if not r:
            continue
        ev["orcamento_id"] = r[1]
        ev["orcamento_numero"] = r[2]
        if r[3]:
            ev["sinal_centavos"] = r[3]
        if r[4]:
            ev["total_centavos"] = r[4]
    return eventos


def orcamento_do_evento(pool, conta_id: int, evento_id: int) -> int | None:
    """O orçamento que originou este compromisso, se houver. Escopado por conta.

    Um evento só — passa pelo mesmo `_com_orcamento` das listas de propósito: duas
    leituras do vínculo evento↔orçamento seriam duas chances de discordarem."""
    achados = _com_orcamento(pool, conta_id, [{"id": evento_id}])
    return achados[0].get("orcamento_id")


def conflitos(pool, conta_id: int, inicio: datetime, fim: datetime | None,
              ignorar_id: int | None = None) -> list[dict]:
    """Compromissos da conta que se sobrepõem à janela — ativos E pré-reservados.

    Não existia checagem nenhuma: dois orçamentos aprovados pra mesma data criavam
    dois compromissos e ninguém era avisado. Pra buffet/espaço isso é vender a
    mesma data duas vezes. Quem chama decide o que fazer — aqui só se informa.

    Compromisso sem `fim` conta como 1h — a mesma convenção que o .ics já usa
    (`_fim_ou_1h`). Fosse duração zero, um evento começando na hora exata do outro
    não apareceria como conflito, que é justamente o choque mais provável."""
    fim = fim or (inicio + timedelta(hours=1))
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            " where conta_id=%s and status = any(%s) and (%s::bigint is null or id <> %s) "
            # sobreposição clássica: começa antes do outro acabar e acaba depois de
            # o outro começar.
            "   and inicio < %s and coalesce(fim, inicio + interval '1 hour') > %s "
            " order by inicio",
            (conta_id, list(_ATIVO_OU_PRE), ignorar_id, ignorar_id, fim, inicio)).fetchall()
    return [_fmt_evento(r) for r in rows]


def choques_de_data(pool, conta_id: int, de: datetime | None = None) -> list[dict]:
    """Dias que têm MAIS DE UM compromisso — no nicho eventos, isso já é o alerta.

    Não é o mesmo que `conflitos()`, e a diferença não é detalhe. `conflitos()`
    compara HORÁRIOS: dois compromissos só se chocam se um começa antes de o outro
    acabar. Pra reunião isso está certo; pra quem aluga espaço, não. Uma locação às
    17:00 e outra às 20:00 no mesmo sábado não se sobrepõem por hora nenhuma — e são
    duas festas no mesmo salão.

    Foi exatamente o caso que expôs isto: 10/07/2027 na Prime Eventos tem o Allef
    fixado às 17:00 e a Márcia em negociação às 20:00. Pelo cálculo de horário
    (17–18h contra 20–21h) não há choque nenhum, e o dia passaria limpo.

    Conta pré-reserva junto de propósito: é justamente a data segurada que ninguém
    lembra que está ocupada. Só o futuro — dia que já passou não tem o que resolver.

    O fuso é `interval '-03:00'`, e não a string 'UTC-03': em nome POSIX o sinal é
    INVERTIDO, então 'UTC-03' significa UTC+3 e uma festa das 22:30 cairia no dia
    seguinte — que é exatamente o erro que faria duas festas do mesmo sábado
    parecerem dias diferentes.

    Devolve um item por DIA, com os compromissos daquele dia:
        [{"dia": date, "eventos": [ev, ev, ...]}]
    """
    de = de or agora_brt()
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            " where conta_id=%s and status = any(%s) and inicio >= %s "
            "   and (inicio at time zone interval '-03:00')::date in ("
            "        select (inicio at time zone interval '-03:00')::date"
            "          from eventos_agenda"
            "         where conta_id=%s and status = any(%s) and inicio >= %s"
            "         group by 1 having count(*) > 1)"
            " order by inicio",
            (conta_id, list(_ATIVO_OU_PRE), de,
             conta_id, list(_ATIVO_OU_PRE), de)).fetchall()
    por_dia: dict = {}
    for r in rows:
        ev = _fmt_evento(r)
        por_dia.setdefault(ev["inicio"].astimezone(BRT).date(), []).append(ev)
    return [{"dia": d, "eventos": evs} for d, evs in sorted(por_dia.items())]


def horas_a_conferir(pool, conta_id: int, de: datetime | None = None) -> list[dict]:
    """Compromissos futuros cujo horário o SISTEMA chutou e ninguém confirmou.

    Existe pra que um palpite nunca fique com a mesma cara de um dado escolhido.
    Enquanto a linha estiver aqui, a tela mostra o horário marcado; quando alguém
    corrige o horário, `hora_sugerida` vira false e a linha some sozinha."""
    de = de or agora_brt()
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            " where conta_id=%s and status = any(%s) and inicio >= %s and hora_sugerida "
            " order by inicio",
            (conta_id, list(_ATIVO_OU_PRE), de)).fetchall()
    return [_fmt_evento(r) for r in rows]


def sem_vendedor(pool, conta_id: int, de: datetime | None = None) -> list[dict]:
    """Compromissos futuros que não têm ninguém responsável.

    Numa agenda de dono só isso não importava. Numa agenda de equipe, uma festa
    marcada sem dono é uma festa que ninguém está tocando — e o caso que trouxe
    isto à tona foi uma vendedora que saiu da empresa deixando seis datas para
    trás, uma delas uma negociação ainda aberta."""
    de = de or agora_brt()
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            " where conta_id=%s and status = any(%s) and inicio >= %s and membro_id is null "
            " order by inicio",
            (conta_id, list(_ATIVO_OU_PRE), de)).fetchall()
    return [_fmt_evento(r) for r in rows]


def pendencias(pool, conta_id: int, de: datetime | None = None) -> dict:
    """Tudo que a agenda tem pra alguém conferir, num lugar só.

    É a fonte do card de alerta no topo da tela. Some sozinho quando zera — por
    isso `total` existe: quem desenha não precisa somar três listas pra saber se
    mostra o card.

    Tolerante de propósito: se qualquer uma das três consultas falhar (banco sem a
    migração 179, por exemplo), ela devolve vazio e as outras continuam. Um card de
    aviso não pode derrubar a agenda inteira — a agenda é o que a pessoa veio ver;
    o aviso é enfeite útil."""
    de = de or agora_brt()
    out: dict = {"choques": [], "horas": [], "sem_vendedor": []}
    for chave, fn in (("choques", choques_de_data), ("horas", horas_a_conferir),
                      ("sem_vendedor", sem_vendedor)):
        try:
            out[chave] = fn(pool, conta_id, de)
        except Exception as e:  # noqa: BLE001 — aviso não derruba a tela
            _log.warning("pendencias.%s conta=%s: %s", chave, conta_id, e)
    out["total"] = len(out["choques"]) + len(out["horas"]) + len(out["sem_vendedor"])
    return out


_DESFECHOS = ("realizado", "nao_realizado")


def marcar_desfecho(pool, conta_id: int, evento_id: int, desfecho: str, agora: datetime) -> bool:
    """Marca se um compromisso já passado aconteceu ou não — só pra eventos
    ATIVOS cuja data já chegou (não faz sentido marcar o futuro nem um
    cancelado, que já é sabidamente 'não aconteceu')."""
    if desfecho not in _DESFECHOS:
        return False
    with pool.connection() as c:
        cur = c.execute(
            "update eventos_agenda set desfecho=%s "
            "where id=%s and conta_id=%s and status='ativo' and inicio <= %s",
            (desfecho, evento_id, conta_id, agora),
        )
        c.commit()
        return cur.rowcount > 0


def eventos_para_reaproveitar(pool, conta_id: int, agora: datetime, limite: int = 6) -> list[dict]:
    """Compromissos recentes (últimos 90 dias) marcados como NÃO REALIZADOS —
    candidatos a reaproveitar em vez de recriar do zero. Cancelado fica de fora
    de propósito: quem cancela já decidiu que não tem previsão de acontecer, e
    listar todo cancelado lotaria a lista de sugestão à toa — só "não rolou"
    (ficou sem confirmação de que não vai mais acontecer) é sugestão útil aqui.
    Mais recentes primeiro."""
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + ", (select count(*) from evento_convidados ec "
            "where ec.evento_id = eventos_agenda.id) as n_convidados "
            "from eventos_agenda "
            "where conta_id=%s and inicio >= %s and inicio <= %s and desfecho='nao_realizado' "
            "order by inicio desc limit %s",
            (conta_id, agora - timedelta(days=90), agora, limite),
        ).fetchall()
    out = []
    for r in rows:
        ev = _fmt_evento(r[:-1])
        ev["n_convidados"] = r[-1]
        out.append(ev)
    return out


# ---------- "adicionar ao calendário": Google (link) e .ics (universal) ----------

def _utc(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fim_ou_1h(ev: dict) -> datetime:
    return ev.get("fim") or (ev["inicio"] + timedelta(hours=1))


def link_google(ev: dict) -> str:
    """URL do Google Calendar que adiciona o evento em 1 toque (sem login)."""
    from urllib.parse import quote
    p = (f"action=TEMPLATE&text={quote(ev['titulo'])}"
         f"&dates={_utc(ev['inicio'])}/{_utc(_fim_ou_1h(ev))}")
    if ev.get("local"):
        p += f"&location={quote(ev['local'])}"
    if ev.get("descricao"):
        p += f"&details={quote(ev['descricao'])}"
    return "https://calendar.google.com/calendar/render?" + p


LOCAL_ONLINE = "Online"


def eh_online(local: str | None) -> bool:
    """True quando o local marcado foi o botão "reunião online" do formulário
    (ou alguém digitou exatamente isso à mão) — não tem endereço de verdade, então
    ninguém deve receber link de mapa pra ele."""
    return (local or "").strip().lower() == LOCAL_ONLINE.lower()


def link_maps(local: str | None) -> str | None:
    """URL do Google Maps pro local do evento (texto livre — endereço, nome do
    lugar etc.). None se não tiver local — ou se for reunião online, que não tem
    endereço nenhum pra mostrar no mapa — pra quem for montar mensagem pular a
    linha inteira em vez de mandar um link vazio ou sem sentido."""
    local = (local or "").strip()
    if not local or eh_online(local):
        return None
    from urllib.parse import quote
    return "https://www.google.com/maps/search/?api=1&query=" + quote(local)


def _ics_escape(t: str) -> str:
    return (str(t or "").replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def evento_para_ics(ev: dict) -> str:
    linhas = ["BEGIN:VEVENT", f"UID:zaq-{ev['id']}@zaq-ia.com",
              f"DTSTAMP:{_utc(ev.get('criado_em') or ev['inicio'])}",
              f"DTSTART:{_utc(ev['inicio'])}", f"DTEND:{_utc(_fim_ou_1h(ev))}",
              f"SUMMARY:{_ics_escape(ev['titulo'])}"]
    if ev.get("local"):
        linhas.append(f"LOCATION:{_ics_escape(ev['local'])}")
    if ev.get("descricao"):
        linhas.append(f"DESCRIPTION:{_ics_escape(ev['descricao'])}")
    linhas.append("END:VEVENT")
    return "\r\n".join(linhas)


def feed_ics(eventos: list[dict]) -> str:
    """Calendário .ics completo (assinável) — Google/Apple/Outlook leem."""
    cab = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Zaq//Agenda//PT",
           "CALSCALE:GREGORIAN", "X-WR-CALNAME:Zaq"]
    corpo = [evento_para_ics(e) for e in eventos]
    return "\r\n".join(cab + corpo + ["END:VCALENDAR"]) + "\r\n"


def fmt_hora(ev: dict) -> str:
    """dd/mm HH:MM em Brasília, pra mostrar pro usuário."""
    return ev["inicio"].astimezone(BRT).strftime("%d/%m %H:%M")


# ---------- calendário do mês (pro portal) ----------

def eventos_mes(pool, conta_id: int, ano: int, mes: int) -> list[dict]:
    """Todos os eventos ativos que caem no mês (Brasília). Pra pintar o calendário."""
    de = datetime(ano, mes, 1, tzinfo=BRT)
    ate = datetime(ano + 1, 1, 1, tzinfo=BRT) if mes == 12 else datetime(ano, mes + 1, 1, tzinfo=BRT)
    # com pré-reserva: é o calendário que mostra o que está OCUPADO
    return listar_eventos(pool, conta_id, de, ate, incluir_pre_reserva=True)


# ---------- config do lembrete (opt-in) + feed .ics assinável ----------

def get_config(pool, conta_id: int) -> dict:
    """Config do lembrete da conta (cria defaults na memória se ainda não salvou)."""
    with pool.connection() as c:
        r = c.execute(
            "select lembrete_ativo, hora_resumo, aviso_antes_min, feed_token, resumo_ativo, "
            "avisar_convidados, enviar_confirmacao, pre_reserva_dias "
            "from agenda_config where conta_id=%s",
            (conta_id,)).fetchone()
    if not r:
        return {"lembrete_ativo": False, "resumo_ativo": False, "hora_resumo": 7,
                "aviso_antes_min": None, "feed_token": None, "avisar_convidados": True,
                "enviar_confirmacao": True, "pre_reserva_dias": PRE_RESERVA_DIAS}
    return {"lembrete_ativo": bool(r[0]), "hora_resumo": r[1],
            "aviso_antes_min": r[2], "feed_token": r[3], "resumo_ativo": bool(r[4]),
            "avisar_convidados": bool(r[5]), "enviar_confirmacao": bool(r[6]),
            "pre_reserva_dias": r[7] or PRE_RESERVA_DIAS}


def salvar_config(pool, conta_id: int, *, resumo_ativo: bool, hora_resumo: int,
                  aviso_antes_min: int | None, avisar_convidados: bool = True,
                  enviar_confirmacao: bool = True,
                  pre_reserva_dias: int | None = None) -> None:
    """Grava (upsert) a config do lembrete. resumo_ativo liga o "resumo do dia";
    aviso_antes_min (None = desligado) liga o "aviso antes"; avisar_convidados
    estende o "aviso antes" pra quem confirmou presença (só importa quando
    aviso_antes_min também está ligado); enviar_confirmacao liga a resposta
    automática pro convidado quando ele responde ao convite (independe dos
    outros dois). lembrete_ativo é o opt-in geral, derivado (ligado se qualquer
    um dos dois primeiros estiver).

    pre_reserva_dias (quanto tempo a data fica segurada esperando o sinal) é o
    único que aceita None como "não mexe": ele não vive no mesmo card que os
    lembretes, e um salvar de lembrete não pode zerar a regra de reserva de quem
    já ajustou pra 7 dias."""
    hora = hora_resumo if 0 <= (hora_resumo or 0) <= 23 else 7
    lembrete_ativo = bool(resumo_ativo) or (aviso_antes_min is not None)
    dias = None
    if pre_reserva_dias is not None:
        dias = min(90, max(1, int(pre_reserva_dias)))
    with pool.connection() as c:
        c.execute(
            """insert into agenda_config
                 (conta_id, lembrete_ativo, resumo_ativo, hora_resumo, aviso_antes_min,
                  avisar_convidados, enviar_confirmacao, pre_reserva_dias, atualizado_em)
               values (%s,%s,%s,%s,%s,%s,%s, coalesce(%s::int, %s), now())
               on conflict (conta_id) do update set
                 lembrete_ativo     = excluded.lembrete_ativo,
                 resumo_ativo       = excluded.resumo_ativo,
                 hora_resumo        = excluded.hora_resumo,
                 aviso_antes_min    = excluded.aviso_antes_min,
                 avisar_convidados  = excluded.avisar_convidados,
                 enviar_confirmacao = excluded.enviar_confirmacao,
                 pre_reserva_dias   = coalesce(%s::int, agenda_config.pre_reserva_dias),
                 atualizado_em      = now()""",
            (conta_id, lembrete_ativo, bool(resumo_ativo), hora, aviso_antes_min,
             bool(avisar_convidados), bool(enviar_confirmacao), dias, PRE_RESERVA_DIAS, dias))
        c.commit()


def salvar_pre_reserva_dias(pool, conta_id: int, dias: int) -> int:
    """Quantos dias a data fica segurada esperando o sinal. Devolve o que gravou.

    Separado de salvar_config porque é outra decisão e outro card: lembrete é
    preferência de aviso, prazo de reserva é regra de venda. Juntos, salvar um
    mexeria no outro."""
    d = min(90, max(1, int(dias)))
    with pool.connection() as c:
        c.execute(
            """insert into agenda_config (conta_id, pre_reserva_dias, atualizado_em)
               values (%s,%s, now())
               on conflict (conta_id) do update set
                 pre_reserva_dias = excluded.pre_reserva_dias, atualizado_em = now()""",
            (conta_id, d))
        c.commit()
    return d


def garantir_feed_token(pool, conta_id: int) -> str:
    """Devolve o token do feed .ics da conta, criando um (e a linha de config) se preciso.
    O token é o segredo que deixa o link .ics ser público sem expor a conta."""
    import secrets
    cfg = get_config(pool, conta_id)
    if cfg.get("feed_token"):
        return cfg["feed_token"]
    token = secrets.token_urlsafe(18)
    with pool.connection() as c:
        c.execute(
            """insert into agenda_config (conta_id, feed_token) values (%s,%s)
               on conflict (conta_id) do update set feed_token =
                 coalesce(agenda_config.feed_token, excluded.feed_token)""",
            (conta_id, token))
        r = c.execute("select feed_token from agenda_config where conta_id=%s",
                      (conta_id,)).fetchone()
        c.commit()
    return r[0]


def conta_por_feed_token(pool, token: str) -> int | None:
    """Acha a conta dona de um feed .ics pelo token (rota pública do .ics)."""
    token = (token or "").strip()
    if not token:
        return None
    with pool.connection() as c:
        r = c.execute("select conta_id from agenda_config where feed_token=%s",
                      (token,)).fetchone()
    return r[0] if r else None


def eventos_para_feed(pool, conta_id: int) -> list[dict]:
    """Eventos que vão no feed .ics: dos últimos 30 dias em diante (histórico curto
    + tudo que vem), com teto pra não estourar."""
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            "where conta_id=%s and status='ativo' and inicio >= %s "
            "order by inicio limit 500",
            (conta_id, agora_brt() - timedelta(days=30))).fetchall()
    return [_fmt_evento(r) for r in rows]
