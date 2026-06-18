"""Contas (tenants) e membros.

A CONTA e' quem assina e paga (PF individual, PF familia ou PJ). Os MEMBROS
sao as pessoas dela (dono + familia/funcionarios), cada um identificado pelo
canal (telegram_id ou whatsapp_id). O cadastro acontece no PORTAL; o chat
NAO cria conta sozinho - se o numero nao for de um membro, orienta a se
cadastrar.

Aqui tambem moram: controle de acesso (status + vencimento), protecao de
custo (teto de mensagens/dia POR CONTA), auditoria e mascaramento LGPD.
"""
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class Conta:
    id: int
    tipo: str                  # 'pf' | 'pj'
    nome: str
    documento: str | None
    plano: str | None
    status: str                # trial | ativa | inadimplente | suspensa | cancelada
    vencimento: date | None
    limite_mensagens_dia: int
    limite_cupons_dia: int
    cidade: str | None = None


@dataclass
class Membro:
    id: int
    conta_id: int
    nome: str | None
    papel: str                 # 'dono' | 'membro'
    telegram_id: int | None
    whatsapp_id: str | None
    ativo: bool


_C = "id, tipo, nome, documento, plano, status, vencimento, limite_mensagens_dia, limite_cupons_dia, cidade"
_M = "id, conta_id, nome, papel, telegram_id, whatsapp_id, ativo"


# ---------- Identificacao (quem esta falando?) ----------

def membro_por_telegram(pool, telegram_id: int) -> tuple[Membro, Conta] | None:
    return _membro_por(pool, "telegram_id", telegram_id)


def membro_por_whatsapp(pool, whatsapp_id: str) -> tuple[Membro, Conta] | None:
    return _membro_por(pool, "whatsapp_id", whatsapp_id)


def _membro_por(pool, coluna: str, valor) -> tuple[Membro, Conta] | None:
    with pool.connection() as conn:
        row = conn.execute(
            f"""select {", ".join("m."+c.strip() for c in _M.split(","))},
                       {", ".join("c."+c.strip() for c in _C.split(","))}
                from membros m join contas c on c.id = m.conta_id
                where m.{coluna} = %s and m.ativo""",
            (valor,),
        ).fetchone()
    if row is None:
        return None
    n = len(_M.split(","))
    return Membro(*row[:n]), Conta(*row[n:])


# ---------- Acesso (a conta pode usar?) ----------

def acesso_liberado(conta: Conta) -> bool:
    """True se a conta pode usar o servico.

    trial/ativa dentro da validade -> ok. Vencida, suspensa ou cancelada -> nao.
    """
    if conta.status in ("suspensa", "cancelada"):
        return False
    if conta.vencimento is not None and conta.vencimento < date.today():
        return False
    return conta.status in ("trial", "ativa", "inadimplente")


def ativar(pool, conta_id: int, dias: int = 30, plano: str | None = None) -> date:
    """Pagamento confirmado: ativa e estende a validade. Registra auditoria."""
    venc = date.today() + timedelta(days=dias)
    with pool.connection() as conn:
        if plano:
            conn.execute("update contas set status='ativa', vencimento=%s, plano=%s where id=%s",
                         (venc, plano, conta_id))
        else:
            conn.execute("update contas set status='ativa', vencimento=%s where id=%s",
                         (venc, conta_id))
        conn.commit()
    registrar_evento(pool, conta_id, "ativacao", f"validade ate {venc.isoformat()}")
    return venc


def suspender(pool, conta_id: int, motivo: str = ""):
    """Corta o acesso (inadimplencia confirmada/cancelamento). Registra auditoria."""
    with pool.connection() as conn:
        conn.execute("update contas set status='suspensa' where id=%s", (conta_id,))
        conn.commit()
    registrar_evento(pool, conta_id, "suspensao", motivo)


