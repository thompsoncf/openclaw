"""O cadastro da clínica: profissionais, tipos de atendimento, locais e a grade.

Fase 1 ("base") do plano aprovado em docs/mockups/clinica_visao_geral.html, com as
telas de Configurar do protótipo (clinica_prototipo.html). É o que o resto usa:
a agenda do dia (fase 2) desenha uma coluna por profissional, o agente (fase 3)
oferece horário livre de quem FAZ aquele atendimento, e o roteiro (fase 4) abre
dia em cidade.

O ZAQ É A AGENDA DA CLÍNICA (decisão do dono, 25/09/2026: o Amigo sai). Não há
segunda agenda pra conciliar; o horário livre sai só daqui.

AS TRÊS PEÇAS DE HORÁRIO:
  grade       a semana de cada profissional, por local: "seg a sex 08:00–12:00".
              Repete toda semana, a cada 15 dias ou uma vez por mês ("3ª quinta").
  bloqueio    exceção: congresso, férias, feriado. Sem profissional = a clínica
              toda; sem horário = o dia todo.
  ocupado     o que já foi marcado (a agenda da fase 2 passa essa lista).
  livre = grade − bloqueios − ocupados, em passos de 30 minutos, cabendo a duração
  do tipo de atendimento inteira antes do fim da faixa.

Horários de grade são HORA DE BRASÍLIA (-3 fixo, a mesma convenção do resto do
painel: `funil_regua._UTC_BR`). O que sai de `livres` é UTC, pronto pra gravar.

Nada aqui é dado de saúde: é quem atende, onde, quando e quanto custa.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta, timezone

from finance import funil_regua as fr

CATEGORIAS = (("consulta", "Consulta"), ("retorno", "Retorno"),
              ("procedimento", "Procedimento"), ("cirurgia", "Cirurgia"),
              ("exame", "Exame"), ("sessao", "Sessão de pacote"))
FUNCOES = ("Dermatologista", "Fisioterapeuta dermatofuncional", "Esteticista",
           "Enfermagem", "Recepção, não atende")
CORES = ("#25D366", "#229ED9", "#C9A3E0", "#E0A32E", "#E0574F", "#8FE3B8",
         "#46F58A", "#8FA197")
ACESSOS = (("sem_login", "Só agenda (sem login)"), ("propria", "Vê a própria agenda"),
           ("gestor", "Gestor"))
REPETE = (("semanal", "Toda semana"), ("quinzenal", "A cada 15 dias"),
          ("mensal", "Uma vez por mês"))
DIAS = ((1, "Seg"), (2, "Ter"), (3, "Qua"), (4, "Qui"), (5, "Sex"), (6, "Sáb"), (7, "Dom"))
ORDINAL = {1: "1ª", 2: "2ª", 3: "3ª", 4: "4ª", 5: "última"}

PASSO_MIN = 30          # a grade de horário da clínica anda de meia em meia hora
_RE_COR = re.compile(r"^#[0-9A-Fa-f]{6}$")


# ------------------------------------------------------------------ regras puras

def dias_de(txt: str | None) -> list[int]:
    """"1,2,3" → [1, 2, 3]. Ignora lixo; nunca estoura."""
    out = []
    for p in (txt or "").split(","):
        p = p.strip()
        if p.isdigit() and 1 <= int(p) <= 7 and int(p) not in out:
            out.append(int(p))
    return sorted(out)


def dias_txt(dias) -> str:
    """[1,2,3,4,5] → "Seg a Sex"; [2,4] → "Ter, Qui"."""
    ds = sorted(set(int(d) for d in dias))
    nome = dict(DIAS)
    if not ds:
        return "—"
    if len(ds) > 2 and ds == list(range(ds[0], ds[-1] + 1)):
        return f"{nome[ds[0]]} a {nome[ds[-1]]}"
    return ", ".join(nome[d] for d in ds)


def _semana_do_mes(dia: date) -> int:
    return (dia.day - 1) // 7 + 1


def _ultima_do_mes(dia: date) -> bool:
    return dia.day + 7 > calendar.monthrange(dia.year, dia.month)[1]


def acontece(regra: dict, dia: date) -> bool:
    """A faixa de grade vale neste dia?"""
    if dia.isoweekday() not in dias_de(regra.get("dias")):
        return False
    repete = regra.get("repete") or "semanal"
    if repete == "quinzenal":
        ref = regra.get("referencia")
        if not ref:
            return True     # sem âncora, vale como semanal — melhor sobrar que sumir
        segunda_ref = ref - timedelta(days=ref.isoweekday() - 1)
        segunda_dia = dia - timedelta(days=dia.isoweekday() - 1)
        return ((segunda_dia - segunda_ref).days // 7) % 2 == 0
    if repete == "mensal":
        n = regra.get("semana_do_mes") or 1
        return _ultima_do_mes(dia) if n == 5 else _semana_do_mes(dia) == n
    return True


def _min(t: time) -> int:
    return t.hour * 60 + t.minute


def _hora(m: int) -> time:
    return time(m // 60, m % 60)


def faixas_do_dia(grade: list[dict], bloqueios: list[dict], profissional_id: int,
                  dia: date) -> list[dict]:
    """As faixas em que o profissional atende neste dia, já sem os bloqueios.

    Devolve [{inicio, fim, local_id, encaixes}], em hora de Brasília, ordenadas.
    """
    faixas = [{"inicio": _min(g["inicio"]), "fim": _min(g["fim"]),
               "local_id": g["local_id"], "encaixes": g.get("encaixes") or 0}
              for g in grade
              if g["profissional_id"] == profissional_id and g.get("ativo", True)
              and acontece(g, dia)]
    for b in bloqueios:
        if b.get("profissional_id") not in (None, profissional_id):
            continue
        if not (b["de"] <= dia <= b["ate"]):
            continue
        if b.get("inicio") is None:
            return []                                   # o dia todo
        bi, bf = _min(b["inicio"]), _min(b["fim"])
        cortadas = []
        for f in faixas:
            if bf <= f["inicio"] or bi >= f["fim"]:
                cortadas.append(f)
                continue
            if f["inicio"] < bi:
                cortadas.append(dict(f, fim=bi))
            if bf < f["fim"]:
                cortadas.append(dict(f, inicio=bf))
        faixas = cortadas
    return sorted(({**f, "inicio": _hora(f["inicio"]), "fim": _hora(f["fim"])} for f in faixas),
                  key=lambda f: f["inicio"])


def faixas_txt(faixas: list[dict]) -> str:
    """[08:00–12:00, 13:30–16:30] → "08:00–12:00 e 13:30–16:30"."""
    if not faixas:
        return "—"
    return " e ".join(f"{f['inicio']:%H:%M}–{f['fim']:%H:%M}" for f in faixas)


def inicios_na_faixa(inicio: time, fim: time, duracao_min: int) -> list[time]:
    """Onde dá pra começar um atendimento de `duracao_min` dentro da faixa, de meia
    em meia hora (ou no passo da duração, se for menor que 30)."""
    passo = min(PASSO_MIN, max(5, int(duracao_min or PASSO_MIN)))
    a, b, d = _min(inicio), _min(fim), int(duracao_min or PASSO_MIN)
    return [_hora(m) for m in range(a, b - d + 1, passo)]


def _utc(dia: date, hora: time) -> datetime:
    return (datetime.combine(dia, hora) - fr._UTC_BR).replace(tzinfo=timezone.utc)


def livres_puros(grade: list[dict], bloqueios: list[dict], profissional_id: int,
                 duracao_min: int, de: date, dias: int, agora: datetime,
                 ocupados: list[tuple[datetime, datetime]] | None = None,
                 limite: int | None = None) -> list[dict]:
    """[{inicio (UTC), fim (UTC), local_id}] livres, do dia `de` por `dias` dias."""
    ocupados = ocupados or []
    out: list[dict] = []
    dur = timedelta(minutes=int(duracao_min or PASSO_MIN))
    for i in range(max(0, int(dias))):
        dia = de + timedelta(days=i)
        for f in faixas_do_dia(grade, bloqueios, profissional_id, dia):
            for h in inicios_na_faixa(f["inicio"], f["fim"], duracao_min):
                ini = _utc(dia, h)
                fim = ini + dur
                if ini <= agora:
                    continue
                if any(ini < of and oi < fim for oi, of in ocupados):
                    continue
                out.append({"inicio": ini, "fim": fim, "local_id": f["local_id"]})
                if limite and len(out) >= limite:
                    return out
    return out


def centavos(txt: str | None) -> int | None:
    """'500' / '500,00' / 'R$ 1.200,50' → centavos. Vazio → 0 (sob consulta).
    Texto que não é número → None (erro de validação)."""
    t = (txt or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        return 0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        v = round(float(t) * 100)
    except ValueError:
        return None
    return v if v >= 0 else None


def reais(c: int | None) -> str:
    if not c:
        return "sob consulta"
    v = c / 100
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "R$ " + (s[:-3] if s.endswith(",00") else s)


# ------------------------------------------------------------------ leitura

def _do_escopo(c, conta_id: int, tabela: str, id_: int | None) -> bool:
    if id_ is None:
        return True
    return bool(c.execute(f"select 1 from {tabela} where id=%s and conta_id=%s",
                          (id_, conta_id)).fetchone())


def listar_tipos(c, conta_id: int, so_ativos: bool = True) -> list[dict]:
    rows = c.execute(
        """select id, nome, coalesce(duracao_min, 30), coalesce(categoria, ''), cor,
                  coalesce(setup_centavos, 0), volta_dias, coalesce(volta_motivo, ''),
                  agente_diz_preco, agente_marca, ativo, slug
             from servicos_catalogo where conta_id=%s""" + (" and ativo" if so_ativos else "")
        + " order by ordem, id", (conta_id,)).fetchall()
    return [{"id": r[0], "nome": r[1], "duracao_min": r[2], "categoria": r[3],
             "cor": r[4] or CORES[0], "preco_centavos": r[5], "preco": reais(r[5]),
             "volta_dias": r[6], "volta_motivo": r[7], "agente_diz_preco": r[8],
             "agente_marca": r[9], "ativo": r[10], "slug": r[11]} for r in rows]


def listar_locais(c, conta_id: int, so_ativos: bool = True) -> list[dict]:
    rows = c.execute(
        """select id, nome, coalesce(endereco,''), coalesce(cidade,''), tipo, ativo
             from clinica_locais where conta_id=%s""" + (" and ativo" if so_ativos else "")
        + " order by tipo <> 'sede', ordem, nome", (conta_id,)).fetchall()
    return [{"id": r[0], "nome": r[1], "endereco": r[2], "cidade": r[3], "tipo": r[4],
             "ativo": r[5]} for r in rows]


def listar_grade(c, conta_id: int) -> list[dict]:
    rows = c.execute(
        """select id, profissional_id, local_id, dias, inicio, fim, repete, semana_do_mes,
                  referencia, encaixes
             from clinica_grade where conta_id=%s and ativo
            order by profissional_id, inicio""", (conta_id,)).fetchall()
    return [{"id": r[0], "profissional_id": r[1], "local_id": r[2], "dias": r[3],
             "inicio": r[4], "fim": r[5], "repete": r[6], "semana_do_mes": r[7],
             "referencia": r[8], "encaixes": r[9], "ativo": True} for r in rows]


def listar_bloqueios(c, conta_id: int, desde: date | None = None) -> list[dict]:
    rows = c.execute(
        """select id, profissional_id, de, ate, inicio, fim, coalesce(motivo,'')
             from clinica_bloqueios where conta_id=%s and ate >= %s
            order by de, id""", (conta_id, desde or date(1970, 1, 1))).fetchall()
    return [{"id": r[0], "profissional_id": r[1], "de": r[2], "ate": r[3],
             "inicio": r[4], "fim": r[5], "motivo": r[6]} for r in rows]


def listar_profissionais(c, conta_id: int, so_ativos: bool = True) -> list[dict]:
    rows = c.execute(
        """select id, nome, coalesce(funcao,''), coalesce(especialidade,''),
                  coalesce(conselho,''), cor, membro_id, acesso, aviso_agenda, ativo
             from clinica_profissionais where conta_id=%s""" + (" and ativo" if so_ativos else "")
        + " order by ordem, id", (conta_id,)).fetchall()
    tipos: dict[int, list[int]] = {}
    for p, s in c.execute(
            "select profissional_id, servico_id from clinica_profissional_tipos where conta_id=%s",
            (conta_id,)).fetchall():
        tipos.setdefault(p, []).append(s)
    return [{"id": r[0], "nome": r[1], "funcao": r[2], "especialidade": r[3],
             "conselho": r[4], "cor": r[5] or CORES[0], "membro_id": r[6], "acesso": r[7],
             "aviso_agenda": r[8], "ativo": r[9], "tipos": tipos.get(r[0], [])} for r in rows]


def membros_da_conta(c, conta_id: int) -> list[dict]:
    return [{"id": r[0], "nome": r[1]} for r in c.execute(
        """select id, coalesce(nullif(nome,''), email, 'sem nome') from membros
            where conta_id=%s and coalesce(ativo, true) order by 2""", (conta_id,)).fetchall()]


def livres(c, conta_id: int, profissional_id: int, servico_id: int, de: date,
           dias: int = 7, agora: datetime | None = None,
           ocupados: list[tuple[datetime, datetime]] | None = None,
           limite: int | None = None) -> list[dict]:
    """Horários livres do profissional para aquele tipo de atendimento. Quem não
    faz o tipo não tem horário pra ele (lista vazia)."""
    agora = agora or datetime.now(timezone.utc)
    faz = c.execute(
        """select s.duracao_min from clinica_profissional_tipos pt
             join servicos_catalogo s on s.id = pt.servico_id and s.conta_id = pt.conta_id
            where pt.conta_id=%s and pt.profissional_id=%s and pt.servico_id=%s and s.ativo""",
        (conta_id, profissional_id, servico_id)).fetchone()
    if not faz:
        return []
    return livres_puros(listar_grade(c, conta_id), listar_bloqueios(c, conta_id, de),
                        profissional_id, faz[0] or PASSO_MIN, de, dias, agora,
                        ocupados, limite)


def resumo(c, conta_id: int) -> dict:
    """O "Colocar no ar": o que falta pra agenda e o agente funcionarem."""
    profs = listar_profissionais(c, conta_id)
    grade = listar_grade(c, conta_id)
    tipos = listar_tipos(c, conta_id)
    com_grade = {g["profissional_id"] for g in grade}
    atendem = [p for p in profs if p["funcao"] != "Recepção, não atende"]
    return {
        "profissionais": len(atendem),
        "sem_grade": [p["nome"] for p in atendem if p["id"] not in com_grade],
        "sem_tipo": [p["nome"] for p in atendem if not p["tipos"]],
        "tipos": len(tipos),
        "tipos_sem_preco": [t["nome"] for t in tipos if not t["preco_centavos"]],
        "locais": len(listar_locais(c, conta_id)),
    }


# ------------------------------------------------------------------ escrita
# Toda função devolve o erro pra tela (str) ou None. Nenhuma faz commit: quem
# chama (a rota) fecha a transação — um erro no meio desfaz tudo junto.

def salvar_profissional(c, conta_id: int, *, id: int | None = None, nome: str,
                        funcao: str = "", especialidade: str = "", conselho: str = "",
                        cor: str = "", acesso: str = "sem_login",
                        membro_id: int | None = None, aviso_agenda: bool = False,
                        tipos: list[int] | None = None) -> str | None:
    nome = (nome or "").strip()
    if not nome:
        return "Informe o nome do profissional como ele aparece na agenda."
    if acesso not in dict(ACESSOS):
        return "Acesso inválido."
    cor = (cor or "").strip() or CORES[0]
    if not _RE_COR.match(cor):
        return "Cor inválida."
    if acesso != "sem_login" and not membro_id:
        return "Pra ver a agenda no Zaq, escolha quem da equipe é este profissional."
    if membro_id and not _do_escopo(c, conta_id, "membros", membro_id):
        return "Membro não encontrado."
    if acesso == "sem_login":
        membro_id = None
    tipos = [int(t) for t in (tipos or [])]
    if tipos:
        ok = {r[0] for r in c.execute(
            "select id from servicos_catalogo where conta_id=%s and id = any(%s)",
            (conta_id, tipos)).fetchall()}
        if set(tipos) - ok:
            return "Tipo de atendimento não encontrado."
    campos = (nome[:80], (funcao or "").strip()[:80] or None, (especialidade or "").strip()[:80] or None,
              (conselho or "").strip()[:40] or None, cor, membro_id, acesso, bool(aviso_agenda))
    if id:
        r = c.execute(
            """update clinica_profissionais
                  set nome=%s, funcao=%s, especialidade=%s, conselho=%s, cor=%s,
                      membro_id=%s, acesso=%s, aviso_agenda=%s
                where id=%s and conta_id=%s returning id""", campos + (id, conta_id)).fetchone()
        if not r:
            return "Profissional não encontrado."
    else:
        ordem = c.execute("select coalesce(max(ordem),0)+1 from clinica_profissionais where conta_id=%s",
                          (conta_id,)).fetchone()[0]
        id = c.execute(
            """insert into clinica_profissionais
                 (nome, funcao, especialidade, conselho, cor, membro_id, acesso, aviso_agenda,
                  conta_id, ordem)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            campos + (conta_id, ordem)).fetchone()[0]
    c.execute("delete from clinica_profissional_tipos where conta_id=%s and profissional_id=%s",
              (conta_id, id))
    for t in tipos:
        c.execute("""insert into clinica_profissional_tipos (conta_id, profissional_id, servico_id)
                     values (%s,%s,%s) on conflict do nothing""", (conta_id, id, t))
    return None


