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
  CREDIFY_ID_QS              (IdConsulta da consulta "Quadro Societário")
  CREDIFY_ID_TEL             (IdConsulta do "Telefone por CPF"; default 576)
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
#   {"Consulta": {"IdConsulta": <id do produto>, "CpfCnpj": <documento>, "TipoPessoa": "F"|"J"}}
# O IdConsulta identifica a consulta contratada (por env, por conta). Confirmado
# na referência: PF Telefone-CPF usa IdConsulta 576 e TipoPessoa "F".
_ID_TEL_PADRAO = "576"


def _id_qs() -> str:
    return (os.environ.get("CREDIFY_ID_QS") or "").strip()


def _id_tel() -> str:
    return (os.environ.get("CREDIFY_ID_TEL") or _ID_TEL_PADRAO).strip()


def _corpo_consulta(id_consulta: str, documento: str, tipo_pessoa: str) -> dict:
    return {"Consulta": {"IdConsulta": str(id_consulta), "CpfCnpj": documento,
                         "TipoPessoa": tipo_pessoa}}


def _registros(bloco) -> list[dict]:
    """A Credify devolve blocos como {REGISTRO_1:{...}, REGISTRO_2:{...}} (dict) ou
    como lista. Normaliza pra lista de dicts."""
    if isinstance(bloco, dict):
        return [v for v in bloco.values() if isinstance(v, dict)]
    if isinstance(bloco, list):
        return [v for v in bloco if isinstance(v, dict)]
    return []


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
    cid = os.environ.get("CREDIFY_CLIENT_ID")
    csec = os.environ.get("CREDIFY_CLIENT_SECRET")
    if not (cid and csec):
        raise CredifyErro("faltam CREDIFY_CLIENT_ID / CREDIFY_CLIENT_SECRET")

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
        ultimo = str(_pega(j, "Message", "message", "erro", "Erro",
                           default=f"HTTP {r.status_code}"))
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

def quadro_societario(cnpj: str) -> list[dict]:
    """CNPJ -> lista de sócios [{nome, cpf, qualificacao}]. [] em falha.

    Envelope: POST /quadrosocietario {"Consulta":{IdConsulta, CpfCnpj, TipoPessoa:"J"}}.
    Precisa do IdConsulta da consulta 'Quadro Societário' em CREDIFY_ID_QS. Parser
    tolerante ao retorno (bloco SOCIOS/QUADROSOCIETARIO com REGISTRO_N ou lista).
    """
    d = _so_digitos(cnpj)
    if len(d) != 14 or not _id_qs():
        return []
    try:
        j = _post("/quadrosocietario", _corpo_consulta(_id_qs(), d, "J"))
    except CredifyErro:
        return []
    if not _codigo_ok(j):
        return []
    bloco = _pega(j, "SOCIOS", "socios", "QUADROSOCIETARIO", "quadroSocietario",
                  "quadro_societario", "QSA", "qsa", "PARTNERS", default=None)
    out = []
    for s in _registros(bloco):
        nome = _pega(s, "NOME", "nome", "name", "nomeSocio", "RAZAOSOCIAL", "NOMESOCIO")
        cpf = _so_digitos(_pega(s, "CPF", "cpf", "CPFCNPJ", "CpfCnpj", "documento", "DOCUMENTO", ""))
        qual = _pega(s, "QUALIFICACAO", "qualificacao", "CARGO", "cargo", "qualification", default="")
        if nome or len(cpf) == 11:
            out.append({"nome": nome, "cpf": cpf if len(cpf) == 11 else None,
                        "qualificacao": qual})
    return out


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
    bloco = _pega(j, "TELEFONES", "telefones", "phones", default=None)
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
    decisor_whatsapp, telefones}. Best-effort: em falha, {"ok": False, "erro"}.

    NÃO persiste o CPF (dado sensível): ele é usado só pra buscar o telefone e
    descartado. Guarde no lead apenas nome + telefone.
    """
    if not tem_credenciais():
        return {"ok": False, "erro": "sem_credenciais"}
    socios = quadro_societario(cnpj)
    if not socios:
        return {"ok": False, "erro": "sem_socios"}
    dec = _escolher_decisor(socios)
    if not dec or not dec.get("cpf"):
        # tem nome mas não veio CPF completo -> não dá pra achar o telefone
        return {"ok": False, "erro": "sem_cpf_do_decisor",
                "decisor_nome": (dec or {}).get("nome")}
    tels = telefones_por_cpf(dec["cpf"])
    principal = None
    for t in tels:  # prioriza um celular/WhatsApp
        if t.get("whatsapp"):
            principal = t
            break
    principal = principal or (tels[0] if tels else None)
    return {
        "ok": bool(principal),
        "decisor_nome": dec.get("nome"),
        "decisor_qualificacao": dec.get("qualificacao"),
        "decisor_telefone": principal.get("formatado") if principal else None,
        "decisor_whatsapp": bool(principal.get("whatsapp")) if principal else False,
        "telefones": [t.get("formatado") for t in tels],
        "erro": None if principal else "sem_telefone",
    }
