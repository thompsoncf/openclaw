"""Entrar como: o acesso de suporte às contas dos clientes.

POR QUE EXISTE. Até 13/09/2026 não havia jeito bom de ver a tela de um cliente.
Os três caminhos eram: pedir a senha (que passa a existir no histórico do
WhatsApp), trocar a senha (que deixa o cliente de fora — a conta 16 tem senha
própria em uso) ou virar membro da equipe dele. O terceiro foi o que fizemos na
SUPER FIT, e o remendo aparece na tela de Equipe do dono: um membro "Suporte ZAQ"
pra criar e remover conta a conta — que, no papel `gestor`, nem abre a tela de
Produtos.

POR QUE NÃO UMA SENHA MESTRA, que foi a primeira ideia: ela não diz QUEM entrou,
não dá pra revogar de uma conta só, cai tudo junto se vazar, e precisa ficar
guardada em algum lugar — e senha guardada é senha compartilhada. Este módulo não
cria credencial nenhuma: reaproveita a sessão que o admin já abriu, e por isso não
há o que vazar.

AS DUAS ESCOLHAS DO DONO (13/09/2026), e elas são o desenho:

  MODO LEITURA por padrão. Em sessão de suporte, escrita é barrada no middleware
  (web/app.py) — não é botão escondido em cada tela, que é como se esquece um. O
  destravamento fica pra fase 2; até lá, suporte OLHA.

  60 MINUTOS. Sessão de suporte esquecida aberta é o mesmo risco da senha
  guardada, então ela se encerra sozinha. `expirou()` é consultado a cada
  requisição e a volta é automática.

O QUE FICA GRAVADO: `suporte_acessos` responde "quem entrou nessa conta, quando, e
até quando ficou" — pergunta que hoje nada responde. E a entrada também vira um
evento em `eventos_conta`, que é a auditoria administrativa que já existe e que a
tela do admin já lista.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from finance.relogio import agora

#: Quanto dura uma sessão de suporte. Decisão do dono em 13/09/2026.
DURACAO = timedelta(minutes=60)

#: As chaves que a sessão ganha. Ficam juntas porque `restaurar` apaga todas:
#: esquecer uma deixa a sessão meio-suporte, que é pior que os dois estados.
CHAVES = ("suporte_de", "suporte_de_nome", "suporte_expira", "suporte_acesso_id")

#: O que uma sessão de suporte PODE fazer por POST mesmo em modo leitura. É a
#: própria saída: sem isto, o admin entra e não consegue mais voltar — a rota de
#: volta é um POST (ação com efeito), e a trava barraria justamente ela.
POSTS_LIVRES = ("/admin/voltar", "/sair")


def iniciar(pool, admin_conta_id: int, conta_id: int, motivo: str = "") -> dict:
    """Abre a sessão de suporte no banco. Devolve {acesso_id, expira_em}.

    Grava ANTES de mexer na sessão de propósito: se o insert falhar, o admin
    continua na conta dele e ninguém entrou em lugar nenhum. O contrário —
    entrar e falhar o registro — é o caso que a trilha existe pra impedir.
    """
    expira_em = agora() + DURACAO
    with pool.connection() as c:
        row = c.execute(
            """insert into suporte_acessos (admin_conta_id, conta_id, motivo)
               values (%s, %s, %s) returning id""",
            (admin_conta_id, conta_id, (motivo or "").strip()[:200] or None),
        ).fetchone()
        c.commit()
    return {"acesso_id": int(row[0]), "expira_em": expira_em}


def encerrar(pool, acesso_id: int, por: str = "voltou") -> None:
    """Fecha a linha da trilha. `por` é 'voltou' (clicou) ou 'expirou' (o relógio).

    Tolerante a fechar duas vezes: o `and encerrado_em is null` faz a segunda
    chamada não mexer em nada — a sessão pode expirar no mesmo instante em que a
    pessoa clica em voltar, e isso não é erro.
    """
    with pool.connection() as c:
        c.execute(
            """update suporte_acessos set encerrado_em = now(), encerrado_por = %s
                where id = %s and encerrado_em is null""",
            (por if por in ("voltou", "expirou") else "voltou", acesso_id),
        )
        c.commit()


def aplicar_na_sessao(session, *, admin_conta_id: int, admin_nome: str,
                      conta_id: int, acesso_id: int, expira_em: datetime) -> None:
    """Troca o contexto da sessão pro cliente e marca que isso é suporte.

    `papel='dono'` porque o suporte precisa ver a conta inteira — e é a trava de
    escrita, não o papel, que segura o que ele pode fazer. `membro_id` sai: o
    admin não é membro da equipe do cliente, e deixar um id velho ali faria a
    conta creditar ação de suporte a um vendedor de verdade.
    """
    session["suporte_de"] = int(admin_conta_id)
    session["suporte_de_nome"] = admin_nome or "minha conta"
    session["suporte_expira"] = expira_em.isoformat()
    session["suporte_acesso_id"] = int(acesso_id)
    session["conta_id"] = int(conta_id)
    session["papel"] = "dono"
    session.pop("membro_id", None)


def restaurar_sessao(session) -> int | None:
    """Devolve a sessão pra conta do admin. Retorna o id dela, ou None se não
    havia sessão de suporte (chamar isto à toa é inofensivo)."""
    admin_id = session.get("suporte_de")
    if not admin_id:
        return None
    session["conta_id"] = int(admin_id)
    session["papel"] = "dono"
    session.pop("membro_id", None)
    for k in CHAVES:
        session.pop(k, None)
    return int(admin_id)


def ativo(session) -> dict | None:
    """Os dados da sessão de suporte, ou None se esta não é uma."""
    if not session.get("suporte_de"):
        return None
    return {
        "admin_conta_id": session.get("suporte_de"),
        "volta_para": session.get("suporte_de_nome") or "minha conta",
        "acesso_id": session.get("suporte_acesso_id"),
        "expira_em": session.get("suporte_expira"),
        "restam_min": minutos_restantes(session),
    }


def _expira_em(session) -> datetime | None:
    bruto = session.get("suporte_expira")
    if not bruto:
        return None
    try:
        return datetime.fromisoformat(bruto)
    except (TypeError, ValueError):
        # sessão com lixo no lugar da data: trata como expirada. Falhar fechado
        # aqui devolve o admin pra conta dele; falhar aberto deixaria uma sessão
        # de suporte sem prazo nenhum, que é o que os 60 minutos existem pra evitar.
        return None


def expirou(session) -> bool:
    """Passou dos 60 minutos? Sessão sem prazo legível conta como expirada."""
    if not session.get("suporte_de"):
        return False
    fim = _expira_em(session)
    return fim is None or agora() >= fim


def minutos_restantes(session) -> int:
    fim = _expira_em(session)
    if fim is None:
        return 0
    return max(0, int((fim - agora()).total_seconds() // 60))


def escrita_bloqueada(session, metodo: str, caminho: str) -> bool:
    """Esta requisição é uma escrita que o modo leitura tem que barrar?

    Vale pro painel inteiro, e é o ponto único da regra: a fase 1 não tem
    destravamento, então em sessão de suporte NENHUMA escrita passa — exceto a
    própria saída (POSTS_LIVRES).
    """
    if not session.get("suporte_de"):
        return False
    if (metodo or "GET").upper() in ("GET", "HEAD", "OPTIONS"):
        return False
    return not any(caminho == a or caminho.startswith(a + "/") for a in POSTS_LIVRES)