def desativar(c, conta_id: int, tabela: str, id_: int) -> bool:
    """Tira da tela sem apagar: agendamento antigo continua apontando pra ele."""
    if tabela not in ("clinica_profissionais", "clinica_locais", "clinica_grade"):
        raise ValueError(tabela)
    return bool(c.execute(f"update {tabela} set ativo=false where id=%s and conta_id=%s returning id",
                          (id_, conta_id)).fetchone())


def salvar_local(c, conta_id: int, *, id: int | None = None, nome: str,
                 endereco: str = "", cidade: str = "", tipo: str = "sede") -> str | None:
    nome = (nome or "").strip()
    if not nome:
        return "Informe o nome do local."
    if tipo not in ("sede", "viagem"):
        return "Tipo de local inválido."
    campos = (nome[:80], (endereco or "").strip()[:160] or None, (cidade or "").strip()[:80] or None, tipo)
    if id:
        r = c.execute("""update clinica_locais set nome=%s, endereco=%s, cidade=%s, tipo=%s
                          where id=%s and conta_id=%s returning id""", campos + (id, conta_id)).fetchone()
        return None if r else "Local não encontrado."
    c.execute("""insert into clinica_locais (nome, endereco, cidade, tipo, conta_id)
                 values (%s,%s,%s,%s,%s)""", campos + (conta_id,))
    return None