def marcar_inadimplente(pool, conta_id: int):
    """Pagamento atrasou: marca (carencia); o corte vem com suspender()."""
    with pool.connection() as conn:
        conn.execute("update contas set status='inadimplente' where id=%s", (conta_id,))
        conn.commit()
    registrar_evento(pool, conta_id, "inadimplencia", "pagamento em atraso")


# ---------- Protecao de custo (POR CONTA) ----------

def checar_e_registrar_uso(pool, conta: Conta, eh_cupom: bool = False) -> tuple[bool, int]:
    """Incrementa o uso do dia da CONTA. eh_cupom=True conta no limite SEPARADO de
    cupons (foto/PDF, ~5x mais caro); senao conta no limite de mensagens de texto.
    Retorna (liberado, restante) do contador relevante."""
    hoje = date.today()
    coluna = "cupons" if eh_cupom else "mensagens"
    limite = conta.limite_cupons_dia if eh_cupom else conta.limite_mensagens_dia
    with pool.connection() as conn:
        row = conn.execute(
            f"select {coluna} from uso_diario where conta_id = %s and dia = %s",
            (conta.id, hoje),
        ).fetchone()
        usado = row[0] if row else 0
        if usado >= limite:
            return False, 0
        conn.execute(
            f"""insert into uso_diario (conta_id, dia, {coluna}) values (%s,%s,1)
               on conflict (conta_id, dia) do update
                 set {coluna} = uso_diario.{coluna} + 1""",
            (conta.id, hoje),
        )
        conn.commit()
    return True, limite - (usado + 1)


# ---------- Auditoria ----------

def registrar_evento(pool, conta_id: int, tipo: str, detalhe: str = "",
                     membro_id: int | None = None):
    with pool.connection() as conn:
        conn.execute(
            "insert into eventos_conta (conta_id, membro_id, tipo, detalhe) values (%s,%s,%s,%s)",
            (conta_id, membro_id, tipo, detalhe),
        )
        conn.commit()


# ---------- Administracao (portal/admin; o chat NAO cria conta) ----------

def criar_conta(pool, tipo: str, nome: str, plano: str | None = None,
                documento: str | None = None, status: str = "trial",
                vencimento: date | None = None, cidade: str | None = None) -> int:
    if vencimento is None and status == "trial":
        vencimento = date.today() + timedelta(days=7)   # teste gratis padrao
    with pool.connection() as conn:
        row = conn.execute(
            """insert into contas (tipo, nome, documento, plano, status, vencimento, cidade)
               values (%s,%s,%s,%s,%s,%s,%s) returning id""",
            (tipo, nome, documento, plano, status, vencimento, cidade),
        ).fetchone()
        conn.commit()
    registrar_evento(pool, row[0], "conta_criada", f"tipo={tipo} plano={plano or '-'} cidade={cidade or '-'}")
    return int(row[0])


def adicionar_membro(pool, conta_id: int, nome: str | None = None,
                     papel: str = "membro", telegram_id: int | None = None,
                     whatsapp_id: str | None = None) -> int:
    with pool.connection() as conn:
        row = conn.execute(
            """insert into membros (conta_id, nome, papel, telegram_id, whatsapp_id)
               values (%s,%s,%s,%s,%s) returning id""",
            (conta_id, nome, papel, telegram_id, whatsapp_id),
        ).fetchone()
        conn.commit()
    registrar_evento(pool, conta_id, "membro_adicionado",
                     f"{nome or 'sem nome'} ({papel})", membro_id=row[0])
    return int(row[0])


