"""Cockpit do Vendedor — motor do app mobile (PWA) do vendedor.

Espaço enxuto e robusto SÓ pro vendedor receber e trabalhar o lead fora do painel
completo do Zaq. Login por LINK MÁGICO (sem senha): o gestor gera o link (ou o
vendedor pede pelo próprio e-mail) e o token de `cockpit_acesso` abre a sessão do
membro. Daí ele vê a fila DELE, conversa (pelo chip da empresa, via whatsapp_out),
mexe no funil e fecha (ganho/perdido).

Regras de escopo (defesa central, além da sessão):
  * o vendedor SÓ enxerga/atua em lead com prospeccao.vendedor_id = ele;
  * toda ação revalida a posse no banco (nunca confia no id que veio da tela).

Reaproveita o inbox omnichannel já existente (conversas/mensagens) e o motor de
etapas do funil — o Cockpit é uma porta mobile pra esse mesmo dado, não um silo novo.
"""
from __future__ import annotations

import logging
import secrets
from finance import funil_regua as _fr

# "Em aberto" e "ganho do mês" passam a perguntar pela FASE da etapa em vez do
# literal 'ganho' — ver finance/funil_regua.sql_fechadas. Sem isto, o lead que
# anda pra uma etapa de pós-venda sumiria do placar do vendedor no dia em que
# alguém arrastasse o card, como se a venda tivesse sido desfeita.
_ABERTO_P = "p.status not in " + _fr.sql_encerradas("p")
_ABERTO_T = "status not in " + _fr.sql_encerradas("prospeccao")
_FECHADO_T = "status in " + _fr.sql_fechadas("prospeccao")

from datetime import date as _date, datetime, timedelta, timezone

_log = logging.getLogger(__name__)

_TTL_MIN = 15                      # o link mágico vale 15 min (depois some sozinho)
_PAPEIS_OK = ("vendedor", "gestor", "dono")   # quem pode usar o Cockpit


def _agora():
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------- login mágico

def gerar_token(pool, conta_id: int, membro_id: int) -> str:
    """Cria um token de acesso (1 uso, 15 min) pro membro e devolve a string."""
    token = secrets.token_urlsafe(24)
    with pool.connection() as c:
        c.execute(
            """insert into cockpit_acesso (token, conta_id, membro_id, expira_em)
               values (%s,%s,%s,%s)""",
            (token, conta_id, membro_id, _agora() + timedelta(minutes=_TTL_MIN)))
        c.commit()
    return token


def link_acesso(token: str) -> str:
    from finance.email_sender import _app_url
    return f"{_app_url()}/cockpit/entrar/{token}"


def validar_token(pool, token: str) -> dict | None:
    """Valida o token (existe e não expirou) e devolve {conta_id, membro_id, nome}.
    REUSÁVEL dentro da janela de 15 min: scanner/antivírus de e-mail costuma abrir o
    link antes do vendedor — se fosse 1 uso, queimaria e ele veria "expirado". Aqui o
    que protege é o prazo curto (15 min); `usado_em` guarda só o 1º toque (auditoria).
    Depois de expirar, não valida mais. A sessão (cookie) mantém ele logado."""
    token = (token or "").strip()
    if not token:
        return None
    with pool.connection() as c:
        r = c.execute(
            """update cockpit_acesso set usado_em=coalesce(usado_em, now())
                where token=%s and expira_em > now()
            returning conta_id, membro_id""", (token,)).fetchone()
        if not r:
            return None
        conta_id, membro_id = r[0], r[1]
        m = c.execute("select coalesce(nullif(nome,''), email), papel, ativo "
                      "from membros where id=%s and conta_id=%s",
                      (membro_id, conta_id)).fetchone()
        c.commit()
    if not m or not m[2] or (m[1] or "") not in _PAPEIS_OK:
        return None
    return {"conta_id": conta_id, "membro_id": membro_id, "nome": m[0], "papel": m[1]}


# ------------------------------------------------- "manter conectado" (indeterminado)
# O vendedor marca a caixa e o aparelho dele para de pedir login. Não tem prazo: vale
# até alguém revogar, o membro ser desativado, ou ele apertar Sair. Ver a migração 173
# pra o porquê de ser tabela em vez de um max_age maior no cookie.
#
# O COOKIE guarda o token cru; a TABELA guarda só o sha256 dele. Quem lê o banco não
# consegue se passar por ninguém — mesma razão de `membros.senha_hash`.

LEMBRETE_COOKIE = "zaq_lembrar"


def _hash_lembrete(token: str) -> str:
    import hashlib
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def lembrar_criar(pool, conta_id: int, membro_id: int, aparelho: str = "") -> str | None:
    """Registra este aparelho e devolve o token CRU (só ele serve pro cookie).

    Best-effort: se falhar, o vendedor segue logado pela sessão normal — perder o
    "manter conectado" é chato, derrubar o login por causa disso seria pior."""
    token = secrets.token_urlsafe(32)
    try:
        with pool.connection() as c:
            c.execute(
                """insert into cockpit_lembrete (conta_id, membro_id, token_hash, aparelho)
                   values (%s,%s,%s,%s)""",
                (int(conta_id), int(membro_id), _hash_lembrete(token),
                 (aparelho or "")[:120]))
            c.commit()
    except Exception as e:  # noqa: BLE001
        _log.warning("lembrar_criar falhou (membro %s): %s: %s", membro_id, type(e).__name__, e)
        return None
    return token


def lembrar_validar(pool, token: str) -> dict | None:
    """O cookie ainda vale? Devolve {conta_id, membro_id, papel} ou None.

    RELÊ O MEMBRO a cada uso, de propósito: desativar alguém na Equipe, ou mudar o
    papel dele, tem que cortar o acesso no request seguinte. Se isto confiasse só na
    linha do lembrete, um vendedor demitido continuaria entrando pra sempre — que é
    exatamente o risco de uma sessão sem prazo."""
    token = (token or "").strip()
    if not token:
        return None
    try:
        with pool.connection() as c:
            r = c.execute(
                """update cockpit_lembrete set ultimo_uso=now()
                    where token_hash=%s and revogado_em is null
                returning conta_id, membro_id""", (_hash_lembrete(token),)).fetchone()
            if not r:
                return None
            m = c.execute("select papel, ativo from membros where id=%s and conta_id=%s",
                          (r[1], r[0])).fetchone()
            c.commit()
    except Exception as e:  # noqa: BLE001
        _log.warning("lembrar_validar falhou: %s: %s", type(e).__name__, e)
        return None
    if not m or not m[1] or (m[0] or "") not in _PAPEIS_OK:
        return None
    return {"conta_id": r[0], "membro_id": r[1], "papel": m[0]}


def lembrar_revogar(pool, token: str) -> bool:
    """Encerra ESTE aparelho (o botão Sair). Os outros continuam."""
    token = (token or "").strip()
    if not token:
        return False
    try:
        with pool.connection() as c:
            n = c.execute(
                """update cockpit_lembrete set revogado_em=now()
                    where token_hash=%s and revogado_em is null""",
                (_hash_lembrete(token),)).rowcount
            c.commit()
        return bool(n)
    except Exception as e:  # noqa: BLE001
        _log.warning("lembrar_revogar falhou: %s: %s", type(e).__name__, e)
        return False


def lembrar_revogar_membro(pool, conta_id: int, membro_id: int) -> int:
    """Derruba TODOS os aparelhos do membro — celular perdido, ou desligamento.
    Escopado por conta: ninguém encerra sessão de membro de outra empresa."""
    try:
        with pool.connection() as c:
            n = c.execute(
                """update cockpit_lembrete set revogado_em=now()
                    where conta_id=%s and membro_id=%s and revogado_em is null""",
                (int(conta_id), int(membro_id))).rowcount
            c.commit()
        return n or 0
    except Exception as e:  # noqa: BLE001
        _log.warning("lembrar_revogar_membro falhou: %s: %s", type(e).__name__, e)
        return 0


def tem_senha(pool, conta_id: int, membro_id: int) -> bool:
    """Esta pessoa JÁ TEM COMO ENTRAR com senha? Decide se a tela de "crie sua
    senha" aparece depois do link mágico.

    A pergunta é sobre a PESSOA, não sobre a linha de membro — e é o que faltava.
    A autoridade da identidade é a conta própria: quem tem conta no Zaq com este
    e-mail entra com A SENHA DELA em qualquer empresa onde seja membro
    (`contas.equipe.contextos_de_login`). Perguntar a essa pessoa se ela quer
    "criar uma senha" criaria uma SEGUNDA senha, na linha de membro — as duas
    passariam a funcionar, e trocar uma não mexeria na outra.

    Só quem NÃO tem conta precisa de senha própria no vínculo de membro."""
    try:
        with pool.connection() as c:
            r = c.execute(
                """select coalesce(m.senha_hash,'') <> ''
                        or exists (select 1 from contas ct
                                    where lower(ct.email) = lower(m.email)
                                      and coalesce(ct.senha_hash,'') <> '')
                     from membros m where m.id=%s and m.conta_id=%s""",
                (int(membro_id), int(conta_id))).fetchone()
    except Exception:  # noqa: BLE001
        return True          # na dúvida NÃO insiste em pedir senha
    return bool(r and r[0])


def definir_senha(pool, conta_id: int, membro_id: int, senha_txt: str) -> dict:
    """Grava a senha do membro na MESMA coluna do login web (migração 072), pra o
    vendedor ter uma credencial só — Cockpit e painel."""
    senha_txt = (senha_txt or "").strip()
    if len(senha_txt) < 8:
        return {"ok": False, "erro": "A senha precisa de pelo menos 8 caracteres."}
    if len(senha_txt) > 72:
        return {"ok": False, "erro": "Senha longa demais (máximo 72 caracteres)."}
    from contas import senha as _senha
    try:
        with pool.connection() as c:
            # QUEM TEM CONTA NÃO GANHA UMA SEGUNDA SENHA. A senha da conta é a
            # autoridade e já abre todas as empresas; gravar outra aqui faria as
            # duas valerem, e trocar a da conta não mexeria nesta — foi assim que
            # alguém trocou a senha duas vezes e o app continuou recusando.
            dona = c.execute(
                """select 1 from membros m join contas ct
                          on lower(ct.email) = lower(m.email)
                    where m.id=%s and m.conta_id=%s
                      and coalesce(ct.senha_hash,'') <> ''""",
                (int(membro_id), int(conta_id))).fetchone()
            if dona:
                return {"ok": False, "ja_tem_conta": True,
                        "erro": "Você já tem conta no Zaq com este e-mail — entre com "
                                "a senha dela. Pra trocar, use 'Esqueci minha senha' "
                                "na tela de login do painel."}
            n = c.execute("update membros set senha_hash=%s where id=%s and conta_id=%s",
                          (_senha.hash_senha(senha_txt), int(membro_id), int(conta_id))).rowcount
            c.commit()
    except Exception as e:  # noqa: BLE001
        _log.warning("definir_senha falhou (membro %s): %s", membro_id, e)
        return {"ok": False, "erro": "Não deu pra salvar a senha. Tente de novo."}
    return {"ok": True} if n else {"ok": False, "erro": "Membro não encontrado."}