def salvar_tipo(c, conta_id: int, *, id: int | None = None, nome: str,
                duracao_min: int = 30, categoria: str = "consulta", cor: str = "",
                preco_centavos: int = 0, volta_dias: int | None = None,
                volta_motivo: str = "", agente_diz_preco: bool = False,
                agente_marca: bool = False) -> str | None:
    nome = (nome or "").strip()
    if not nome:
        return "Informe o nome do atendimento."
    if categoria not in dict(CATEGORIAS):
        return "Categoria inválida."
    try:
        duracao_min = int(duracao_min)
    except (TypeError, ValueError):
        return "Duração inválida."
    if not 5 <= duracao_min <= 480:
        return "A duração vai de 5 minutos a 8 horas."
    if preco_centavos is None or preco_centavos < 0:
        return "Preço inválido."
    if volta_dias is not None and not 1 <= int(volta_dias) <= 730:
        return "O prazo de volta vai de 1 a 730 dias."
    cor = (cor or "").strip() or CORES[0]
    if not _RE_COR.match(cor):
        return "Cor inválida."
    # o agente só diz preço que existe: zero é "sob consulta"
    diz = bool(agente_diz_preco) and preco_centavos > 0
    campos = (nome[:80], duracao_min, categoria, cor, int(preco_centavos),
              int(volta_dias) if volta_dias else None, (volta_motivo or "").strip()[:200] or None,
              diz, bool(agente_marca))
    if id:
        r = c.execute(
            """update servicos_catalogo
                  set nome=%s, duracao_min=%s, categoria=%s, cor=%s, setup_centavos=%s,
                      volta_dias=%s, volta_motivo=%s, agente_diz_preco=%s, agente_marca=%s
                where id=%s and conta_id=%s and ativo returning id""",
            campos + (id, conta_id)).fetchone()
        return None if r else "Atendimento não encontrado."
    from finance.servicos_catalogo import _slugify
    existentes = {x[0] for x in c.execute(
        "select slug from servicos_catalogo where conta_id=%s", (conta_id,)).fetchall()}
    ordem = c.execute("select coalesce(max(ordem),0)+1 from servicos_catalogo where conta_id=%s",
                      (conta_id,)).fetchone()[0]
    c.execute(
        """insert into servicos_catalogo
             (nome, duracao_min, categoria, cor, setup_centavos, volta_dias, volta_motivo,
              agente_diz_preco, agente_marca, conta_id, slug, ordem, mensal_centavos)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0)""",
        campos + (conta_id, _slugify(nome, existentes), ordem))
    return None


