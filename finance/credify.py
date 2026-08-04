"""finance/credify.py — cliente da Credify (achar o DECISOR do lead).

Fecha a lacuna da CNPJA. A CNPJA/BrasilAPI dão o telefone da EMPRESA e o NOME do
sócio (CPF mascarado por LGPD), mas nunca o telefone do sócio. A Credify liga as
duas pontas:
  1) Quadro Societário: CNPJ -> sócios com CPF COMPLETO + qualificação.
  2) Telefone por CPF:  CPF  -> telefones (com flag de WhatsApp).
Juntando: CNPJ -> decisor (sócio-administrador) -> telefone dele.

Autenticação (o "não tem chave estática"): POST /auth com ClientID+ClientSecret
devolve um JWT válido por 24h; ele vai como 'Authorization: Bearer <token>' nas
demais chamadas. O token é cacheado em memória e renovado sozinho (na expiração
ou num 401).

Config (env, no Render):
  CREDIFY_CLIENT_ID          (o "Logon" da Credify)
  CREDIFY_CLIENT_SECRET      (a "Senha")
  CREDIFY_ID_QS              (IdConsulta do "Quadro Societário"; default 567)
  CREDIFY_ID_TEL             (IdConsulta do "Telefone por CPF"; default 576)
  CREDIFY_ID_EMAIL           (IdConsulta do "Pesquisa Email PF"; default 359 — achado
                               no catálogo da conta, R$0,36/consulta)
  CREDIFY_BASE_URL           (opcional; default https://api.credify.com.br)

Contrato (confirmado na referência): o corpo dos endpoints de DADOS é aninhado —
  {"Consulta": {"IdConsulta": <id>, "CpfCnpj": <documento>, "TipoPessoa": "F"|"J"}}
e a resposta traz blocos {RESPOSTA:{CODIGO}, TELEFONES:{REGISTRO_1:{...}}} etc.

CUIDADO (LGPD + custo): é dado de PESSOA REAL e consulta PAGA (2 chamadas por
lead). Use só em lead qualificado e com base legal pra abordagem. Tudo é
best-effort: qualquer falha -> retorna None/{} e o lead fica como está.
"""
from __future__ import annotations

import os
import re
import time

import httpx

_DEFAULT_BASE = "https://api.credify.com.br"
_TIMEOUT = 25.0

# cache do token em memória (processo). Renova 1h antes das 24h por segurança.
_TOKEN: str | None = None
_TOKEN_EXP: float = 0.0
_TTL_SEG = 23 * 3600
# formato de corpo do /auth que funcionou (memoizado após o 1º sucesso), pra não
# ficar testando todos toda vez.
_AUTH_SHAPE_OK: int | None = None

# CONFIRMADO num teste real (Render): o /auth aceita {ClientID, ClientSecret} e
# devolve o JWT. Fica em 1º na lista (economiza tentativas); os outros formatos
# ficam de fallback. (u = CREDIFY_CLIENT_ID = "Logon" da Credify; p =
# CREDIFY_CLIENT_SECRET = "Senha".)
_AUTH_SHAPES = [
    lambda u, p: {"ClientID": u, "ClientSecret": p},
    lambda u, p: {"logon": u, "senha": p},
    lambda u, p: {"Logon": u, "Senha": p},
    lambda u, p: {"login": u, "senha": p},
    lambda u, p: {"usuario": u, "senha": p},
    lambda u, p: {"email": u, "senha": p},
]


class CredifyErro(Exception):
    """Erro vindo da Credify (auth ou consulta)."""


def tem_credenciais() -> bool:
    return bool(os.environ.get("CREDIFY_CLIENT_ID") and
               os.environ.get("CREDIFY_CLIENT_SECRET"))


def _base() -> str:
    return (os.environ.get("CREDIFY_BASE_URL") or _DEFAULT_BASE).rstrip("/")