def membro_por_email(pool, email: str) -> dict | None:
    """Acha o membro ATIVO (vendedor/gestor/dono) por e-mail, pro login self-service.
    Devolve {conta_id, membro_id} do 1º match ou None. Não revela nada pra fora — o
    endpoint sempre responde 'confira seu e-mail'."""
    e = (email or "").strip().lower()
    if "@" not in e:
        return None
    with pool.connection() as c:
        r = c.execute(
            """select conta_id, id from membros
                where lower(email)=%s and ativo and papel in ('vendedor','gestor','dono')
                order by id limit 1""", (e,)).fetchone()
    return {"conta_id": r[0], "membro_id": r[1]} if r else None


# ----------------------------------------------------------------- dados / leituras

def _base_leads_sql() -> str:
    """Leads ABERTOS do vendedor (estágio 'lead', fora de ganho/perdido), com a
    última mensagem e se o agente ainda está no automático (IA) ou é a vez dele."""
    return """
        select p.id, p.empresa, p.cnpj, p.cidade, p.uf, p.temperatura, p.status,
               p.whatsapp, p.telefone,
               cv.id, coalesce(cv.agente_ativo, true), cv.ultima_msg_em,
               lm.texto, lm.autor,
               -- quantas mensagens do cliente ficaram SEM RESPOSTA: as que
               -- chegaram depois da última saída da conversa. É o que vira a
               -- bolinha vermelha no card. Resposta da IA conta como resposta —
               -- se o agente já atendeu, não há nada pendente de gente, e o
               -- vermelho continua raro o bastante pra ser levado a sério.
               -- (Antes havia aqui um `n_in` de 30 dias que ninguém lia.)
               -- o corte é por ID, não por criado_em: `now()` no Postgres é o
               -- início da TRANSAÇÃO, então duas mensagens gravadas na mesma
               -- transação nascem com o mesmo instante e um `>` por tempo
               -- descarta as duas. id é serial, sempre crescente e sem empate.
               (select count(*) from mensagens mm
                 where mm.conversa_id=cv.id and mm.direcao='in'
                   and mm.id > coalesce(
                         (select max(m2.id) from mensagens m2
                           where m2.conversa_id=cv.id and m2.direcao='out'), 0)) as n_pend
          from prospeccao p
          left join conversas cv on cv.prospeccao_id=p.id and cv.conta_id=p.conta_id
          left join lateral (select texto, autor from mensagens
                              where conversa_id=cv.id order by criado_em desc limit 1) lm on true
         where p.conta_id=%s and p.vendedor_id=%s
           and coalesce(p.estagio,'lead')='lead'
           and """ + _ABERTO_P + """
         order by coalesce(cv.ultima_msg_em, p.atualizado_em) desc
         limit 100"""


def total_pendentes(pool, conta_id: int, membro_id: int) -> int:
    """Quantas mensagens de cliente estão sem resposta na carteira INTEIRA do
    vendedor. É o número da bolinha no ícone do app — a mesma conta do selo do card
    (ver `_base_leads_sql`), somada. Vale pro push conseguir marcar o ícone com o app
    fechado, que é quando o vendedor mais precisa ser lembrado."""
    with pool.connection() as c:
        r = c.execute(
            """select coalesce(sum(x.n), 0) from prospeccao p
                 join conversas cv on cv.prospeccao_id=p.id and cv.conta_id=p.conta_id
                 join lateral (
                   select count(*) n from mensagens mm
                    where mm.conversa_id=cv.id and mm.direcao='in'
                      and mm.id > coalesce((select max(m2.id) from mensagens m2
                                             where m2.conversa_id=cv.id and m2.direcao='out'), 0)
                 ) x on true
                where p.conta_id=%s and p.vendedor_id=%s
                  and coalesce(p.estagio,'lead')='lead'
                  and """ + _ABERTO_P + """""",
            (conta_id, membro_id)).fetchone()
    return int(r[0] or 0) if r else 0


def sinal_fila(pool, conta_id: int, membro_id: int) -> str:
    """Assinatura barata da fila do vendedor: quantos leads e qual a mensagem mais
    recente entre eles. A tela compara com o que tem na mão e só recarrega quando
    mudou — em vez de re-renderizar a lista no cliente, que seria muito mais código
    pra um app que é todo form + redirect."""
    with pool.connection() as c:
        r = c.execute(
            """select count(*), coalesce(max(ult.mid), 0) from prospeccao p
                 left join lateral (
                   select max(m.id) mid from conversas cv
                     join mensagens m on m.conversa_id = cv.id
                    where cv.conta_id = p.conta_id and cv.prospeccao_id = p.id
                 ) ult on true
                where p.conta_id=%s and p.vendedor_id=%s
                  and coalesce(p.estagio,'lead')='lead'
                  and """ + _ABERTO_P + """""",
            (conta_id, membro_id)).fetchone()
    return f"{r[0]}:{r[1]}" if r else "0:0"


def leads_do_vendedor(pool, conta_id: int, membro_id: int) -> list[dict]:
    from web.painel_prospeccao import _zap_link, TEMP_COR
    out = []
    with pool.connection() as c:
        rows = c.execute(_base_leads_sql(), (conta_id, membro_id)).fetchall()
    for r in rows:
        ia = bool(r[10])                 # agente_ativo → IA ainda atende
        ult = (r[12] or "").strip().replace("\n", " ")
        autor = r[13] or ""
        if ult:
            pref = "🤖 " if autor == "bot" else ("" if autor == "lead" else "Você: ")
            snip = (pref + ult)[:60]
        else:
            snip = "Sem mensagens ainda"
        out.append({
            "id": r[0], "empresa": r[1] or "Lead", "cnpj": r[2] or "",
            "cidade": r[3] or "", "uf": r[4] or "", "temperatura": r[5] or "frio",
            "temp_cor": TEMP_COR.get(r[5] or "frio", "#5b9bd5"),
            "status": r[6] or "novo",
            "zap": _zap_link(r[7] or r[8] or ""),
            "conversa_id": r[9], "ia": ia, "snip": snip,
            "pend": int(r[14] or 0),
        })
    return out


def _conta_membro(c, conta_id, membro_id):
    return c.execute("select coalesce(nullif(nome,''), email), email, coalesce(whatsapp,''), "
                     "coalesce(cockpit_push_ativo,true), coalesce(cockpit_pausado,false) "
                     "from membros where id=%s and conta_id=%s",
                     (membro_id, conta_id)).fetchone()


def lead_do_vendedor(pool, conta_id: int, membro_id: int, lead_id: int,
                     *, pos_visto: bool = False) -> dict | None:
    """Detalhe de UM lead do vendedor (revalida a posse). Traz a ficha, as etapas do
    funil da conta e o histórico da conversa. None se não é dele.

    `pos_visto` zera o cooldown do push desta conversa — quem ABRIU está em dia, e a
    próxima mensagem do cliente deve tocar na hora em vez de esperar a janela de 10
    min fechar. O cooldown existe pra abafar rajada que ninguém viu, não pra calar
    novidade depois que o vendedor se pôs em dia; sem isso, quanto mais rápido ele
    atendia, mais chance tinha de perder a mensagem seguinte.

    Só a tela passa `pos_visto` — o polling de 8s NÃO passa, senão a conversa aberta
    ficaria zerando o cooldown a cada tique. Resposta da IA também não zera: ela
    responde ao cliente, não põe o vendedor em dia (é de propósito que difere da
    bolinha vermelha, que pergunta 'precisa de gente?' em vez de 'a gente viu?')."""
    from web.painel_prospeccao import _carrega_alvo, _etapas
    alvo = _carrega_alvo(pool, conta_id, lead_id)
    if not alvo or alvo.get("vendedor_id") != membro_id:
        return None
    with pool.connection() as c:
        etapas = [e for e in _etapas(c, conta_id) if e["chave"] not in ("ganho", "perdido")]
        cv = c.execute("select id, coalesce(agente_ativo,true) from conversas "
                       "where prospeccao_id=%s and conta_id=%s order by ultima_msg_em desc limit 1",
                       (lead_id, conta_id)).fetchone()
        if cv and pos_visto:
            c.execute("update conversas set push_avisado_em=null where id=%s", (cv[0],))
            c.commit()
        msgs = []
        if cv:
            # 'asc limit 200' pegava as 200 mais ANTIGAS: numa conversa de 395
            # mensagens o vendedor abria a tela e nunca via o que acabou de chegar.
            # Corta pelas últimas e devolve em ordem de leitura.
            rows = c.execute(
                """select id, direcao, autor, texto, criado_em from (
                     select id, direcao, autor, texto, criado_em from mensagens
                      where conversa_id=%s order by criado_em desc limit 200
                   ) t order by criado_em asc""", (cv[0],)).fetchall()
            for mid, d, autor, texto, quando in rows:
                who = "ia" if autor == "bot" else ("out" if d == "out" else "in")
                # o id vai junto porque a tela do lead se atualiza sozinha e precisa
                # saber a partir de onde pedir o que é novo
                msgs.append({"id": mid, "who": who, "texto": texto or "", "quando": quando})
    alvo["etapas"] = etapas
    alvo["conversa_id"] = cv[0] if cv else None
    alvo["ia"] = bool(cv[1]) if cv else True
    alvo["mensagens"] = msgs
    return alvo


def perfil(pool, conta_id: int, membro_id: int) -> dict:
    """Dados do vendedor + KPIs simples pro cabeçalho do perfil."""
    with pool.connection() as c:
        m = _conta_membro(c, conta_id, membro_id)
        na_fila = c.execute(
            "select count(*) from prospeccao where conta_id=%s and vendedor_id=%s "
            "and coalesce(estagio,'lead')='lead' and " + _ABERTO_T,
            (conta_id, membro_id)).fetchone()[0]
        ganhos = c.execute(
            "select count(*) from prospeccao where conta_id=%s and vendedor_id=%s "
            "and " + _FECHADO_T + " and atualizado_em >= date_trunc('month', now())",
            (conta_id, membro_id)).fetchone()[0]
        atend = c.execute(
            "select count(distinct cv.prospeccao_id) from conversas cv "
            "where cv.conta_id=%s and cv.responsavel_membro_id=%s", (conta_id, membro_id)).fetchone()[0]
    nome, email, wa, push, pausado = (m or ("", "", "", True, False))
    return {"nome": nome or "Vendedor", "email": email or "", "whatsapp": wa or "",
            "push_ativo": bool(push), "pausado": bool(pausado),
            "na_fila": na_fila, "ganhos": ganhos, "atendidos": atend}


# ----------------------------------------------------------------- ações

def _posse(c, conta_id, membro_id, lead_id) -> bool:
    r = c.execute("select vendedor_id from prospeccao where id=%s and conta_id=%s",
                  (lead_id, conta_id)).fetchone()
    return bool(r and r[0] == membro_id)