def resgatar_convite_canal(pool, codigo: str, canal: str, valor) -> tuple[bool, str]:
    """Vincula um convite a um canal (telegram_id ou whatsapp_id). Generico.
    canal: 'telegram' ou 'whatsapp'. valor: id do telegram (int) ou numero do
    whatsapp (str). Mantem o Telegram funcionando igual."""
    codigo = (codigo or "").strip().upper()
    if not codigo:
        return False, "Codigo vazio."
    if canal == "telegram":
        coluna, rotulo = "telegram_id", "Telegram"
    elif canal == "whatsapp":
        coluna, rotulo = "whatsapp_id", "WhatsApp"
    else:
        return False, "Canal invalido."
    with pool.connection() as conn:
        ja = conn.execute(
            f"select 1 from membros where {coluna}=%s", (valor,)
        ).fetchone()
        if ja:
            return False, f"Esse {rotulo} ja' esta vinculado a uma conta."
        m = conn.execute(
            f"select id, conta_id, nome, papel from membros "
            f"where codigo_convite=%s and {coluna} is null",
            (codigo,),
        ).fetchone()
        if not m:
            return False, "Codigo invalido ou ja' utilizado."
        conn.execute(
            f"update membros set {coluna}=%s, codigo_convite=null, ativo=true where id=%s",
            (valor, m[0]),
        )
        conn.commit()
    registrar_evento(pool, m[1], "convite_resgatado",
                     f"{m[2]} vinculou {rotulo}", membro_id=m[0])
    return True, f"Pronto, {m[2] or 'tudo certo'}! Voce foi vinculado a' conta."


def resgatar_convite(pool, codigo: str, telegram_id: int) -> tuple[bool, str]:
    """Atalho pra Telegram (mantido pra compatibilidade)."""
    return resgatar_convite_canal(pool, codigo, "telegram", telegram_id)


def gerar_convite_para(pool, membro_id: int, conta_id: int) -> str | None:
    """(Re)gera um codigo de convite pra um membro EXISTENTE desta conta.
    Zera o telegram_id (vai ser revinculado ao resgatar). Retorna o codigo,
    ou None se o membro nao for desta conta ou for o dono.
    """
    import secrets
    with pool.connection() as conn:
        m = conn.execute(
            "select papel, (select nome from contas where id=%s) from membros where id=%s and conta_id=%s",
            (conta_id, membro_id, conta_id),
        ).fetchone()
        if not m or m[0] == "dono":
            return None
        pref = "".join(ch for ch in (m[1] or "OPEN") if ch.isalnum())[:3].upper() or "OPC"
        codigo = f"{pref}-{secrets.token_hex(2).upper()}"
        conn.execute(
            "update membros set codigo_convite=%s, telegram_id=null, ativo=true where id=%s and conta_id=%s",
            (codigo, membro_id, conta_id),
        )
        conn.commit()
    registrar_evento(pool, conta_id, "convite_regerado", f"cod={codigo}", membro_id=membro_id)
    return codigo


def regerar_acesso(pool, membro_id: int, conta_id: int) -> str | None:
    """Recupera o acesso de QUALQUER membro (inclusive o dono): zera os dois
    canais (telegram_id E whatsapp_id) e emite um codigo novo, pra reconectar de
    outro aparelho/numero. Caso 'troquei de celular' / 'perdi o numero'.
    Retorna o codigo, ou None se o membro nao for desta conta."""
    import secrets
    with pool.connection() as conn:
        m = conn.execute(
            "select (select nome from contas where id=%s) "
            "from membros where id=%s and conta_id=%s",
            (conta_id, membro_id, conta_id),
        ).fetchone()
        if not m:
            return None
        pref = "".join(ch for ch in (m[0] or "OPEN") if ch.isalnum())[:3].upper() or "OPC"
        codigo = f"{pref}-{secrets.token_hex(2).upper()}"
        conn.execute(
            "update membros set codigo_convite=%s, telegram_id=null, "
            "whatsapp_id=null, ativo=true where id=%s and conta_id=%s",
            (codigo, membro_id, conta_id),
        )
        conn.commit()
    registrar_evento(pool, conta_id, "acesso_regerado", f"cod={codigo}",
                     membro_id=membro_id)
    return codigo