# ---------- contrato da Credify (gateway de consultas) ----------
# Todo endpoint de dados recebe o MESMO envelope aninhado:
#   {"Consulta": {"IdConsulta": <id do produto>, "CpfCnpj": <documento>[, "TipoPessoa": "F"|"J"]}}
# IdConsulta identifica a consulta contratada. Confirmado na referência:
#   Quadro Societário -> 567 (só IdConsulta+CpfCnpj); Telefone por CPF -> 576 (+TipoPessoa "F").
# Dá pra sobrescrever por env (CREDIFY_ID_QS / CREDIFY_ID_TEL) se a conta usar outro.
_ID_QS_PADRAO = "567"
_ID_TEL_PADRAO = "576"


def _id_qs() -> str:
    return (os.environ.get("CREDIFY_ID_QS") or _ID_QS_PADRAO).strip()


_ID_TELREV_PADRAO = "7"   # 'PF Telefone' (reverso) — ID confirmado na conta


def _id_telrev() -> str:
    # Telefone REVERSO ('PF Telefone', /pftelefone): número -> titular. IdConsulta
    # próprio (a conta libera à parte). Default 7; override por CREDIFY_ID_TELREV.
    return (os.environ.get("CREDIFY_ID_TELREV") or _ID_TELREV_PADRAO).strip()


def _id_tel() -> str:
    return (os.environ.get("CREDIFY_ID_TEL") or _ID_TEL_PADRAO).strip()


_ID_EMAIL_PADRAO = "359"  # 'Pesquisa Email PF' — achado no catálogo da conta (R$0,36)


def _id_email() -> str:
    # NÃO confirmado num teste real ainda (diferente de QS/TEL/TELREV acima) — só
    # temos o IdConsulta do catálogo. O caminho HTTP (/pfemailcpf) segue o mesmo
    # padrão de /pftelefonecpf; se a Credify usar outro, ajusta aqui.
    return (os.environ.get("CREDIFY_ID_EMAIL") or _ID_EMAIL_PADRAO).strip()


def _corpo_consulta(id_consulta: str, documento: str, tipo_pessoa: str | None = None) -> dict:
    consulta = {"IdConsulta": str(id_consulta), "CpfCnpj": documento}
    if tipo_pessoa:
        consulta["TipoPessoa"] = tipo_pessoa
    return {"Consulta": consulta}


def _registros(bloco) -> list[dict]:
    """A Credify devolve blocos como {REGISTRO_1:{...}, REGISTRO_2:{...}} (dict) ou
    como lista. Normaliza pra lista de dicts."""
    if isinstance(bloco, dict):
        return [v for v in bloco.values() if isinstance(v, dict)]
    if isinstance(bloco, list):
        return [v for v in bloco if isinstance(v, dict)]
    return []


def _resposta(j) -> dict:
    """Os dados (QUADROSOCIETARIO / TELEFONES / ...) vêm ANINHADOS dentro de
    RESPOSTA. Devolve esse bloco (ou o próprio j se algum endpoint não aninhar)."""
    resp = _pega(j, "RESPOSTA", "resposta", default=None)
    return resp if isinstance(resp, dict) else (j if isinstance(j, dict) else {})


def _codigo_ok(j) -> bool:
    """RESPOSTA.CODIGO: 1=sucesso, 2=não encontrado, 3=erro. Sem o bloco, assume ok
    (deixa o parser tentar)."""
    resp = _pega(j, "RESPOSTA", "resposta", default=None)
    if isinstance(resp, dict):
        cod = str(_pega(resp, "CODIGO", "codigo", default="")).strip()
        return cod in ("", "1")
    return True


# ---------- helpers tolerantes (parsing sem depender do nome exato) ----------

def _pega(d: dict, *chaves, default=None):
    """Primeiro valor não-vazio dentre `chaves` (case-insensitive) do dict."""
    if not isinstance(d, dict):
        return default
    baixo = {str(k).lower(): v for k, v in d.items()}
    for k in chaves:
        v = baixo.get(str(k).lower())
        if v not in (None, "", [], {}):
            return v
    return default


def _lista(d: dict, *chaves) -> list:
    v = _pega(d, *chaves, default=[])
    return v if isinstance(v, list) else ([v] if v else [])