def enviar_mensagem(pool, conta_id: int, membro_id: int, lead_id: int, texto: str) -> dict:
    """Manda uma mensagem pro lead pelo WhatsApp da empresa (dentro da janela 24h).
    Grava no inbox e ASSUME a conversa (pausa o bot). Revalida a posse."""
    from finance import whatsapp_out
    from web.painel_prospeccao import _add_msg, _conversa_id
    texto = (texto or "").strip()
    if not texto:
        return {"ok": False, "erro": "vazio"}
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        p = c.execute("select whatsapp, telefone from prospeccao where id=%s and conta_id=%s",
                      (lead_id, conta_id)).fetchone()
        numero = (p[0] or p[1] or "") if p else ""
        if not numero:
            return {"ok": False, "erro": "Lead sem número de WhatsApp."}
        # responde pelo mesmo chip que recebeu. `_conversa_id` já é chamado logo
        # abaixo pra gravar no inbox; aqui ele vem antes porque o chip precisa ser
        # decidido ANTES do envio.
        res = whatsapp_out.enviar(
            c, conta_id, numero, texto,
            chip_id=whatsapp_out.chip_da_conversa(
                c, conta_id, _conversa_id(c, conta_id, lead_id, "whatsapp")))
        if not res.get("ok"):
            erros = {"nao_configurado": "WhatsApp não conectado (credencial no Render).",
                     "sem_numero_empresa": "Configure o WhatsApp da empresa na aba Canais.",
                     "numero_invalido": "Número do lead inválido."}
            return {"ok": False, "erro": erros.get(res.get("erro"),
                    "Não consegui enviar (a janela de 24h pode ter fechado).")}
        conv = _conversa_id(c, conta_id, lead_id, "whatsapp")
        _add_msg(c, conv, "whatsapp", "out", "humano", texto, membro_id, res.get("sid"))
        # `push_avisado_em=null` zera o cooldown: quem acabou de responder está EM DIA,
        # e a próxima mensagem do cliente tem que tocar na hora. Ver `pos_visto`.
        c.execute("update conversas set status='pendente', agente_ativo=false, "
                  "push_avisado_em=null where id=%s", (conv,))
        c.commit()
    return {"ok": True}


def entrega_sempre(pool, conta_id: int) -> bool:
    """O Zaq consegue falar com o cliente A QUALQUER HORA nesta conta?

    Só no canal QR. Twilio e Cloud API são a API oficial: fora da janela de 24h
    só sai template aprovado, então existe hora em que o vendedor simplesmente
    não consegue responder pelo Zaq. As três distinções seguem separadas.

    É este portão que decide se o atalho pro WhatsApp aparece: fechar a porta numa
    conta que não entrega sempre deixaria o vendedor sem saída num horário morto.

    TOLERANTE pro lado seguro: falha de leitura responde NÃO — e "não" aqui é
    manter a porta aberta, que é o pior dos dois males só quando tudo funciona.
    """
    try:
        from finance import whatsapp_out as _wo
        with pool.connection() as c:
            return _wo.provedor_da_conta(c, conta_id) == "qr"
    except Exception as e:  # noqa: BLE001
        _log.warning("não deu pra saber o canal da conta %s (%s: %s)",
                     conta_id, type(e).__name__, e)
        return False


def saida_por_fora(pool, conta_id: int, dias: int = 7) -> dict:
    """Quanto do que a empresa mandou ao cliente saiu POR FORA do Zaq.

    Mensagem enviada pelo Zaq nasce com `membro_id`; a que sai do celular chega
    pelo espelho da sessão e vem sem autor nenhum. Então contar `membro_id is
    null` mede exatamente o que a porta fechada quer resolver — e mede o USO,
    não a possibilidade: a lista de aparelhos diz que a porta existe, este número
    diz se alguém passa por ela.

    Vale por si quando a consulta de aparelhos falha (sessão fora do ar), porque
    sai do banco e não depende do WhatsApp responder.

    {dias, total, por_fora, pct} — pct arredondado, 0 quando não houve mensagem.
    """
    try:
        with pool.connection() as c:
            r = c.execute(
                """select count(*),
                          count(*) filter (where m.membro_id is null)
                     from mensagens m join conversas cv on cv.id = m.conversa_id
                    where cv.conta_id=%s and m.canal='whatsapp' and m.direcao='out'
                      and m.autor <> 'agente'
                      and m.criado_em > now() - (%s || ' days')::interval""",
                (conta_id, int(dias))).fetchone()
        total, fora = int(r[0] or 0), int(r[1] or 0)
    except Exception as e:  # noqa: BLE001
        _log.warning("não deu pra medir a saída por fora da conta %s: %s", conta_id, e)
        return {"dias": dias, "total": 0, "por_fora": 0, "pct": 0}
    return {"dias": dias, "total": total, "por_fora": fora,
            "pct": round(100 * fora / total) if total else 0}


def pode_gravar_audio(pool, conta_id: int) -> bool:
    """Esta conta consegue MANDAR áudio pelo Zaq?

    Mesmo portão de canal do `entrega_sempre`, mais o serviço de QR estar de pé.
    Twilio e Cloud API mandam mídia por outro caminho (URL pública e media-id) e
    nenhum dos dois está construído — mostrar o microfone numa conta dessas faria
    o vendedor gravar e o envio falhar depois, que é pior que não ter o botão.

    TOLERANTE: falha de leitura responde não. O pior caso é um microfone que não
    aparece; o outro lado é um áudio que o cliente nunca recebe.
    """
    if not entrega_sempre(pool, conta_id):
        return False
    try:
        from finance import whatsapp_qr as _qr
        return _qr.configurado()
    except Exception as e:  # noqa: BLE001
        _log.warning("não deu pra saber se a conta %s pode gravar áudio (%s: %s)",
                     conta_id, type(e).__name__, e)
        return False


