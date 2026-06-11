"""Usuarios do piloto.

No Telegram, cada pessoa ja' chega com um id unico (telegram_id), entao
a gente identifica o usuario sem precisar de tela de login.
Aqui tambem mora a protecao de custo: teto de mensagens por dia.
"""
from dataclasses import dataclass
from datetime import date


@dataclass
class Usuario:
    id: int
    telegram_id: int
    nome: str | None
    limite_mensagens_dia: int
    ativo: bool


def get_or_create(pool, telegram_id: int, nome: str | None = None) -> Usuario:
    with pool.connection() as conn:
        row = conn.execute(
            "select id, telegram_id, nome, limite_mensagens_dia, ativo from usuarios where telegram_id = %s",
            (telegram_id,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "insert into usuarios (telegram_id, nome) values (%s,%s) "
                "returning id, telegram_id, nome, limite_mensagens_dia, ativo",
                (telegram_id, nome),
            ).fetchone()
            conn.commit()
        return Usuario(*row)


def checar_e_registrar_uso(pool, usuario: Usuario) -> tuple[bool, int]:
    """Incrementa o uso do dia. Retorna (liberado, mensagens_restantes).

    Se ja' bateu o teto do dia, retorna (False, 0) e NAO incrementa.
    """
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