def _so_digitos(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _fmt_tel(ddd: str, numero: str) -> str | None:
    d = _so_digitos(str(ddd or "") + str(numero or ""))
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    return d or None


def _eh_movel(ddd: str, numero: str) -> bool:
    """Celular BR: 11 dígitos (DDD+9) e o 1º dígito do número é 9."""
    d = _so_digitos(str(ddd or "") + str(numero or ""))
    return len(d) == 11 and d[2] == "9"


# ---------- autenticação ----------

def _extrai_token(j) -> str | None:
    """Tira o JWT do retorno do /auth, tolerante ao envelope. A Credify responde
    {"Sucess":bool,"Message":...,"Dados":...}; no sucesso o token costuma vir em
    Dados (string) ou Dados.token."""
    if not isinstance(j, dict):
        return None
    dados = _pega(j, "Dados", "data", "result", "retorno")
    if isinstance(dados, dict):
        t = _pega(dados, "token", "access_token", "accessToken", "jwt", "Token")
        if t:
            return str(t)
    elif isinstance(dados, str) and len(dados) >= 20:
        return dados  # o próprio Dados já é o token
    t = _pega(j, "token", "access_token", "accessToken", "jwt", "Token")
    return str(t) if t else None


def _obter_token(force: bool = False) -> str:
    """Devolve um JWT válido (cacheado). Renova se expirou ou se force=True.

    Testa os formatos de corpo do /auth (ver _AUTH_SHAPES) e memoiza o que
    funcionou. Levanta CredifyErro com a Message do servidor se todos falharem
    (ex.: 'LOGON OU SENHA INVALIDOS' = valores errados, não formato)."""
    global _TOKEN, _TOKEN_EXP, _AUTH_SHAPE_OK
    agora = time.time()
    if not force and _TOKEN and agora < _TOKEN_EXP:
        return _TOKEN
    # .strip() protege contra espaço/quebra colados no valor da env (comum ao colar
    # no painel do Render) — que fariam o /auth ver o campo "vazio".
    cid = (os.environ.get("CREDIFY_CLIENT_ID") or "").strip()
    csec = (os.environ.get("CREDIFY_CLIENT_SECRET") or "").strip()
    if not (cid and csec):
        faltam = " e ".join(n for n, v in (("CREDIFY_CLIENT_ID", cid),
                                           ("CREDIFY_CLIENT_SECRET", csec)) if not v)
        raise CredifyErro(f"faltam credenciais no ambiente: {faltam}")

    ordem = ([_AUTH_SHAPES[_AUTH_SHAPE_OK]] if _AUTH_SHAPE_OK is not None
             else []) + [s for i, s in enumerate(_AUTH_SHAPES)
                         if i != _AUTH_SHAPE_OK]
    ultimo = "sem resposta"
    for shape in ordem:
        corpo = shape(cid, csec)
        try:
            r = httpx.post(_base() + "/auth", json=corpo, timeout=_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            raise CredifyErro(f"rede no /auth: {e}") from e
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            j = {}
        tok = _extrai_token(j)
        if tok:
            _TOKEN = tok
            _TOKEN_EXP = agora + _TTL_SEG
            _AUTH_SHAPE_OK = _AUTH_SHAPES.index(shape)
            return _TOKEN
        corpo_txt = (r.text or "").strip()[:200]
        ultimo = str(_pega(j, "Message", "message", "erro", "Erro",
                           default=(corpo_txt or f"HTTP {r.status_code}")))
        # Erro de SERVIDOR/LIMITE (não é "formato errado"): não adianta testar os
        # outros formatos — só gera mais requisições ao /auth (piora rate limit).
        if r.status_code in (400, 429) or r.status_code >= 500:
            raise CredifyErro(f"/auth {r.status_code}: {ultimo}")
    raise CredifyErro(f"auth recusada em todos os formatos: {ultimo}")


def _post(caminho: str, payload: dict) -> dict:
    """POST autenticado. Renova o token uma vez se tomar 401 (token velho)."""
    url = _base() + caminho
    for tentativa in (1, 2):
        token = _obter_token(force=(tentativa == 2))
        try:
            r = httpx.post(url, json=payload,
                           headers={"Authorization": f"Bearer {token}"},
                           timeout=_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            raise CredifyErro(f"rede em {caminho}: {e}") from e
        if r.status_code == 401 and tentativa == 1:
            continue  # token expirou/inválido -> reautentica e tenta de novo
        if r.status_code >= 300:
            msg = f"{caminho} {r.status_code}: {r.text[:200]}"
            from core.falhas import avaliar_falha_provedor
            avaliar_falha_provedor(msg, servico="Credify")  # avisa admin se auth/quota/credito
            raise CredifyErro(msg)
        return r.json()
    raise CredifyErro(f"{caminho}: 401 mesmo após reautenticar")


# ---------- consultas de dados ----------

def _parse_socios(j) -> list[dict]:
    """Extrai [{nome, cpf, qualificacao}] do retorno da QS (bloco dentro de RESPOSTA)."""
    bloco = _pega(_resposta(j), "QUADROSOCIETARIO", "quadroSocietario", "quadro_societario",
                  "SOCIOS", "socios", "QSA", "qsa", default=None)
    out = []
    for s in _registros(bloco):
        # a PESSOA: NOME + DOCUMENTO (o CPF dela). CPFCNPJ/NOMERAZAO são da EMPRESA.
        nome = _pega(s, "NOME", "nome", "name", "nomeSocio")
        cpf = _so_digitos(_pega(s, "DOCUMENTO", "documento", "CPF", "cpf", ""))
        qual = _pega(s, "QUALIFICACAO", "qualificacao", "CARGO", "cargo", default="")
        if nome or len(cpf) == 11:
            out.append({"nome": nome, "cpf": cpf if len(cpf) == 11 else None,
                        "qualificacao": qual})
    return out


_MSG_CODIGO = {"2": "CNPJ não encontrado na base da Credify",
               "3": "erro na consulta na Credify", "0": "documento obrigatório"}


def _qs_consulta(cnpj: str) -> dict:
    """Consulta o Quadro Societário e devolve {socios, codigo, mensagem, permissao}
    — expõe o MOTIVO quando volta vazio (não encontrado / sem permissão / erro)."""
    d = _so_digitos(cnpj)
    if len(d) != 14:
        return {"socios": [], "codigo": "", "mensagem": "CNPJ inválido", "permissao": False}
    try:
        j = _post("/quadrosocietario", _corpo_consulta(_id_qs(), d))   # QS não exige TipoPessoa
    except CredifyErro as e:
        m = str(e)
        return {"socios": [], "codigo": "", "mensagem": m[:140], "permissao": "permiss" in m.lower()}
    resp = _pega(j, "RESPOSTA", "resposta")
    cod_resp = str(_pega(_pega(j, "CONSULTA", "consulta", default={}) or {},
                         "CODIGORESPOSTA", "codigoResposta", default="") or "")
    if "PERMISS" in (str(resp) + " " + cod_resp).upper():
        return {"socios": [], "codigo": "", "permissao": True,
                "mensagem": "sem permissão nesta consulta"}
    codigo = str(_pega(resp, "CODIGO", "codigo", default="")).strip() if isinstance(resp, dict) else ""
    socios = _parse_socios(j)
    mensagem = "" if socios else _MSG_CODIGO.get(codigo, "quadro societário não retornado")
    return {"socios": socios, "codigo": codigo, "mensagem": mensagem, "permissao": False}


def quadro_societario(cnpj: str) -> list[dict]:
    """CNPJ -> lista de sócios [{nome, cpf, qualificacao}]. [] em falha.

    Envelope: POST /quadrosocietario {"Consulta":{IdConsulta, CpfCnpj}}. IdConsulta da
    consulta 'Quadro Societário' em CREDIFY_ID_QS (default 567)."""
    return _qs_consulta(cnpj)["socios"]


def telefones_por_cpf(cpf: str) -> list[dict]:
    """CPF -> lista de telefones [{numero, ddd, formatado, whatsapp, tipo}]. [] em falha.

    Envelope: POST /pftelefonecpf {"Consulta":{IdConsulta, CpfCnpj, TipoPessoa:"F"}}.
    IdConsulta = CREDIFY_ID_TEL (default 576). Retorno: TELEFONES.REGISTRO_N.
    """
    d = _so_digitos(cpf)
    if len(d) != 11:
        return []
    try:
        j = _post("/pftelefonecpf", _corpo_consulta(_id_tel(), d, "F"))
    except CredifyErro:
        return []
    if not _codigo_ok(j):
        return []
    bloco = _pega(_resposta(j), "TELEFONES", "telefones", "phones", default=None)
    out = []
    for t in _registros(bloco):
        ddd = _pega(t, "DDD", "ddd", "area", default="")
        num = _pega(t, "TELEFONE", "telefone", "numero", "number", default="")
        formatado = _fmt_tel(ddd, num)
        if not formatado:
            continue
        wpp = _pega(t, "WHATSAPP", "whatsapp", "isWhatsapp")
        wpp_bool = (str(wpp).strip().upper() in ("SIM", "S", "TRUE", "1", "YES")
                    if wpp is not None else _eh_movel(ddd, num))
        out.append({
            "numero": num, "ddd": ddd, "formatado": formatado,
            "whatsapp": wpp_bool,
            "tipo": _pega(t, "TIPO_CONTATO_TELEFONE", "tipo", "type", default=None),
        })
    return out


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def emails_por_cpf(cpf: str) -> list[dict]:
    """CPF -> lista de e-mails PESSOAIS [{email, tipo}]. [] em falha.

    Envelope: POST /pfemailcpf {"Consulta":{IdConsulta, CpfCnpj, TipoPessoa:"F"}}.
    IdConsulta = CREDIFY_ID_EMAIL (default 359, 'Pesquisa Email PF' no catálogo).

    AINDA NÃO TESTADO contra a API de verdade (caminho/nomes de campo seguem o
    mesmo padrão de telefones_por_cpf, mas a Credify pode divergir aqui) — se
    voltar sempre [] mesmo em CPF que deveria ter e-mail, é o 1º lugar pra
    conferir (o caminho HTTP ou os nomes tentados em _pega abaixo)."""
    d = _so_digitos(cpf)
    if len(d) != 11:
        return []
    try:
        j = _post("/pfemailcpf", _corpo_consulta(_id_email(), d, "F"))
    except CredifyErro:
        return []
    if not _codigo_ok(j):
        return []
    bloco = _pega(_resposta(j), "EMAILS", "emails", "EMAIL", default=None)
    out, vistos = [], set()
    for e in _registros(bloco):
        email = str(_pega(e, "EMAIL", "email", "endereco", default="") or "").strip().lower()
        if not _EMAIL_RE.match(email) or email in vistos:
            continue
        vistos.add(email)
        out.append({"email": email, "tipo": _pega(e, "TIPO_EMAIL", "tipo", "type", default=None)})
    return out


# ---------- telefone REVERSO: número -> titular ----------

def _titulares(bloco) -> list[dict]:
    """BUSCATELEFONE pode vir como um único titular {CPF,NOME,...} ou como
    {REGISTRO_1:{...}} ou lista. Normaliza pra lista de dicts."""
    if isinstance(bloco, dict):
        if any(str(k).upper().startswith("REGISTRO") for k in bloco):
            return _registros(bloco)
        return [bloco]
    return _registros(bloco)


def titular_por_telefone(ddd, telefone, id_consulta: str | None = None) -> dict:
    """Telefone -> titular (nome, cpf, endereço). Consulta 'PF Telefone' (reversa,
    /pftelefone). IdConsulta em CREDIFY_ID_TELREV (a Credify libera à parte).

    Devolve {ok, titulares:[{nome, cpf, endereco, cidade, uf, tipo}], erro}.
    Best-effort: em falha, ok=False + erro. NÃO persiste o CPF (LGPD)."""
    idc = (id_consulta or _id_telrev()).strip()
    dd = _so_digitos(ddd)
    nn = _so_digitos(telefone)
    if not dd and len(nn) >= 10:          # veio o número inteiro em 'telefone'
        dd, nn = nn[:2], nn[2:]
    if not idc:
        return {"ok": False, "erro": "sem_idconsulta"}
    if len(dd) != 2 or len(nn) < 8:
        return {"ok": False, "erro": "telefone_invalido"}
    corpo = {"Consulta": {"IdConsulta": idc, "Ddd": dd, "Telefone": nn, "TipoPessoa": "F"}}
    try:
        j = _post("/pftelefone", corpo)
    except CredifyErro as e:
        return {"ok": False, "erro": str(e)[:160]}
    if not _codigo_ok(j):
        return {"ok": False, "erro": "titular não encontrado pra esse número"}
    bloco = _pega(_resposta(j), "BUSCATELEFONE", "buscaTelefone", "buscatelefone", default=None)
    out = []
    for t in _titulares(bloco):
        if not isinstance(t, dict):
            continue
        nome = _pega(t, "NOME", "nome")
        cpf = _so_digitos(_pega(t, "CPF", "cpf", "documento", ""))
        if not (nome or len(cpf) == 11):
            continue
        out.append({
            "nome": nome,
            "cpf": cpf if len(cpf) == 11 else None,
            "tplogradouro": _pega(t, "TPLOGRADOURO", "tplogradouro", default=""),
            "logradouro": _pega(t, "LOGRADOURO", "logradouro", default=""),
            "numero": _pega(t, "NUMERO", "numero", default=""),
            "complemento": _pega(t, "COMPLEMENTO", "complemento", default=""),
            "bairro": _pega(t, "BAIRRO", "bairro", default=""),
            "endereco": _pega(t, "ENDERECO", "endereco", default=""),
            "cidade": _pega(t, "CIDADE", "cidade", default=""),
            "uf": _pega(t, "UF", "uf", default=""),
            "cep": _so_digitos(_pega(t, "CEP", "cep", default="")),
            "tipo": _pega(t, "TP", "tp", "tipo", default=""),
        })
    return {"ok": bool(out), "titulares": out, "erro": None if out else "sem_titular"}


# ---------- orquestração: CNPJ -> decisor com telefone ----------

def _escolher_decisor(socios: list[dict]) -> dict | None:
    """Escolhe o sócio-ADMINISTRADOR (o decisor). Fallback: 1º com CPF; senão 1º.

    Função pura (testável sem rede)."""
    if not socios:
        return None
    def _adm(s):
        return "administra" in (s.get("qualificacao") or "").lower()
    adms = [s for s in socios if _adm(s)]
    if adms:
        # entre os administradores, prefere quem já tem CPF completo
        com_cpf = [s for s in adms if s.get("cpf")]
        return (com_cpf or adms)[0]
    com_cpf = [s for s in socios if s.get("cpf")]
    return (com_cpf or socios)[0]


def decisor_com_telefone(cnpj: str) -> dict:
    """CNPJ -> {ok, decisor_nome, decisor_qualificacao, decisor_telefone,
    decisor_whatsapp, decisor_email, telefones}. Best-effort: em falha, {"ok": False, "erro"}.

    NÃO persiste o CPF (dado sensível): ele é usado só pra buscar telefone e
    e-mail e descartado. Guarde no lead apenas nome + telefone + e-mail.
    """
    if not tem_credenciais():
        return {"ok": False, "erro": "sem_credenciais"}
    qs = _qs_consulta(cnpj)
    socios = qs["socios"]
    if not socios:
        return {"ok": False, "erro": "sem_socios",
                "motivo": qs.get("mensagem"), "permissao": qs.get("permissao", False)}
    dec = _escolher_decisor(socios)
    if not dec or not dec.get("cpf"):
        # tem nome mas não veio CPF completo -> não dá pra achar telefone/e-mail
        return {"ok": False, "erro": "sem_cpf_do_decisor",
                "decisor_nome": (dec or {}).get("nome")}
    tels = _ranquear_telefones(telefones_por_cpf(dec["cpf"]))
    principal = tels[0] if tels else None
    emails = emails_por_cpf(dec["cpf"])
    email_principal = emails[0]["email"] if emails else None
    return {
        "ok": bool(principal or email_principal),
        "decisor_nome": dec.get("nome"),
        "decisor_qualificacao": dec.get("qualificacao"),
        "decisor_telefone": principal.get("formatado") if principal else None,
        "decisor_whatsapp": bool(principal.get("whatsapp")) if principal else False,
        "decisor_email": email_principal,
        # lista RICA (ordenada por probabilidade; o 1º vem marcado provavel=True)
        "telefones": [{"formatado": t.get("formatado"), "tipo": t.get("tipo"),
                       "whatsapp": bool(t.get("whatsapp")), "provavel": t.get("provavel", False)}
                      for t in tels if t.get("formatado")],
        "erro": None if (principal or email_principal) else "sem_telefone_e_sem_email",
    }


def decisor_por_lead(cnpj: str = "", telefones: list | None = None) -> dict:
    """Acha o decisor com telefone EM CASCATA — o jeito certo pra lead do Google Maps:
    1) tem CNPJ → sócio-administrador (quadro societário);
    2) sem CNPJ mas com telefone → TITULAR do número (busca reversa) = dono provável
       do comércio; com o CPF dele, puxa todos os números que ele tem.
    Devolve o mesmo formato de decisor_com_telefone, + 'origem' ('cnpj'|'telefone').
    """
    cd = _so_digitos(cnpj or "")
    if len(cd) == 14:
        r = decisor_com_telefone(cd)
        if r.get("decisor_nome"):          # achou o sócio (com ou sem telefone)
            r["origem"] = "cnpj"
            return r
    # fallback: quem é o titular do número que o Maps trouxe
    for tel in (telefones or []):
        d = _so_digitos(tel)
        if len(d) >= 12 and d.startswith("55"):    # tira o código do país (+55) antes do DDD
            d = d[2:]
        if len(d) < 10 or len(d) > 11:
            continue
        rt = titular_por_telefone("", d)
        if not rt.get("ok") or not rt.get("titulares"):
            continue
        tit = rt["titulares"][0]
        nome = tit.get("nome")
        if not nome:
            continue
        tels = _ranquear_telefones(telefones_por_cpf(tit["cpf"])) if tit.get("cpf") else []
        if not tels:                        # sem CPF/telefones extras: fica com o número achado
            tels = [{"formatado": tel, "whatsapp": False, "provavel": True, "tipo": ""}]
        principal = tels[0]
        emails = emails_por_cpf(tit["cpf"]) if tit.get("cpf") else []
        return {
            "ok": True, "origem": "telefone",
            "decisor_nome": nome, "decisor_qualificacao": "Titular do telefone (provável dono)",
            "decisor_telefone": principal.get("formatado"),
            "decisor_whatsapp": bool(principal.get("whatsapp")),
            "decisor_email": emails[0]["email"] if emails else None,
            "telefones": [{"formatado": t.get("formatado"), "tipo": t.get("tipo", ""),
                           "whatsapp": bool(t.get("whatsapp")), "provavel": t.get("provavel", False)}
                          for t in tels if t.get("formatado")],
            "erro": None,
        }
    return {"ok": False, "erro": "sem_cnpj_e_sem_titular"}


def _ranquear_telefones(tels: list[dict]) -> list[dict]:
    """Ordena os telefones do decisor por probabilidade de ser o número dele e marca
    o mais provável (provavel=True no 1º). Sinais, do mais forte pro mais fraco:
    WhatsApp ativo > celular > tipo 'PRINCIPAL'/'CELULAR' > ordem que a Credify devolveu
    (ela costuma trazer o melhor primeiro). Função pura (sem rede)."""
    def _score(idx_t):
        idx, t = idx_t
        s = 0
        if t.get("whatsapp"):
            s += 100
        if _eh_movel(t.get("ddd", ""), t.get("numero", "")):
            s += 40
        tp = (t.get("tipo") or "").upper()
        if "PRINCIPAL" in tp or "CELULAR" in tp:
            s += 15
        s -= idx  # desempate: preserva a ordem original da Credify
        return s
    ordenados = [t for _, t in sorted(enumerate(tels), key=_score, reverse=True)]
    for i, t in enumerate(ordenados):
        t["provavel"] = (i == 0)
    return ordenados