def enviar_audio(pool, conta_id: int, membro_id: int, lead_id: int, dados: bytes,
                 mimetype: str, segundos: int, onda: bytes | None = None) -> dict:
    """Manda o áudio que o vendedor gravou DENTRO do Zaq, e o transcreve.

    A ordem importa: transcreve ANTES de enviar. Os bytes já estão aqui, então não
    há download nenhum — enquanto hoje o áudio gravado no celular só vira texto
    depois de o WhatsApp entregar e o serviço Node baixar de volta. Dá menos
    trabalho ao serviço que o fluxo de hoje, não mais.

    E a mensagem nasce com `membro_id`. É o ganho que sobrevive a tudo: hoje 98%
    do que a Prime manda ao cliente chega sem nome, porque sai do celular.
    """
    from finance import audio_voz as av
    from web.painel_prospeccao import _add_msg, _conversa_id
    if not dados:
        return {"ok": False, "erro": "Áudio vazio."}
    if len(dados) > av.LIMITE_BYTES:
        return {"ok": False, "erro": "Áudio grande demais."}
    segundos = max(1, int(segundos or 1))
    if segundos > av.LIMITE_SEGUNDOS:
        return {"ok": False, "erro": f"Áudio passa de {av.LIMITE_SEGUNDOS}s."}
    if not pode_gravar_audio(pool, conta_id):
        return {"ok": False, "erro": "Esta conta não manda áudio pelo Zaq."}

    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        p = c.execute("select whatsapp, telefone from prospeccao where id=%s and conta_id=%s",
                      (lead_id, conta_id)).fetchone()
    numero = (p[0] or p[1] or "") if p else ""
    if not numero:
        return {"ok": False, "erro": "Lead sem número de WhatsApp."}

    pronto = av.preparar(dados, mimetype)
    if pronto.get("erro"):
        _log.warning("áudio da conta %s não converteu (%s) — vai como veio",
                     conta_id, pronto["erro"])

    # A transcrição é um EXTRA: se o STT falhar, o áudio sai do mesmo jeito. O que
    # não pode é o vendedor ficar sem mandar porque a transcrição caiu.
    texto = ""
    try:
        from core.transcribe import transcritor_se_configurado
        tr = transcritor_se_configurado()
        if tr is not None:
            nome = "audio.ogg" if pronto["mimetype"].startswith("audio/ogg") else "audio.mp4"
            texto = (tr.transcrever(pronto["bytes"], nome) or "").strip()[:4000]
    except Exception as e:  # noqa: BLE001
        _log.warning("não deu pra transcrever o áudio da conta %s: %s", conta_id, e)

    from finance import whatsapp_qr as _qr
    res = _qr.enviar_audio(conta_id, numero, pronto["bytes"], pronto["mimetype"],
                           segundos, onda)
    if not res.get("ok"):
        erros = {"desconectado": "WhatsApp desconectado. Reconecte na aba Canais.",
                 "numero_invalido": "Número do lead inválido.",
                 "qr_indisponivel": "O serviço de WhatsApp está fora do ar.",
                 "audio_vazio_ou_grande": "Áudio grande demais."}
        return {"ok": False, "erro": erros.get(res.get("erro"), "Não consegui enviar o áudio.")}

    # a marca é a MESMA que o serviço Node escreve pro áudio que chega do celular
    # (ver textoDaMsg), então as duas origens ficam iguais na conversa.
    marca = "🎤 Áudio (%d:%02d)" % (segundos // 60, segundos % 60)
    with pool.connection() as c:
        conv = _conversa_id(c, conta_id, lead_id, "whatsapp")
        _add_msg(c, conv, "whatsapp", "out", "humano",
                 (marca + "\n" + texto) if texto else marca, membro_id, res.get("sid"))
        c.execute("update conversas set status='pendente', agente_ativo=false, "
                  "push_avisado_em=null where id=%s", (conv,))
        c.commit()
    return {"ok": True, "texto": texto, "convertido": pronto["convertido"]}


def assumir(pool, conta_id: int, membro_id: int, lead_id: int) -> dict:
    """Tira a conversa do automático (o vendedor passa a responder). Revalida posse."""
    from web.painel_prospeccao import _conversa_id
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        conv = _conversa_id(c, conta_id, lead_id, "whatsapp")
        c.execute("update conversas set agente_ativo=false, status='pendente', "
                  "responsavel_membro_id=%s where id=%s", (membro_id, conv))
        c.commit()
    return {"ok": True}


def _historico(c, conta_id, lead_id, de, para, membro_id) -> None:
    """Linha no histórico do funil. Best-effort de propósito: registrar é
    acessório, mover o card do vendedor é o pedido — e o Cockpit roda no celular
    dele, no meio da rua."""
    # O `with c.transaction()` é SAVEPOINT, e não enfeite: sem ele um erro aqui
    # aborta a transação inteira, o except engole a exceção e o commit lá embaixo
    # vira ROLLBACK calado — o vendedor arrasta o card, a tela diz "ok" e nada
    # muda. Com o savepoint, quem cai é só o registro.
    try:
        from finance import funil_regua as _fr
        with c.transaction():
            _fr.registrar_movimento(c, conta_id, lead_id, de, para, "manual", membro_id)
    except Exception:  # noqa: BLE001
        _log.warning("movimento do funil não registrado (lead %s)", lead_id, exc_info=True)


def mudar_etapa(pool, conta_id: int, membro_id: int, lead_id: int, chave: str) -> dict:
    """Move o lead pra uma etapa do funil (não deixa cair em ganho/perdido por aqui —
    isso é o botão de fechar). Revalida posse e que a etapa é da conta."""
    chave = (chave or "").strip()
    if chave in ("ganho", "perdido"):
        return {"ok": False, "erro": "use_fechar"}
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        ok = c.execute("select 1 from funil_etapas where conta_id=%s and chave=%s",
                       (conta_id, chave)).fetchone()
        if not ok:
            return {"ok": False, "erro": "etapa_invalida"}
        antes = c.execute("select status from prospeccao where id=%s and conta_id=%s",
                          (lead_id, conta_id)).fetchone()
        c.execute("update prospeccao set status=%s, atualizado_em=now() "
                  "where id=%s and conta_id=%s", (chave, lead_id, conta_id))
        _historico(c, conta_id, lead_id, antes[0] if antes else None, chave, membro_id)
        c.commit()
    return {"ok": True}


def criar_lead(pool, conta_id: int, membro_id: int, nome: str, whatsapp: str) -> dict:
    """O vendedor cadastra um lead na mão, do celular. Devolve {ok, lead_id, existia}.

    Por que existe: o cockpit tinha vinte e tantas rotas e NENHUMA criava lead — o
    vendedor só trabalhava o que o rodízio entregava. Quem pegava um contato na rua não
    tinha onde botar, e o app perdia justamente o lead que ninguém mais ia registrar.

    Nasce dele: `vendedor_id` = quem criou, então cai na fila da própria pessoa já
    aberto. `tipo='pf'` é o mesmo palpite do lead que entra pelo WhatsApp (quem manda
    mensagem é uma pessoa); um toque na ficha troca pra empresa.

    NÃO DUPLICA. Número repetido é o modo normal de errar aqui — o vendedor não sabe de
    cor quem já está na base, e um segundo cadastro do mesmo número parte a conversa em
    duas fichas. Se já existe lead com aquele número na conta, devolve o que existe com
    `existia=True` e a tela abre ele em vez de criar outro. A busca usa os 8 finais mais
    a igualdade exata nas duas grafias do celular brasileiro (com e sem o nono dígito) —
    o mesmo casamento de `_conversa_wa_do_contato`, porque '98392961' pode ser o final
    de um celular do 86 e de um do 11.
    """
    from web.painel_prospeccao import _so_digitos, _so_digitos_wa, _wa_equivalentes

    nome = (nome or "").strip()
    if not nome:
        return {"ok": False, "erro": "Diga o nome do contato."}
    # O vendedor digita "(86) 99999-1234", sem DDI. Guardar assim quebraria duas coisas
    # de uma vez: o lead nasceria com número diferente do formato que o WhatsApp entrega
    # (`+55...`, ver o insert de whatsapp_inbound), e o _wa_equivalentes — que é quem
    # acha o repetido — só reconhece as duas grafias do celular brasileiro a partir da
    # forma COM o 55. `_so_digitos_wa` é o normalizador que já existe pra isso.
    if len(_so_digitos(whatsapp)) < 10:
        return {"ok": False, "erro": "WhatsApp incompleto (DDD + número)."}
    digs = _so_digitos_wa(whatsapp)

    alvo8 = digs[-8:]
    with pool.connection() as c:
        achado = c.execute(
            r"""select id from prospeccao
                 where conta_id=%s
                   and right(regexp_replace(coalesce(whatsapp,''), '\D', '', 'g'), 8) = %s
                   and regexp_replace(coalesce(whatsapp,''), '\D', '', 'g') = any(%s)
                 order by id limit 1""",
            (conta_id, alvo8, _wa_equivalentes(digs) or [digs])).fetchone()
        if achado:
            return {"ok": True, "lead_id": achado[0], "existia": True}
        lead_id = c.execute(
            """insert into prospeccao (conta_id, vendedor_id, empresa, contato, whatsapp,
                 tipo, origem, temperatura, status, estagio, criado_por)
               values (%s,%s,%s,%s,%s,'pf','manual_vendedor','morno','novo','lead',%s)
            returning id""",
            (conta_id, membro_id, nome[:250], nome[:250],
             "+" + digs, membro_id)).fetchone()[0]
        c.commit()
    return {"ok": True, "lead_id": lead_id, "existia": False}


def _data_nascimento(texto: str):
    """'AAAA-MM-DD' do <input type=date> -> date. Devolve (date|None, erro|None).

    Vazio é ausência, não erro: campo em branco mantém o que está gravado, como todo
    o resto da ficha. Lixo é ERRO com mensagem, e não um silencioso None — o vendedor
    que digitou uma data no celular precisa saber que ela não entrou; engolir o valor
    e responder "Ficha salva ✓" seria mentir pra ele.
    """
    if not texto:
        return None, None
    try:
        d = _date.fromisoformat(texto[:10])
    except ValueError:
        return None, "Data de nascimento inválida (use dia/mês/ano)."
    # 1900 corta o dedo escorregado no ano (0002, 0202) sem inventar idade mínima;
    # o futuro é sempre engano — ninguém nasce depois de hoje.
    if d.year < 1900 or d > _date.today():
        return None, "Data de nascimento fora do intervalo."
    return d, None


def salvar_ficha(pool, conta_id: int, membro_id: int, lead_id: int, dados: dict) -> dict:
    """Preenche os dados do cliente pela tela do vendedor. Antes isso só existia no
    painel desktop do gestor: o lead entrava por WhatsApp com um número e mais nada, e
    quem estava conversando — que é quem descobre o nome, o CPF e o e-mail — não tinha
    onde anotar. Revalida posse, como toda ação daqui.

    Campo vazio NÃO apaga o que já está gravado: o vendedor abre a ficha no meio da
    conversa pra somar uma informação, não pra recadastrar o lead. Só o documento tem
    caminho de correção (mandar outro por cima), e ele é validado antes de entrar.

    Consequência disso pro WhatsApp: dá pra TROCAR o número por outro, nunca pra apagar
    — deixar em branco mantém o que está lá. É o que se quer, porque o número é a
    identidade da conversa; esvaziá-lo por engano ao salvar a ficha inteira só pra
    corrigir um cargo seria caro."""
    from web.painel_prospeccao import _doc_lead

    def limpo(chave):
        return (dados.get(chave) or "").strip()

    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        atual = c.execute("select tipo from prospeccao where id=%s and conta_id=%s",
                          (lead_id, conta_id)).fetchone()
        tipo_atual = (atual[0] if atual else "") or "pj"

        doc = limpo("documento")
        tipo, cnpj, cpf, erro = _doc_lead(tipo_atual, "", "", doc)
        if erro:
            return {"ok": False, "erro": erro}

        uf = limpo("uf")[:2].upper()
        email = limpo("email").lower()
        cep = "".join(ch for ch in limpo("cep") if ch.isdigit())[:8]
        nasc, erro_nasc = _data_nascimento(limpo("nascimento"))
        if erro_nasc:
            return {"ok": False, "erro": erro_nasc}
        # coalesce: o que veio em branco fica como estava. O documento só é reescrito
        # quando o vendedor digitou algo — senão um "salvar" limparia CPF já cadastrado.
        # `whatsapp` entra junto e é editável: ele é o número por onde a conversa corre,
        # e o vendedor precisa poder corrigir quando o cliente passa outro. Isso NÃO
        # descasa a conversa que já existe — a busca liga por `conversas.prospeccao_id`
        # e, no segundo caminho, por `conversas.contato_ref` (o número de quem mandou
        # de verdade); nenhum dos dois lê esta coluna. Ver _conversa_wa_do_contato.
        campos = [("empresa", limpo("empresa")), ("contato", limpo("contato")),
                  ("cargo", limpo("cargo")), ("telefone", limpo("telefone")),
                  ("whatsapp", limpo("whatsapp")),
                  ("email", email), ("segmento", limpo("segmento")),
                  ("cidade", limpo("cidade")), ("uf", uf), ("obs", limpo("obs")),
                  # endereço: o CEP puxa rua/bairro/cidade/UF na tela (/api/cep), mas o
                  # que chega aqui é sempre o que ESTÁ no formulário — o servidor não
                  # reconsulta. Guardar o CEP é o que permite conferir depois de onde
                  # veio o endereço preenchido sozinho.
                  ("cep", cep), ("endereco", limpo("endereco")),
                  ("numero", limpo("numero")), ("bairro", limpo("bairro"))]
        sets = [f"{k}=coalesce(%s,{k})" for k, _ in campos]
        vals = [v or None for _, v in campos]
        # data não passa pelo mesmo laço: os outros campos são texto e o `or None`
        # resolve; aqui o valor já vem date-ou-None do _data_nascimento.
        sets.append("nascimento=coalesce(%s,nascimento)")
        vals.append(nasc)
        if doc:
            sets += ["tipo=%s", "cnpj=%s", "cpf=%s"]
            vals += [tipo, cnpj, cpf]
        c.execute(f"update prospeccao set {', '.join(sets)}, atualizado_em=now() "
                  "where id=%s and conta_id=%s", (*vals, lead_id, conta_id))
        c.commit()
    return {"ok": True}


def fechar(pool, conta_id: int, membro_id: int, lead_id: int, tipo: str, motivo: str = "") -> dict:
    """Fecha o lead: 'ganho' ou 'perdido'. Em perdido, grava o motivo em obs (timeline).
    Revalida posse."""
    if tipo not in ("ganho", "perdido"):
        return {"ok": False, "erro": "tipo"}
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        antes = c.execute("select status from prospeccao where id=%s and conta_id=%s",
                          (lead_id, conta_id)).fetchone()
        c.execute("update prospeccao set status=%s, atualizado_em=now() "
                  "where id=%s and conta_id=%s", (tipo, lead_id, conta_id))
        _historico(c, conta_id, lead_id, antes[0] if antes else None, tipo, membro_id)
        try:
            c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, resultado, descricao)
                         values (%s,%s,'nota',%s,%s)""",
                      (lead_id, membro_id, "fechado" if tipo == "ganho" else "sem_interesse",
                       ("Ganho 🎉" if tipo == "ganho" else f"Perdido — {motivo or 'sem motivo'}")[:400]))
        except Exception:  # noqa: BLE001 — timeline é best-effort
            pass
        c.commit()
    return {"ok": True}


def devolver_ia(pool, conta_id: int, membro_id: int, lead_id: int) -> dict:
    """Volta a conversa pro automático (reativa o agente). Revalida posse."""
    from web.painel_prospeccao import _conversa_id
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        conv = _conversa_id(c, conta_id, lead_id, "whatsapp")
        c.execute("update conversas set agente_ativo=true, status='aberta' where id=%s", (conv,))
        c.commit()
    return {"ok": True}


def set_push(pool, conta_id: int, membro_id: int, on: bool) -> None:
    with pool.connection() as c:
        c.execute("update membros set cockpit_push_ativo=%s where id=%s and conta_id=%s",
                  (bool(on), membro_id, conta_id))
        c.commit()


# ----------------------------------------------------------------- web push (assinaturas)

def salvar_assinatura(pool, conta_id: int, membro_id: int, sub: dict) -> bool:
    """Guarda (ou atualiza) a assinatura de push do navegador do vendedor."""
    endpoint = (sub or {}).get("endpoint")
    keys = (sub or {}).get("keys") or {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (endpoint and p256dh and auth):
        return False
    with pool.connection() as c:
        c.execute(
            """insert into push_assinaturas (conta_id, membro_id, endpoint, p256dh, auth)
               values (%s,%s,%s,%s,%s)
               on conflict (endpoint) do update
                 set conta_id=excluded.conta_id, membro_id=excluded.membro_id,
                     p256dh=excluded.p256dh, auth=excluded.auth""",
            (conta_id, membro_id, endpoint, p256dh, auth))
        c.commit()
    return True


def remover_assinatura(pool, endpoint: str) -> None:
    if not endpoint:
        return
    with pool.connection() as c:
        c.execute("delete from push_assinaturas where endpoint=%s", (endpoint,))
        c.commit()


def enviar_push(pool, conta_id: int, membro_id: int, titulo: str, corpo: str,
                url: str = "/cockpit", badge: int | None = None) -> int:
    """Dispara push pra TODAS as assinaturas ativas do vendedor (se ele deixou o push
    ligado). Best-effort: nunca levanta; apaga assinatura morta (404/410). Devolve
    quantas foram entregues. Sem chaves VAPID no ambiente, não faz nada."""
    try:
        from finance import webpush
        if not webpush.configurado():
            return 0
        with pool.connection() as c:
            push_on = c.execute("select coalesce(cockpit_push_ativo,true) from membros "
                                "where id=%s and conta_id=%s", (membro_id, conta_id)).fetchone()
            if not push_on or not push_on[0]:
                return 0
            subs = c.execute("select endpoint, p256dh, auth from push_assinaturas "
                             "where membro_id=%s and conta_id=%s", (membro_id, conta_id)).fetchall()
    except Exception as e:  # noqa: BLE001
        _log.info("push: leitura falhou (ok): %s", e)
        return 0
    enviados = 0
    dados = {"title": titulo, "body": corpo, "url": url}
    # o número da bolinha do ícone viaja junto: o service worker acorda com o push
    # mesmo com o app fechado, e é a única chance de marcar o ícone sem o vendedor
    # abrir nada. Só vai quando o chamador soube calcular — `None` é "não mexe".
    if badge is not None:
        dados["badge_n"] = int(badge)
    for endpoint, p256dh, auth in subs:
        sub = {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}
        try:
            from finance import webpush
            if webpush.enviar(sub, dados):
                enviados += 1
        except webpush.PushExpirado:
            remover_assinatura(pool, endpoint)
        except Exception as e:  # noqa: BLE001
            _log.info("push: envio falhou (ok): %s", e)
    return enviados


#: minutos de silêncio por conversa depois de um aviso. O cliente responde em
#: rajada ("Boa tarde" / "08/05/27" / "Debutante" / "19 h" em 40 segundos, que é o
#: padrão real nas conversas), e uma notificação por bolha faria o vendedor
#: desligar o push — matando o recurso inteiro.
PUSH_COOLDOWN_MIN = 10


def avisar_mensagem(pool, conta_id: int, lead_id: int, conversa_id: int, texto: str) -> int:
    """Toca o celular de quem atende quando o CLIENTE responde.

    Até aqui o push só existia num evento: o rodízio atribuindo um lead novo
    (`distribuicao.avisar_vendedor` era o único chamador de `enviar_push` em todo o
    código). Ou seja, da segunda mensagem do cliente em diante era silêncio — e numa
    conta sem rodízio ligado, silêncio desde sempre.

    Quem recebe: o dono do lead. Se o lead não tem dono, todo mundo da conta que
    ASSINOU push no app — assinar é o opt-in explícito, e lead sem dono é de quem
    pegar primeiro. Devolve quantas notificações saíram."""
    texto = (texto or "").strip()
    if not texto:
        return 0
    try:
        from finance import webpush
        if not webpush.configurado():
            return 0
        with pool.connection() as c:
            # O cooldown é decidido no próprio UPDATE, com RETURNING: dois workers
            # processando duas bolhas da mesma rajada disputam a linha e só um sai
            # vencedor. Ler-e-depois-gravar deixaria os dois passarem.
            venceu = c.execute(
                """update conversas set push_avisado_em=now()
                    where id=%s and conta_id=%s
                      and (push_avisado_em is null
                           or push_avisado_em < now() - make_interval(mins => %s))
                 returning id""",
                (conversa_id, conta_id, PUSH_COOLDOWN_MIN)).fetchone()
            if not venceu:
                return 0
            lead = c.execute("select coalesce(empresa,''), vendedor_id from prospeccao "
                             "where id=%s and conta_id=%s", (lead_id, conta_id)).fetchone()
            if not lead:
                c.commit()
                return 0
            empresa, dono = lead[0], lead[1]
            if dono:
                alvos = [dono]
            else:
                alvos = [r[0] for r in c.execute(
                    """select distinct m.id from membros m
                         join push_assinaturas p on p.membro_id=m.id and p.conta_id=m.conta_id
                        where m.conta_id=%s and m.ativo
                          and not coalesce(m.cockpit_pausado, false)""", (conta_id,)).fetchall()]
            c.commit()
    except Exception as e:  # noqa: BLE001
        _log.info("push mensagem: leitura falhou (ok): %s", e)
        return 0

    titulo = (empresa or "Cliente").strip()[:60]
    corpo = texto[:80] + ("…" if len(texto) > 80 else "")
    enviados = 0
    for mid in alvos:
        # deep link: abre a CONVERSA, não a fila. O push do rodízio manda pra
        # /cockpit e obriga o vendedor a procurar de quem era o aviso.
        #
        # O badge é POR VENDEDOR — cada um tem a sua carteira, e num lead sem dono o
        # push vai pra vários. Uma consulta por destinatário, não uma pra todos.
        try:
            n = total_pendentes(pool, conta_id, mid)
        except Exception:  # noqa: BLE001
            n = None       # falhou a conta? manda o push mesmo assim, sem mexer no ícone
        enviados += enviar_push(pool, conta_id, mid, titulo, corpo,
                                f"/cockpit/lead/{lead_id}", badge=n)
    return enviados


def set_pausado(pool, conta_id: int, membro_id: int, on: bool) -> None:
    with pool.connection() as c:
        c.execute("update membros set cockpit_pausado=%s where id=%s and conta_id=%s",
                  (bool(on), membro_id, conta_id))
        c.commit()


# ----------------------------------------------------------------- orçamento / proposta

def catalogo_servicos(pool, conta_id: int) -> list[dict]:
    """Serviços do catálogo da empresa pro vendedor montar o orçamento. Preços em
    REAIS (inteiros) — o snapshot de itens da proposta é em reais (ver web/proposta)."""
    from finance import servicos_catalogo as scat
    out = []
    for s in scat.listar(pool, conta_id):
        out.append({"id": s["id"], "nome": s["nome"], "desc": s.get("descricao", ""),
                    "setup": round((s.get("setup_centavos") or 0) / 100),
                    "mensal": round((s.get("mensal_centavos") or 0) / 100)})
    return out


def _sanear_itens(itens, *, com_desconto: bool = False) -> list[dict]:
    """Snapshot seguro das linhas: nome + setup/mensal em REAIS (inteiros, ≥0).

    `com_desconto` traz o desconto da linha junto — mesmo formato do painel
    (`desc_val` e não `desc`, porque `desc` já é a DESCRIÇÃO do item logo acima).
    Quando False os campos são DESCARTADOS, não zerados: é o portão do nicho, e
    quem não vende serviço não grava desconto nem se mandar no payload.
    """
    out = []
    for it in (itens or [])[:50]:
        nome = (str(it.get("nome") or "")).strip()[:120]
        if not nome:
            continue
        linha = {"nome": nome, "desc": (str(it.get("desc") or "")).strip()[:200],
                 "setup": max(0, int(it.get("setup") or 0)),
                 "mensal": max(0, int(it.get("mensal") or 0))}
        if com_desconto:
            linha["desc_tipo"] = "valor" if (it.get("desc_tipo") or "") == "valor" else "pct"
            linha["desc_val"] = max(0, int(it.get("desc_val") or 0))
        out.append(linha)
    return out


def _sanear_desconto(d) -> dict:
    """O desconto do TOTAL, como a tela manda: {tipo, pct, valor} — `valor` em
    REAIS, igual aos itens. `dsc.quanto_desconta` já limita a base, então aqui só
    se garante que número solto e texto viram zero em vez de explodir."""
    d = d if isinstance(d, dict) else {}
    try:
        pct = max(0.0, min(100.0, float(d.get("pct") or 0)))
    except (TypeError, ValueError):
        pct = 0.0
    try:
        valor = max(0, int(d.get("valor") or 0))
    except (TypeError, ValueError):
        valor = 0
    return {"tipo": "valor" if (d.get("tipo") or "") == "valor" else "pct",
            "pct": pct, "valor": valor}


# ------------------------------------------------------------------ carteira de propostas
STATUS_ORC = ("rascunho", "enviado", "negociando", "aprovada", "fechado", "perdido")
_ROT_ORC = {"rascunho": "Rascunho", "enviado": "Enviada", "negociando": "Negociando",
            "aprovada": "Aprovada", "fechado": "Fechada", "perdido": "Perdida"}


def _link_proposta(token: str) -> str:
    from finance.email_sender import _app_url
    return f"{_app_url()}/proposta/{token}" if token else ""


def _curar_tokens(c, conta_id: int) -> None:
    """Proposta sem token não tem link pro cliente — as antigas foram salvas antes do
    token existir. Mesma auto-cura que painel_servicos.lista já faz."""
    c.execute("""update orcamentos set token = substr(md5(random()::text || id::text
                   || clock_timestamp()::text), 1, 22)
                 where conta_id=%s and token is null""", (conta_id,))


def orcamentos(pool, conta_id: int, *, membro_id: int | None = None,
               status: str = "", vendedor_id: int | None = None) -> list[dict]:
    """As propostas da conta, com o link público pronto pra mandar.

    `membro_id` setado = carteira DELE (o vendedor só enxerga o que criou); None =
    carteira toda (dono/gestor). É a mesma regra de escopo que painel_servicos.lista
    já usa — `orcamentos.criado_por` guarda o membro_id como TEXTO.

    Traz também o lead de origem quando existe (prospeccao.orcamento_id aponta pra
    cá), que é o que permite mandar a proposta na conversa em vez de só por link.
    """
    where = ["o.conta_id=%s"]
    args: list = [conta_id]
    if membro_id:
        where.append("o.criado_por=%s")
        args.append(str(membro_id))
    elif vendedor_id:
        where.append("o.criado_por=%s")
        args.append(str(vendedor_id))
    if status in STATUS_ORC:
        where.append("coalesce(o.status,'rascunho')=%s")
        args.append(status)
    with pool.connection() as c:
        _curar_tokens(c, conta_id)
        c.commit()
        rows = c.execute(
            """select o.id, o.cliente, o.empresa, o.setup_centavos, o.mensal_centavos,
                      coalesce(o.status,'rascunho'), o.token, o.criado_em, o.criado_por,
                      coalesce(o.whatsapp, o.telefone, ''), o.aprovada_por, o.aprovada_em,
                      p.id, coalesce(nullif(m.nome,''), m.email, '—')
                 from orcamentos o
                 left join prospeccao p on p.orcamento_id = o.id and p.conta_id = o.conta_id
                 left join membros m on m.id::text = o.criado_por and m.conta_id = o.conta_id
                where """ + " and ".join(where) + """
                order by o.criado_em desc limit 100""", tuple(args)).fetchall()
    from web.painel_prospeccao import _zap_link_texto
    out = []
    for r in rows:
        link = _link_proposta(r[6])
        out.append({
            "id": r[0], "cliente": r[1] or "", "empresa": r[2] or "",
            "titulo": (r[2] or r[1] or "Proposta"),   # a empresa na frente: é por ela que se procura
            "setup_centavos": int(r[3] or 0), "mensal_centavos": int(r[4] or 0),
            "status": r[5], "status_rot": _ROT_ORC.get(r[5], r[5].title()),
            "token": r[6] or "", "link": link,
            "criado_em": r[7], "vendedor": r[13],
            "zap": _zap_link_texto(r[9], f"Olá! Segue sua proposta 👋\n{link}") if (r[9] and link) else "",
            "aprovada_por": r[10] or "",
            "aprovada_em": r[11],
            "lead_id": r[12],
        })
    return out


def orcamento(pool, conta_id: int, orc_id: int, *, membro_id: int | None = None) -> dict | None:
    """Uma proposta (com os itens), no mesmo escopo de `orcamentos`."""
    import json as _json
    args: list = [orc_id, conta_id]
    dono = ""
    if membro_id:
        dono = " and o.criado_por=%s"
        args.append(str(membro_id))
    with pool.connection() as c:
        _curar_tokens(c, conta_id)
        c.commit()
        r = c.execute(
            """select o.id, o.cliente, o.empresa, o.cnpj, o.segmento,
                      coalesce(o.whatsapp, o.telefone, ''), o.email, o.itens,
                      o.setup_centavos, o.mensal_centavos, coalesce(o.status,'rascunho'),
                      o.token, o.criado_em, o.aprovada_por, o.aprovada_em, o.aprovada_doc,
                      p.id, coalesce(nullif(m.nome,''), m.email, '—'), o.cidade, o.uf
                 from orcamentos o
                 left join prospeccao p on p.orcamento_id = o.id and p.conta_id = o.conta_id
                 left join membros m on m.id::text = o.criado_por and m.conta_id = o.conta_id
                where o.id=%s and o.conta_id=%s""" + dono, tuple(args)).fetchone()
    if not r:
        return None
    itens = r[7]
    if isinstance(itens, str):
        try:
            itens = _json.loads(itens)
        except ValueError:
            itens = []
    link = _link_proposta(r[11])
    from web.painel_prospeccao import _zap_link_texto
    return {
        "id": r[0], "cliente": r[1] or "", "empresa": r[2] or "",
        "titulo": (r[2] or r[1] or "Proposta"),
        "cnpj": r[3] or "", "segmento": r[4] or "", "whatsapp": r[5] or "", "email": r[6] or "",
        "itens": itens or [], "setup_centavos": int(r[8] or 0), "mensal_centavos": int(r[9] or 0),
        "status": r[10], "status_rot": _ROT_ORC.get(r[10], r[10].title()),
        "token": r[11] or "", "link": link, "criado_em": r[12],
        "aprovada_por": r[13] or "", "aprovada_em": r[14], "aprovada_doc": r[15] or "",
        "lead_id": r[16], "vendedor": r[17], "cidade": r[18] or "", "uf": r[19] or "",
        # Mesmo portão que criar_orcamento já usa (ver `entrega_sempre`): onde o Zaq
        # entrega a qualquer hora — o canal QR — o wa.me some, porque mandar por fora
        # tira a conversa de dentro do sistema e o histórico do lead fica pela metade.
        # O detalhe da proposta era o único lugar que ainda oferecia a saída, e por
        # isso o botão continuava aparecendo pra quem está no QR. Onde a entrega NÃO é
        # garantida (Twilio/Cloud fora da janela de 24h) o atalho fica: fechar a porta
        # ali deixaria o vendedor sem nenhuma saída num horário morto.
        "zap": ("" if entrega_sempre(pool, conta_id)
                else (_zap_link_texto(r[5], f"Olá! Segue sua proposta 👋\n{link}")
                      if (r[5] and link) else "")),
    }


# estados que o funil move na mão. 'fechado' fica de fora de propósito: fechar é
# `fechar_contrato`, que gera os títulos a receber — deixar 'fechado' no seletor
# marcaria a proposta como fechada SEM gerar o contrato, e ninguém perceberia.
STATUS_MANUAIS = ("rascunho", "enviado", "negociando", "aprovada", "perdido")


def mudar_status_orcamento(pool, conta_id: int, orc_id: int, novo: str,
                           *, membro_id: int | None = None) -> dict:
    """Move a proposta no funil. `membro_id` setado limita ao que a pessoa criou —
    é o que impede um vendedor de mexer na proposta de outro pela URL.

    Proposta FECHADA não volta atrás por aqui: os títulos a receber já existem, e
    reabrir pra fechar de novo geraria o contrato em dobro. É a mesma regra do
    painel, que também recusa editar orçamento com status 'fechado'.
    """
    if novo not in STATUS_MANUAIS:
        return {"ok": False, "erro": "Status inválido."}
    args: list = [novo, orc_id, conta_id]
    dono = ""
    if membro_id:
        dono = " and criado_por=%s"
        args.append(str(membro_id))
    with pool.connection() as c:
        n = c.execute("update orcamentos set status=%s, atualizado_em=now() "
                      "where id=%s and conta_id=%s and coalesce(status,'rascunho') <> 'fechado'"
                      + dono, tuple(args)).rowcount
        c.commit()
        if not n:
            atual = c.execute("select coalesce(status,'rascunho') from orcamentos "
                              "where id=%s and conta_id=%s", (orc_id, conta_id)).fetchone()
    if not n:
        if atual and atual[0] == "fechado":
            return {"ok": False, "erro": "Essa proposta já virou contrato — não dá pra reabrir."}
        return {"ok": False, "erro": "Proposta não encontrada (ou não é sua)."}
    return {"ok": True, "status": novo, "msg": f"Proposta marcada como {_ROT_ORC.get(novo, novo)} ✓"}


def fechar_contrato(pool, conta_id: int, orc_id: int, *, membro_id: int | None = None) -> dict:
    """Fecha a proposta como CONTRATO: gera os títulos a receber (entrada + a
    mensalidade recorrente) no módulo Empresa.

    Aqui só mora o escopo — quem gera é `vendas.fechar_orcamento`, o mesmo motor do
    botão do painel, que é atômico (o duplo-clique não gera título em dobro) e
    escopado por conta. O que ele NÃO faz é olhar quem criou a proposta: sem a
    checagem abaixo, um vendedor fecharia contrato da proposta de outro pela URL.
    """
    with pool.connection() as c:
        dono = c.execute("select criado_por from orcamentos where id=%s and conta_id=%s",
                         (orc_id, conta_id)).fetchone()
    if not dono:
        return {"ok": False, "erro": "Proposta não encontrada."}
    if membro_id and (dono[0] or "") != str(membro_id):
        return {"ok": False, "erro": "Proposta não encontrada (ou não é sua)."}
    # De quem é a venda: de quem FEZ a proposta, não de quem apertou o botão. Um
    # gestor pode fechar o contrato de um vendedor, e a comissão é do vendedor —
    # é este `criado_por` que vai parar no título e, na baixa, no lançamento.
    try:
        autor = int(str(dono[0]).strip()) if dono[0] else None
    except (TypeError, ValueError):
        autor = None
    from finance import vendas
    r = vendas.fechar_orcamento(pool, conta_id, orc_id, criado_por=autor)
    if not r.get("ok"):
        return r
    quantos = sum(1 for k in ("setup_titulo_id", "mensal_titulo_id") if r.get(k))
    return {**r, "status": "fechado",
            "msg": (f"Contrato fechado ✓ {quantos} título(s) a receber gerado(s)"
                    if quantos else "Contrato fechado ✓")}


def vende_servico(pool, conta_id: int) -> bool:
    """Esta conta vende SERVIÇO? É o portão do desconto no orçamento — o mesmo que
    guarda o painel (web/painel_servicos:150, o `conta[14]`).

    TOLERANTE: se a leitura falhar, responde False. Errar pra menos aqui custa um
    controle que não aparece; errar pra mais gravaria desconto num orçamento de
    quem não deveria tê-lo, e desconto gravado vira título a receber.
    """
    try:
        from finance import empresa as _emp
        return bool(_emp.o_que_vende(pool, conta_id)["servico"])
    except Exception as e:  # noqa: BLE001
        _log.warning("não deu pra saber se a conta %s vende serviço (%s: %s) — "
                     "desconto fica de fora", conta_id, type(e).__name__, e)
        return False


def criar_orcamento(pool, conta_id: int, membro_id: int, lead_id: int, itens,
                    desconto=None) -> dict:
    """Cria a proposta do lead (mesma tabela/token do painel) e devolve o link público
    /proposta/<token> pro vendedor mandar. Revalida a posse do lead. Reusa a página de
    proposta que já existe (o cliente vê com a marca da empresa e aprova online).

    O DESCONTO passa pela MESMA função do painel (`finance.desconto.totais`). Não é
    economia de código: é o motivo de o módulo existir. Duas contas de "quanto é o
    desconto" seriam o começo de dois números, e já custou um orçamento com parcelas
    somando R$ 12.105 contra um total de R$ 9.405.

    E quem faz a conta é o SERVIDOR. O que chega da tela é o que a pessoa digitou
    (o tipo, o percentual, os reais); o total líquido é derivado aqui — senão
    bastaria editar o JSON no navegador pra fechar proposta por qualquer valor.
    """
    import secrets as _secrets
    from web.painel_prospeccao import _zap_link_texto
    from finance.email_sender import _app_url
    from finance import desconto as _dsc
    pode_desconto = vende_servico(pool, conta_id)
    linhas = _sanear_itens(itens, com_desconto=pode_desconto)
    if not linhas:
        return {"ok": False, "erro": "Adicione ao menos um item ao orçamento."}
    dsc_final = _sanear_desconto(desconto) if pode_desconto else _sanear_desconto(None)
    import json as _json
    from finance import vendas as _vendas
    # setup/mensal continuam sendo o BRUTO, como no painel: eles são o preço de
    # tabela do que foi escolhido. O que o desconto muda é `primeiro_ano_centavos`.
    setup_c = sum(x["setup"] for x in linhas) * 100
    mensal_c = sum(x["mensal"] for x in linhas) * 100
    tot = _dsc.totais(linhas, tipo=dsc_final["tipo"], pct=dsc_final["pct"],
                      valor=dsc_final["valor"] * 100)
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        lead = c.execute(
            """select empresa, coalesce(nullif(contato,''), decisor_nome, socio), cnpj,
                      segmento, whatsapp, telefone, email, cidade, uf
                 from prospeccao where id=%s and conta_id=%s""", (lead_id, conta_id)).fetchone()
        try:
            from web.painel_servicos import _garantir_tabela
            _garantir_tabela(c)
        except Exception:  # noqa: BLE001 — colunas já existem em produção
            pass
        token = _secrets.token_urlsafe(16)
        # `primeiro_ano_centavos` NÃO é enfeite e não estava aqui antes: quem gera
        # os títulos lê `coalesce(primeiro_ano_centavos, setup_centavos, 0)`
        # (finance/vendas.py), então sem ele o financeiro cai na soma BRUTA dos
        # itens. Somar desconto sem gravar o líquido faria o cliente assinar por um
        # valor e o sistema cobrar outro — as duas coisas sobem juntas.
        oid = c.execute(
            """insert into orcamentos
                 (conta_id, cliente, empresa, cnpj, segmento, whatsapp, telefone, email,
                  cidade, uf, itens, setup_centavos, mensal_centavos,
                  primeiro_ano_centavos, desconto_tipo, desconto_pct, desconto_centavos,
                  status, criado_por, canal, token, modo)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,
                       %s,%s,%s,%s,'enviado',%s,'cockpit',%s,%s)
               returning id""",
            (conta_id, (lead[1] or None), (lead[0] or None), lead[2], lead[3], lead[4],
             lead[5], lead[6], lead[7], (lead[8] or "")[:2] or None,
             _json.dumps(linhas), setup_c, mensal_c,
             tot["total"], dsc_final["tipo"], dsc_final["pct"], dsc_final["valor"] * 100,
             str(membro_id), token,
             _vendas.modo_do_orcamento(pool, conta_id))).fetchone()[0]
        c.execute("update prospeccao set orcamento_id=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (oid, lead_id, conta_id))
        c.commit()
    link = f"{_app_url()}/proposta/{token}"
    numero = (lead[4] or lead[5] or "")
    msg = f"Olá! Segue sua proposta 👋\n{link}"
    # O ATALHO PRO WHATSAPP SÓ SOBRA ONDE O ZAQ NÃO DÁ CONTA. Na conta que entrega
    # sempre existe o "Enviar na conversa do lead" logo ali, e manter os dois é
    # convidar o vendedor a sair do Zaq — que é justamente o hábito a desfazer.
    return {"ok": True, "id": oid, "token": token, "link": link,
            "zap": ("" if entrega_sempre(pool, conta_id)
                    else (_zap_link_texto(numero, msg) if numero else "")),
            "setup_centavos": setup_c, "mensal_centavos": mensal_c,
            # o líquido volta pra tela poder confirmar o número que o vendedor viu:
            # se divergir do que ela calculou, quem vale é este.
            "total_centavos": tot["total"], "desconto_centavos": tot["desconto_total"]}


def enviar_proposta_conversa(pool, conta_id: int, membro_id: int, lead_id: int, link: str) -> dict:
    """Manda o link da proposta na conversa do lead (WhatsApp da empresa).

    REGISTRA o envio em `orcamento_envios`, como o e-mail já fazia. Sem isso o
    sistema sabia que uma mensagem saiu, mas não que aquela mensagem ERA a proposta
    — e o gatilho `orcamento_enviado`, que hoje lê essa tabela, ficava cego pro
    canal mais usado da casa.

    O orçamento sai do token do próprio link, e não de um parâmetro a mais: as duas
    rotas que chamam isto passam coisas diferentes (uma tem o id, a outra só o
    link), e resolver aqui dentro faz as duas — e qualquer chamador futuro —
    registrarem sem precisar lembrar."""
    r = enviar_mensagem(pool, conta_id, membro_id, lead_id, f"Olá! Segue sua proposta 👋\n{link}")
    _registrar_envio_proposta(pool, conta_id, link, canal="whatsapp",
                              ok=bool(r.get("ok")), erro=str(r.get("erro") or ""),
                              por=str(membro_id or ""))
    return r


def _token_do_link(link: str) -> str:
    """O token no fim de `.../proposta/<token>`. Vazio quando não reconhece —
    registro é anotação, não pode inventar a qual proposta pertence."""
    if not link or "/proposta/" not in link:
        return ""
    return link.rsplit("/proposta/", 1)[1].split("?")[0].split("#")[0].strip()


def _registrar_envio_proposta(pool, conta_id: int, link: str, *, canal: str,
                              ok: bool = True, erro: str = "", por: str = "",
                              destino: str = "") -> None:
    """Anota em `orcamento_envios` que a proposta saiu por este canal.

    Best-effort de ponta a ponta: o registro é o que alimenta o gatilho e a tela de
    histórico, mas ele NÃO pode derrubar o envio. Mensagem entregue e registro
    perdido é um card que não anda; envio que estoura porque a anotação falhou é
    cliente que não recebe a proposta."""
    try:
        token = _token_do_link(link)
        if not token:
            return
        from finance import proposta_email as _pe
        with pool.connection() as c:
            r = c.execute("select id from orcamentos where token=%s and conta_id=%s",
                          (token, conta_id)).fetchone()
        if not r:
            return
        _pe.registrar(pool, conta_id, r[0], destino=destino, remetente_usado="",
                      ok=ok, erro=erro, por=por, canal=canal)
    except Exception as ex:  # noqa: BLE001 — anotação não derruba envio
        _log.warning("proposta: não registrei o envio por %s: %s: %s",
                     canal, type(ex).__name__, ex)


def enviar_proposta_email(pool, conta_id: int, orc_id: int, membro_id: int | None = None) -> dict:
    """Manda a proposta pro e-mail do cliente, do app do vendedor.

    A regra de POR ONDE sai já foi decidida em `finance/proposta_email`: caixa da
    empresa primeiro, remetente do Zaq como reserva com Reply-To. Aqui não se decide
    nada disso de novo — o cockpit só junta o que a tela dele tem e chama. Ter dois
    caminhos de envio seria ter duas respostas pra "de quem é esse e-mail", e a
    resposta do cliente cairia numa caixa diferente dependendo de onde o vendedor
    apertou o botão.

    Registra a tentativa (inclusive a que falhou) na mesma tabela que o painel usa —
    é dela que o gatilho `orcamento_enviado` lê pra saber que a proposta saiu.
    """
    from finance import empresa as _emp
    from finance import proposta_email as _pe

    o = orcamento(pool, conta_id, orc_id, membro_id=membro_id)
    if not o:
        return {"ok": False, "erro": "Proposta não encontrada."}
    destino = (o.get("email") or "").strip()
    if "@" not in destino or "." not in destino.split("@")[-1]:
        return {"ok": False, "erro": "Esse cliente não tem e-mail cadastrado."}
    if not o.get("link"):
        return {"ok": False, "erro": "Essa proposta ainda não tem link público."}

    with pool.connection() as c:
        r = c.execute("select numero, coalesce(modo,'recorrente') from orcamentos "
                      "where id=%s and conta_id=%s", (orc_id, conta_id)).fetchone()
        # `obter_dados_empresa` não traz `contas.nome`, e ele é o último degrau: a
        # conta que ainda não preencheu razão/fantasia precisa assinar com ALGUMA
        # coisa — um e-mail com o topo em branco é pior que um assinado pela pessoa
        titular = (c.execute("select coalesce(nome,'') from contas where id=%s",
                             (conta_id,)).fetchone() or [""])[0]
    numero, modo = (r[0], r[1]) if r else (None, "recorrente")

    d_emp = _emp.obter_dados_empresa(pool, conta_id) or {}
    # nome COMERCIAL: quem recebe é o cliente do salão, e `contas.nome` é o nome de
    # quem abriu a conta — a proposta da Prime Eventos sairia assinada "MANOEL SOARES"
    nome_emp = ((d_emp.get("nome_fantasia") or "").strip()
                or (d_emp.get("razao_social") or "").strip()
                or (titular or "").strip())
    assunto = _pe.assunto_padrao(numero, nome_emp, modo)
    mensagem = _pe.texto_padrao(o.get("cliente") or o.get("empresa"), modo)
    html, texto = _pe.montar(mensagem=mensagem, link=o["link"], numero=numero,
                             empresa=nome_emp, telefone=d_emp.get("telefone") or "",
                             email_empresa=d_emp.get("email_empresa") or "", modo=modo)
    env = _pe.enviar(pool, conta_id, destino=destino, assunto=assunto, html=html,
                     texto=texto, empresa=nome_emp,
                     reply_to=d_emp.get("email_empresa") or "")
    _pe.registrar(pool, conta_id, orc_id, destino=destino,
                  remetente_usado=env.get("remetente", ""), ok=env.get("ok", False),
                  erro=env.get("erro", ""), por=str(membro_id or ""))
    if env.get("ok"):
        return {"ok": True, "destino": destino, "remetente": env.get("remetente", "")}
    return {"ok": False, "erro": env.get("erro") or "Não consegui enviar."}


# ----------------------------------------------------------------- agendar visita

def _maps_link(local: str) -> str:
    from urllib.parse import quote
    return "https://www.google.com/maps/search/?api=1&query=" + quote(local or "") if local else ""


def endereco_empresa(pool, conta_id: int) -> dict:
    """Endereço do SALÃO (a empresa/conta) pro local da visita — vem do cadastro da
    empresa (Empresa → endereço). Lê direto de `contas` (sem depender de nicho).
    {nome, endereco, maps}."""
    with pool.connection() as c:
        r = c.execute(
            "select coalesce(nome_fantasia,''), coalesce(razao_social,''), coalesce(nome,''), "
            "coalesce(endereco,''), coalesce(bairro,''), coalesce(cidade,''), coalesce(uf,'') "
            "from contas where id=%s", (conta_id,)).fetchone()
    if not r:
        return {"nome": "Nosso espaço", "endereco": "", "maps": ""}
    nome = (r[0] or r[1] or r[2] or "Nosso espaço").strip()
    cid_uf = " ".join(x for x in [r[5].strip(), r[6].strip()] if x)
    endereco = " — ".join(p for p in [r[3].strip(), r[4].strip(), cid_uf] if p)
    return {"nome": nome, "endereco": endereco, "maps": _maps_link(endereco or nome)}


def agendar_visita(pool, conta_id: int, membro_id: int, lead_id: int, *, data: str, hora: str,
                   dur_min: int = 60, local: str = "", lembrete_min: int | None = 60,
                   avisar_cliente: bool = True) -> dict:
    """Marca a visita do lead ao espaço: cria o evento na agenda, liga no lead, move o
    lead pra 'qualificado' e (opcional) manda a confirmação com o endereço + o convite
    .ics pro cliente. Revalida a posse do lead."""
    import secrets as _secrets
    from datetime import datetime, timedelta
    from finance import agenda as ag
    from finance.email_sender import _app_url
    try:
        ini = datetime.fromisoformat(f"{data}T{hora}").replace(tzinfo=ag.BRT)
    except Exception:  # noqa: BLE001
        return {"ok": False, "erro": "Data ou hora inválida."}
    dur = max(15, int(dur_min or 60))
    fim = ini + timedelta(minutes=dur)
    esp = endereco_empresa(pool, conta_id)
    local = (local or "").strip() or esp["endereco"] or esp["nome"]
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        lead = c.execute("select coalesce(nullif(contato,''), nullif(empresa,''), 'Cliente'), "
                         "coalesce(whatsapp, telefone, '') from prospeccao where id=%s and conta_id=%s",
                         (lead_id, conta_id)).fetchone()
    quem, numero = (lead[0], lead[1]) if lead else ("Cliente", "")
    descricao = (f"Visita de {quem} ao {esp['nome']}.\nLocal: {local}"
                 + (f"\nMapa: {esp['maps']}" if esp["maps"] else ""))
    lembrete = int(lembrete_min) if lembrete_min else None
    ev = ag.criar_evento(pool, conta_id, f"Visita — {quem}", ini, membro_id=membro_id, fim=fim,
                         local=local, descricao=descricao, lembrete_min=lembrete, tipo="empresa")
    token = _secrets.token_urlsafe(12)
    quando = ini.astimezone(ag.BRT).strftime("%d/%m às %H:%M")
    with pool.connection() as c:
        c.execute("update eventos_agenda set prospeccao_id=%s, ics_token=%s where id=%s and conta_id=%s",
                  (lead_id, token, ev["id"], conta_id))
        try:
            c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, resultado, descricao)
                         values (%s,%s,'visita','agendado',%s)""",
                      (lead_id, membro_id, f"Visita agendada: {quando} — {local}"[:400]))
        except Exception:  # noqa: BLE001
            pass
        # ao agendar a visita, o lead avança pra 'qualificado' (nunca mexe em ganho/perdido)
        c.execute("update prospeccao set status='qualificado', ultimo_contato_em=now(), atualizado_em=now() "
                  "where id=%s and conta_id=%s and " + _ABERTO_T, (lead_id, conta_id))
        c.commit()
    ics_url = f"{_app_url()}/visita/{token}.ics"
    msg = (f"Olá! 👋 Sua visita ao {esp['nome']} está marcada:\n📅 {quando}\n📍 {local}"
           + (f"\n🗺️ {esp['maps']}" if esp["maps"] else "")
           + f"\n📎 Adicione ao seu calendário: {ics_url}\nAté lá! 😊")
    avisado = False
    if avisar_cliente and numero:
        try:
            from finance import whatsapp_out as wo
            from web.painel_prospeccao import _registrar_msg
            with pool.connection() as c:
                res = wo.enviar(c, conta_id, numero, msg)
                if res.get("ok"):
                    _registrar_msg(c, conta_id, lead_id, "whatsapp", "out", "humano", msg, membro_id, res.get("sid"))
                    c.commit()
                    avisado = True
        except Exception:  # noqa: BLE001
            avisado = False
    from web.painel_prospeccao import _zap_link_texto
    # o reenvio pelo WhatsApp só sobra quando o aviso NÃO saiu. Numa conta que
    # entrega sempre e com a confirmação já enviada, o botão seria só uma porta
    # pra fora do Zaq — e mandaria a mesma coisa duas vezes pro cliente.
    sobra_saida = not (avisado and entrega_sempre(pool, conta_id))
    return {"ok": True, "evento_id": ev["id"], "ics_url": ics_url, "quando": quando,
            "local": local, "empresa": esp["nome"], "avisado": avisado,
            "zap": (_zap_link_texto(numero, msg) if (numero and sobra_saida) else "")}