def desativar_tipo(c, conta_id: int, id_: int) -> bool:
    return bool(c.execute(
        "update servicos_catalogo set ativo=false where id=%s and conta_id=%s returning id",
        (id_, conta_id)).fetchone())


def _hora_de(txt: str | None) -> time | None:
    try:
        return datetime.strptime((txt or "").strip(), "%H:%M").time()
    except ValueError:
        return None


def salvar_grade(c, conta_id: int, *, profissional_id: int, local_id: int, dias: list[int],
                 inicio: str, fim: str, repete: str = "semanal",
                 semana_do_mes: int | None = None, referencia: date | None = None,
                 encaixes: int = 0) -> str | None:
    if not _do_escopo(c, conta_id, "clinica_profissionais", profissional_id):
        return "Profissional não encontrado."
    if not _do_escopo(c, conta_id, "clinica_locais", local_id):
        return "Local não encontrado."
    ds = dias_de(",".join(str(d) for d in dias))
    if not ds:
        return "Escolha pelo menos um dia da semana."
    hi, hf = _hora_de(inicio), _hora_de(fim)
    if not hi or not hf or hf <= hi:
        return "Informe início e fim, com o fim depois do início."
    if repete not in dict(REPETE):
        return "Repetição inválida."
    if repete == "mensal" and not (semana_do_mes and 1 <= int(semana_do_mes) <= 5):
        return "Na repetição mensal, escolha qual semana do mês (1ª a 4ª, ou a última)."
    if repete == "quinzenal" and not referencia:
        return "Na repetição a cada 15 dias, informe uma data em que o atendimento acontece."
    try:
        encaixes = int(encaixes or 0)
    except (TypeError, ValueError):
        encaixes = 0
    if not 0 <= encaixes <= 20:
        return "Encaixes vão de 0 a 20 por dia."
    # a mesma pessoa em dois lugares ao mesmo tempo: só pega o caso certo (as duas
    # semanais); quinzenal e mensal cruzando semanal é escolha consciente de quem
    # viaja, e o bloqueio resolve
    if repete == "semanal":
        for g in listar_grade(c, conta_id):
            if (g["profissional_id"] == profissional_id and g["repete"] == "semanal"
                    and set(dias_de(g["dias"])) & set(ds) and hi < g["fim"] and g["inicio"] < hf):
                return (f"Esse horário cruza com outra faixa do mesmo profissional "
                        f"({dias_txt(dias_de(g['dias']))}, {g['inicio']:%H:%M}–{g['fim']:%H:%M}).")
    c.execute(
        """insert into clinica_grade (conta_id, profissional_id, local_id, dias, inicio, fim,
                                      repete, semana_do_mes, referencia, encaixes)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (conta_id, profissional_id, local_id, ",".join(str(d) for d in ds), hi, hf, repete,
         int(semana_do_mes) if repete == "mensal" else None,
         referencia if repete == "quinzenal" else None, encaixes))
    return None


def salvar_bloqueio(c, conta_id: int, *, profissional_id: int | None, de: date, ate: date | None,
                    inicio: str = "", fim: str = "", motivo: str = "") -> str | None:
    if profissional_id and not _do_escopo(c, conta_id, "clinica_profissionais", profissional_id):
        return "Profissional não encontrado."
    if not de:
        return "Informe a data."
    ate = ate or de
    if ate < de:
        return "A data final vem antes da inicial."
    hi, hf = _hora_de(inicio), _hora_de(fim)
    if (inicio or fim) and (not hi or not hf or hf <= hi):
        return "Informe início e fim do bloqueio, ou deixe os dois em branco pro dia todo."
    c.execute(
        """insert into clinica_bloqueios (conta_id, profissional_id, de, ate, inicio, fim, motivo)
           values (%s,%s,%s,%s,%s,%s,%s)""",
        (conta_id, profissional_id or None, de, ate, hi, hf, (motivo or "").strip()[:120] or None))
    return None


def remover_bloqueio(c, conta_id: int, id_: int) -> bool:
    return bool(c.execute("delete from clinica_bloqueios where id=%s and conta_id=%s returning id",
                          (id_, conta_id)).fetchone())