def gerar_convite_dono(pool, conta_id: int) -> str | None:
    """Gera um codigo de convite pro DONO da conta vincular o Telegram dele.
    Diferente de gerar_convite_para (que bloqueia dono), esta e' especifica pro
    onboarding do titular. Retorna None se o dono ja' vinculou o Telegram (tem
    telegram_id) ou se nao existir dono."""
    import secrets
    with pool.connection() as conn:
        m = conn.execute(
            "select id, telegram_id, (select nome from contas where id=%s) "
            "from membros where conta_id=%s and papel='dono'",
            (conta_id, conta_id)).fetchone()
        if not m or m[1] is not None:
            return None
        pref = "".join(ch for ch in (m[2] or "OPEN") if ch.isalnum())[:3].upper() or "OPC"
        codigo = f"{pref}-{secrets.token_hex(2).upper()}"
        conn.execute("update membros set codigo_convite=%s where id=%s", (codigo, m[0]))
        conn.commit()
    return codigo


def reativar_membro(pool, membro_id: int, conta_id: int) -> bool:
    with pool.connection() as conn:
        r = conn.execute(
            "update membros set ativo=true where id=%s and conta_id=%s and papel <> 'dono'",
            (membro_id, conta_id),
        )
        conn.commit()
        if r.rowcount:
            registrar_evento(pool, conta_id, "membro_reativado", "", membro_id=membro_id)
        return r.rowcount > 0


def desativar_membro(pool, membro_id: int):
    """Funcionario saiu? Desativa (NUNCA apaga: o historico dele fica integro)."""
    with pool.connection() as conn:
        row = conn.execute(
            "update membros set ativo = false where id = %s returning conta_id", (membro_id,)
        ).fetchone()
        conn.commit()
    if row:
        registrar_evento(pool, row[0], "membro_desativado", "", membro_id=membro_id)


# ---------- Idempotencia (Telegram + WhatsApp) ----------

def reivindicar_mensagem(pool, chave: str) -> bool:
    """Retorna True se ESTE processo reivindicou a mensagem (deve processar).
    False se ja' foi reivindicada (outra instancia/reentrega) -> pular.

    Protege contra: multiplas instancias + reentrega do Telegram/Twilio.
    Chave: 'tg:update_id' (Telegram) ou 'wa:MessageSid' (WhatsApp)."""
    if not chave:
        return True   # sem chave (raro) -> processa
    try:
        with pool.connection() as conn:
            row = conn.execute(
                "insert into mensagens_processadas (chave) values (%s) "
                "on conflict (chave) do nothing returning chave",
                (chave,),
            ).fetchone()
            conn.commit()
        return row is not None   # inseriu = nosso; None = ja' existia
    except Exception:  # noqa: BLE001
        return True   # se a checagem falhar, melhor processar do que perder


# ---------- Leads (test-drive / cliente novo) ----------

LEAD_LIMITE_GASTOS = 5


def lead_estado(pool, canal: str, identificador: str) -> dict:
    """Retorna {gastos_usados, pode_testar, restantes} do lead. Cria se não existe."""
    ident = str(identificador)
    with pool.connection() as conn:
        row = conn.execute(
            "select gastos_usados from leads where canal=%s and identificador=%s",
            (canal, ident),
        ).fetchone()
        if row is None:
            conn.execute(
                "insert into leads (canal, identificador) values (%s,%s) "
                "on conflict (canal, identificador) do nothing",
                (canal, ident),
            )
            conn.commit()
            usados = 0
        else:
            usados = int(row[0])
    return {"gastos_usados": usados, "pode_testar": usados < LEAD_LIMITE_GASTOS,
            "restantes": max(0, LEAD_LIMITE_GASTOS - usados)}


def lead_registrar_gasto(pool, canal: str, identificador: str) -> int:
    """Incrementa o contador de gastos de teste. Retorna o novo total."""
    ident = str(identificador)
    with pool.connection() as conn:
        row = conn.execute(
            "update leads set gastos_usados = gastos_usados + 1, ultimo_em = now() "
            "where canal=%s and identificador=%s returning gastos_usados",
            (canal, ident),
        ).fetchone()
        conn.commit()
    return int(row[0]) if row else 0