def visita_ics(pool, token: str) -> str | None:
    """.ics público da visita (o cliente abre o link e salva no calendário DELE). Inclui
    VALARM (1 dia e 2h antes) — é o lembrete do cliente. None se o token não existe."""
    token = (token or "").strip()
    if not token:
        return None
    from finance import agenda as ag
    with pool.connection() as c:
        r = c.execute("select id, titulo, inicio, fim, local, descricao, criado_em "
                      "from eventos_agenda where ics_token=%s and status='ativo'", (token,)).fetchone()
    if not r:
        return None
    ev = {"id": r[0], "titulo": r[1], "inicio": r[2], "fim": r[3], "local": r[4],
          "descricao": r[5], "criado_em": r[6]}
    vevent = ag.evento_para_ics(ev)
    alarmes = ("BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Lembrete da visita\r\nTRIGGER:-P1D\r\nEND:VALARM\r\n"
               "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Lembrete da visita\r\nTRIGGER:-PT2H\r\nEND:VALARM")
    vevent = vevent.replace("END:VEVENT", alarmes + "\r\nEND:VEVENT")
    cab = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Zaq//Visita//PT", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    return "\r\n".join(cab + [vevent, "END:VCALENDAR"]) + "\r\n"


# ------------------------------------------------------------------ agenda do vendedor
def agenda_da_conta(pool, conta_id: int, membro_id: int | None = None,
                    so_meus: bool = False, dias: int = 14) -> list[dict]:
    """A agenda dos próximos `dias` — visitas, compromissos e datas SEGURADAS — da
    conta inteira, com quem marcou. Substitui a antiga `visitas_do_vendedor`, que
    filtrava por `membro_id` e `status='ativo'`: o vendedor via só as visitas dele,
    e a data segurada (pré-reserva aguardando sinal) não aparecia pra ninguém no
    app. É exatamente a informação que evita prometer a mesma data duas vezes, e
    quem corre esse risco é o vendedor, na rua.

    `so_meus=True` devolve só os eventos de `membro_id` — o filtro "Meus × Todos"
    da tela. Fica no SQL (e não na tela) porque a tela corta em `dias`; filtrar
    depois faria "Meus" perder eventos quando o time lota a janela.

    Cada item traz `tipo_ev` ('visita' | 'segurada' | 'compromisso'), `autor` (nome
    de quem marcou; '' quando foi o dono titular, que não tem membro) e `minha`
    (o evento é de quem está olhando). O prazo do sinal vai em `prazo` ('2d', '5h',
    'vencido') — no card ele é a diferença entre "data ocupada" e "urgência".
    """
    from finance import agenda as ag
    from web.painel_prospeccao import _zap_link
    hoje = datetime.now(ag.BRT).replace(hour=0, minute=0, second=0, microsecond=0)
    cond = "and e.membro_id=%s " if (so_meus and membro_id) else ""
    args = [conta_id] + ([membro_id] if (so_meus and membro_id) else []) \
        + [hoje, hoje + timedelta(days=max(1, int(dias or 14)))]
    with pool.connection() as c:
        rows = c.execute(
            f"""select e.id, e.titulo, e.inicio, e.local, e.ics_token,
                      e.prospeccao_id, p.empresa, coalesce(p.whatsapp, p.telefone, ''),
                      e.status, e.pre_reserva_ate, e.membro_id,
                      coalesce(nullif(m.nome,''), '')
                 from eventos_agenda e
                 left join prospeccao p on p.id = e.prospeccao_id and p.conta_id = e.conta_id
                 left join membros m on m.id = e.membro_id
                where e.conta_id=%s {cond}and e.status in ('ativo','pre_reservado')
                  and e.inicio >= %s and e.inicio < %s
                order by e.inicio""",
            args).fetchall()
    agora = datetime.now(ag.BRT)
    out = []
    for r in rows:
        ini = r[2].astimezone(ag.BRT) if r[2] else None
        segurada = (r[8] == "pre_reservado")
        # o rótulo do card: visita é o que tem lead pendurado; segurada é a data
        # esperando sinal; o resto (festa confirmada, reunião) é compromisso firme.
        tipo_ev = "segurada" if segurada else ("visita" if r[5] else "compromisso")
        prazo = ""
        if segurada and r[9]:
            horas = (r[9] - agora).total_seconds() / 3600
            prazo = ("vencido" if horas <= 0
                     else f"{max(1, int(horas))}h" if horas < 24
                     else f"{int(horas // 24)}d")
        out.append({
            "id": r[0], "titulo": r[1] or "Visita", "inicio": ini,
            "dia": ini.strftime("%d/%m") if ini else "", "hora": ini.strftime("%H:%M") if ini else "",
            "hoje": bool(ini and ini.date() == hoje.date()),
            "local": r[3] or "", "maps": _maps_link(r[3] or ""),
            "ics_url": f"/visita/{r[4]}.ics" if r[4] else "",
            "lead_id": r[5], "empresa": r[6] or "", "zap": _zap_link(r[7]) if r[7] else "",
            "tipo_ev": tipo_ev, "prazo": prazo,
            "autor": r[11] or "", "minha": bool(membro_id and r[10] == membro_id),
        })
    return out


