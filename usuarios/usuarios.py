"""Usuarios do piloto.

Cada pessoa chega com um id unico por canal: telegram_id (Telegram) ou
whatsapp_id (numero no WhatsApp). Aqui mora a identificacao, a protecao de
custo (teto de mensagens/dia) e o controle de acesso por pagamento.
"""
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class Usuario:
    id: int
    telegram_id: int | None
    whatsapp_id: str | None
    nome: str | None
    limite_mensagens_dia: int
    ativo: bool
    plano: str | None
    vencimento: date | None


_COLS = "id, telegram_id, whatsapp_id, nome, limite_mensagens_dia, ativo, plano, vencimento"


def get_or_create(pool, telegram_id: int, nome: str | None = None) -> Usuario:
    return _get_or_create(pool, "telegram_id", telegram_id, nome)


def get_or_create_whatsapp(pool, whatsapp_id: str, nome: str | None = None) -> Usuario:
    return _get_or_create(pool, "whatsapp_id", whatsapp_id, nome)


def _get_or_create(pool, coluna: str, valor, nome) -> Usuario:
    with pool.connection() as conn:
        row = conn.execute(
            f"select {_COLS} from usuarios where {coluna} = %s", (valor,)
        ).fetchone()
        if row is None:
            row = conn.execute(
                f"insert into usuarios ({coluna}, nome) values (%s,%s) returning {_COLS}",
                (valor, nome),
            ).fetchone()
            conn.commit()
        return Usuario(*row)


def acesso_liberado(usuario: Usuario) -> bool:
    """True se o usuario pode usar o servico (ativo e dentro da validade)."""
    if not usuario.ativo:
        return False
    if usuario.vencimento is not None and usuario.vencimento < date.today():
        return False
    return True


def ativar(pool, usuario_id: int, dias: int = 30, plano: str = "mensal") -> date:
    """Libera o acesso e estende a validade (por id do usuario). Use no pagamento."""
    venc = date.today() + timedelta(days=dias)
    with pool.connection() as conn:
        conn.execute(
            "update usuarios set ativo = true, plano = %s, vencimento = %s where id = %s",
            (plano, venc, usuario_id),
        )
        conn.commit()
    return venc


def suspender(pool, usuario_id: int):
    """Corta o acesso (pagamento em atraso/cancelado), por id do usuario."""
    with pool.connection() as conn:
        conn.execute("update usuarios set ativo = false where id = %s", (usuario_id,))
        conn.commit()


def checar_e_registrar_uso(pool, usuario: Usuario) -> tuple[bool, int]:
    """Incrementa o uso do dia. Retorna (liberado, mensagens_restantes)."""
    hoje = date.today()
    with pool.connection() as conn:
        row = conn.execute(
            "select mensagens from uso_diario where usuario_id = %s and dia = %s",
            (usuario.id, hoje),
        ).fetchone()
        usado = row[0] if row else 0
        if usado >= usuario.limite_mensagens_dia:
            return False, 0
        conn.execute(
            """insert into uso_diario (usuario_id, dia, mensagens) values (%s,%s,1)
               on conflict (usuario_id, dia) do update set mensagens = uso_diario.mensagens + 1""",
            (usuario.id, hoje),
        )
        conn.commit()
        restante = usuario.limite_mensagens_dia - (usado + 1)
        return True, restante