# ------------------------------------------------------------------ o resultado DELE
def remuneracao(pool, conta_id: int, membro_id: int, periodo: str = "mes") -> dict:
    """O resultado do vendedor no período: o que ENTROU, o que está no funil e a
    comissão dele.

    Dois números, e a diferença importa:

    * `recebido_centavos` / `comissao_centavos` vêm de `finance.comissao` — a MESMA
      conta que o relatório do dono faz. É dinheiro que entrou, e é sobre isso que
      a comissão é paga.
    * `fechado_centavos` continua vindo do funil (`prospeccao.valor_estimado_centavos`
      dos leads ganhos). É previsão do vendedor, não confirmação de ninguém — serve
      pra ele acompanhar o próprio pipeline, e por isso NÃO entra na comissão.

    Antes esses dois eram um só, e o vendedor via uma comissão calculada sobre o
    palpite dele — um número que o dono não reconhecia no relatório.
    """
    from finance import cockpit_dono as cd
    from finance import comissao as com
    ini, fim = cd._range(periodo)
    c_ = com.de_um(pool, conta_id, membro_id, ini.date(), fim.date())
    ordem = cd.placar(pool, conta_id, periodo)          # funil: ranking por R$ fechado
    linha = next((p for p in ordem if p["id"] == membro_id), None)
    posicao = next((i + 1 for i, p in enumerate(ordem) if p["id"] == membro_id), None)
    return {
        # dinheiro que entrou (base da comissão)
        "recebido_centavos": c_["recebido_centavos"],
        "n_vendas": c_["n_vendas"],
        "comissao_pct": c_["comissao_pct"],
        "comissao_centavos": c_["comissao_centavos"] if c_["configurada"] else None,
        # funil (previsão, não conta comissão)
        "fechado_centavos": int((linha or {}).get("rs_centavos") or 0),
        "ganhos": int((linha or {}).get("ganhos") or 0),
        "conversao": (linha or {}).get("conversao") or "—",
        "resp": (linha or {}).get("resp") or "—",
        "fila": int((linha or {}).get("fila") or 0),
        "posicao": posicao, "total_equipe": len(ordem),
    }
