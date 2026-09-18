# =============================================================================
# ZEARCH WEB - Backend Flask robusto (multi-engine + cache + memória)
# =============================================================================

import os
import re
import json
import time
import random
import threading
import hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from flask import Flask, request, jsonify

# -----------------------------------------------------------------------------
# optional: duckduckgo-search (fallback extra). Não quebra se não estiver instalado
# -----------------------------------------------------------------------------
try:
    from duckduckgo_search import DDGS

    _TEM_DDGS = True
except Exception:
    DDGS = None
    _TEM_DDGS = False


# =============================================================================
# CONFIG
# =============================================================================

app = Flask(__name__)
PORT = int(os.environ.get("PORT", 5000))

# --- HTTP ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

IMPERSONATES = ["chrome131", "chrome124", "chrome120", "safari17_0"]

# Budget total para UMA busca (todos os engines + retries). O Gunicorn está em 120s.
ORCAMENTO_BUSCA_S = 25.0

# Por-tentativa HTTP
TIMEOUT_HTTP = 6  # cada request individual
MAX_TENTATIVAS = 2  # por engine
BACKOFF_MAX_S = 2.0  # teto do sleep entre retries

# Cache
CACHE_TTL_S = 300  # 5 min
CACHE_MAX_ITENS = 200

# --- Memória ---
DB_PATH = Path(os.environ.get("ZEARCH_DB", "database.json"))
DB_LOCK = threading.Lock()
JANELA_RECENTES_SEGUNDOS = 5 * 60


# =============================================================================
# UTILITÁRIOS GERAIS
# =============================================================================


def _agora() -> datetime:
    return datetime.now()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _hash_query(q: str) -> str:
    return hashlib.sha1(q.strip().lower().encode("utf-8")).hexdigest()


def _log(msg: str):
    try:
        print(msg, flush=True)
    except Exception:
        pass


# =============================================================================
# DATABASE (memória persistente)
# =============================================================================


def db_carregar() -> dict:
    if not DB_PATH.exists():
        return {
            "meta": {
                "criado_em": _iso(_agora()),
                "ultima_atualizacao": _iso(_agora()),
            },
            "historico": [],
            "recentes": [],
        }
    try:
        return json.loads(DB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"meta": {}, "historico": [], "recentes": []}


def db_salvar(db: dict):
    db.setdefault("meta", {})
    db["meta"]["ultima_atualizacao"] = _iso(_agora())
    ordenado = {
        "meta": db.get("meta", {}),
        "historico": db.get("historico", []),
        "recentes": db.get("recentes", []),
    }
    try:
        DB_PATH.write_text(
            json.dumps(ordenado, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        _log(f"⚠️  db_salvar falhou: {e}")


def db_limpar():
    db = {
        "meta": {"criado_em": _iso(_agora()), "ultima_atualizacao": _iso(_agora())},
        "historico": [],
        "recentes": [],
    }
    db_salvar(db)
    return db


def tempo_relativo(iso_str: str) -> str:
    try:
        quando = datetime.fromisoformat(iso_str)
    except Exception:
        return "há muito tempo"
    secs = int((_agora() - quando).total_seconds())
    if secs < 60:
        return f"há {secs} segundos"
    if secs < 3600:
        return f"há {secs // 60} minutos"
    if secs < 86400:
        return f"há {secs // 3600} horas"
    return f"há {secs // 86400} dias"


def _atualizar_recentes(db: dict):
    agora = _agora()
    ficam, vao = [], []
    for item in db.get("recentes", []):
        try:
            quando = datetime.fromisoformat(item.get("enviada_em", ""))
        except Exception:
            vao.append(item)
            continue
        if (agora - quando).total_seconds() <= JANELA_RECENTES_SEGUNDOS:
            ficam.append(item)
        else:
            vao.append(item)
    if vao:
        db["historico"] = vao + db.get("historico", [])
    db["recentes"] = ficam


def registrar_pergunta(pergunta: str) -> dict:
    with DB_LOCK:
        db = db_carregar()
        _atualizar_recentes(db)
        agora = _agora()
        item = {
            "id": int(agora.timestamp() * 1000),
            "pergunta": pergunta,
            "enviada_em": _iso(agora),
            "enviada_em_humano": agora.strftime("%Y-%m-%d %H:%M:%S"),
            "tempo_relativo": "agora",
        }
        db["recentes"] = [item] + db.get("recentes", [])
        db_salvar(db)
    return item


def registrar_resposta(pergunta_id: int, resposta: str):
    with DB_LOCK:
        db = db_carregar()
        for lista in ("recentes", "historico"):
            for item in db.get(lista, []):
                if item.get("id") == pergunta_id:
                    item["resposta"] = resposta
                    item["respondida_em"] = _iso(_agora())
                    break
        db_salvar(db)


def Relembrar(pergunta_atual: str) -> str:
    with DB_LOCK:
        db = db_carregar()
        _atualizar_recentes(db)
        db_salvar(db)

    recentes = db.get("recentes", [])
    historico = db.get("historico", [])

    for item in recentes + historico:
        item["tempo_relativo"] = tempo_relativo(item.get("enviada_em", ""))

    linhas = ["=== CONVERSAS RECENTES (últimos 5 minutos) ==="]
    if not recentes:
        linhas.append("(nenhuma)")
    else:
        for item in recentes:
            linhas.append(
                f"[{item.get('tempo_relativo','')}] USUÁRIO: {item.get('pergunta','')}"
            )
            if item.get("resposta"):
                linhas.append(f"           VOCÊ: {item['resposta']}")

    linhas.append("")
    linhas.append("=== HISTÓRICO ===")
    if not historico:
        linhas.append("(sem histórico)")
    else:
        for item in historico[:20]:
            linhas.append(
                f"[{item.get('tempo_relativo','')}] USUÁRIO: {item.get('pergunta','')}"
            )
            if item.get("resposta"):
                linhas.append(f"           VOCÊ: {item['resposta']}")

    linhas.append("")
    linhas.append("=== PERGUNTA ATUAL ===")
    linhas.append(f"USUÁRIO: {pergunta_atual}")
    return "\n".join(linhas)


# =============================================================================
# CACHE EM MEMÓRIA (evita re-buscar a mesma query)
# =============================================================================


class CacheLRU:
    def __init__(self, ttl_s: int, max_itens: int):
        self.ttl = ttl_s
        self.max = max_itens
        self._d = {}
        self._lock = threading.Lock()

    def get(self, chave: str):
        with self._lock:
            item = self._d.get(chave)
            if not item:
                return None
            valor, expira = item
            if time.time() > expira:
                self._d.pop(chave, None)
                return None
            return valor

    def set(self, chave: str, valor):
        with self._lock:
            if len(self._d) >= self.max:
                # remove o mais antigo (aproximação)
                try:
                    mais_antigo = min(self._d.items(), key=lambda kv: kv[1][1])[0]
                    self._d.pop(mais_antigo, None)
                except Exception:
                    self._d.clear()
            self._d[chave] = (valor, time.time() + self.ttl)


CACHE = CacheLRU(CACHE_TTL_S, CACHE_MAX_ITENS)


# =============================================================================
# ENGINE 1 — DDG LITE via curl_cffi
# =============================================================================

DDG_LITE_URL = "https://lite.duckduckgo.com/lite/?q={q}"


def _decodificar_link(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" not in href:
        return href
    try:
        qs = parse_qs(urlparse(href).query)
        uddg = qs.get("uddg", [None])[0]
        if uddg:
            return unquote(uddg)
    except Exception:
        pass
    return href


def _extrair_resultados_lite(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    resultados = []
    links = soup.select("a.result-link")
    if not links:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and "duckduckgo" not in href:
                links.append(a)

    for a in links:
        titulo = a.get_text(" ", strip=True)
        url = _decodificar_link(a.get("href", ""))
        if not titulo or not url.startswith("http"):
            continue
        snippet, dominio = "", ""
        tr = a.find_parent("tr")
        if tr:
            tr_next = tr.find_next_sibling("tr")
            if tr_next:
                td = tr_next.select_one("td.result-snippet")
                if td:
                    snippet = td.get_text(" ", strip=True)
            dom = tr.select_one(".link-text")
            if dom:
                dominio = dom.get_text(" ", strip=True)
        resultados.append(
            {"titulo": titulo, "url": url, "dominio": dominio, "snippet": snippet}
        )

    vistos, unicos = set(), []
    for r in resultados:
        if r["url"] not in vistos:
            vistos.add(r["url"])
            unicos.append(r)
    return unicos


def _buscar_ddg_lite(query: str, deadline: float) -> list:
    url = DDG_LITE_URL.format(q=quote_plus(query))
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        if time.time() > deadline:
            _log("   ⏱️  ddg-lite: orçamento esgotado")
            return []
        try:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
                "Referer": "https://lite.duckduckgo.com/",
            }
            r = curl_requests.get(
                url,
                headers=headers,
                impersonate=random.choice(IMPERSONATES),
                timeout=TIMEOUT_HTTP,
                allow_redirects=True,
            )
            if r.status_code in (202, 429) or len(r.text) < 500:
                _log(
                    f"   ⚠️  ddg-lite {r.status_code} / {len(r.text)}b tent {tentativa}"
                )
                time.sleep(min(BACKOFF_MAX_S, 0.8 * tentativa))
                continue
            res = _extrair_resultados_lite(r.text)
            if not res:
                _log(f"   ⚠️  ddg-lite 0 resultados tent {tentativa}")
                time.sleep(min(BACKOFF_MAX_S, 0.8 * tentativa))
                continue
            _log(f"   ✅ ddg-lite {len(res)} resultados")
            return res
        except Exception as e:
            _log(f"   ⚠️  ddg-lite {type(e).__name__}: {str(e)[:120]}")
            time.sleep(min(BACKOFF_MAX_S, 0.8 * tentativa))
    return []


# =============================================================================
# ENGINE 2 — DDG HTML via curl_cffi
# =============================================================================

DDG_HTML_URL = "https://html.duckduckgo.com/html/?q={q}"


def _extrair_resultados_html(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for div in soup.select(".result, .web-result"):
        a = div.select_one("a.result__a")
        if not a:
            continue
        titulo = a.get_text(" ", strip=True)
        url = _decodificar_link(a.get("href", ""))
        if not titulo or not url.startswith("http"):
            continue
        sn = div.select_one(".result__snippet")
        snippet = sn.get_text(" ", strip=True) if sn else ""
        dom = ""
        try:
            dom = urlparse(url).netloc
        except Exception:
            pass
        out.append({"titulo": titulo, "url": url, "dominio": dom, "snippet": snippet})
    vistos, unicos = set(), []
    for r in out:
        if r["url"] not in vistos:
            vistos.add(r["url"])
            unicos.append(r)
    return unicos


def _buscar_ddg_html(query: str, deadline: float) -> list:
    url = DDG_HTML_URL.format(q=quote_plus(query))
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        if time.time() > deadline:
            return []
        try:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
                "Referer": "https://duckduckgo.com/",
            }
            r = curl_requests.post(
                url,
                headers=headers,
                data={"q": query, "b": ""},
                impersonate=random.choice(IMPERSONATES),
                timeout=TIMEOUT_HTTP,
                allow_redirects=True,
            )
            if r.status_code in (202, 429) or len(r.text) < 500:
                _log(
                    f"   ⚠️  ddg-html {r.status_code} / {len(r.text)}b tent {tentativa}"
                )
                time.sleep(min(BACKOFF_MAX_S, 0.8 * tentativa))
                continue
            res = _extrair_resultados_html(r.text)
            if not res:
                _log(f"   ⚠️  ddg-html 0 resultados tent {tentativa}")
                time.sleep(min(BACKOFF_MAX_S, 0.8 * tentativa))
                continue
            _log(f"   ✅ ddg-html {len(res)} resultados")
            return res
        except Exception as e:
            _log(f"   ⚠️  ddg-html {type(e).__name__}: {str(e)[:120]}")
            time.sleep(min(BACKOFF_MAX_S, 0.8 * tentativa))
    return []


# =============================================================================
# ENGINE 3 — duckduckgo_search (biblioteca, se disponível)
# =============================================================================


def _buscar_ddgs(query: str, deadline: float) -> list:
    if not _TEM_DDGS:
        return []
    if time.time() > deadline:
        return []
    try:
        with DDGS(timeout=TIMEOUT_HTTP) as ddgs:
            res = list(ddgs.text(query, region="br-pt", max_results=10))
        out = []
        for r in res:
            href = r.get("href") or r.get("url") or ""
            if not href:
                continue
            out.append(
                {
                    "titulo": r.get("title", ""),
                    "url": href,
                    "dominio": urlparse(href).netloc,
                    "snippet": r.get("body", ""),
                }
            )
        if out:
            _log(f"   ✅ ddgs {len(out)} resultados")
        return out
    except Exception as e:
        _log(f"   ⚠️  ddgs {type(e).__name__}: {str(e)[:120]}")
        return []


# =============================================================================
# ORQUESTRADOR DE BUSCA (com fallback e cache)
# =============================================================================


def buscar(query: str) -> dict:
    """
    Retorna:
      {"ok": bool, "resultados": [...], "engine": "ddg-lite|ddg-html|ddgs|none",
       "erro": str|None, "cache": bool}
    """
    chave = _hash_query(query)
    cacheado = CACHE.get(chave)
    if cacheado:
        _log(f"   💾 cache hit ({len(cacheado.get('resultados', []))} resultados)")
        return {**cacheado, "cache": True}

    deadline = time.time() + ORCAMENTO_BUSCA_S
    _log(f"🌐 Buscando: {query}  (orçamento {ORCAMENTO_BUSCA_S}s)")

    tentativas = [
        ("ddg-lite", _buscar_ddg_lite),
        ("ddg-html", _buscar_ddg_html),
        ("ddgs", _buscar_ddgs),
    ]

    for nome, fn in tentativas:
        if time.time() > deadline:
            _log(f"   ⏱️  orçamento esgotado antes de {nome}")
            break
        try:
            res = fn(query, deadline)
        except Exception as e:
            _log(f"   ❌ {nome} crashou: {type(e).__name__}: {str(e)[:120]}")
            res = []
        if res:
            payload = {"ok": True, "resultados": res, "engine": nome, "erro": None}
            CACHE.set(chave, payload)
            return {**payload, "cache": False}

    return {
        "ok": False,
        "resultados": [],
        "engine": "none",
        "erro": "Nenhuma engine retornou resultados (timeout/bloqueio).",
        "cache": False,
    }


# =============================================================================
# PROMPT
# =============================================================================

PROMPT_SISTEMA = """Você é o ZEARCH, um assistente conversacional com MEMÓRIA PERSISTENTE.

REGRAS:
1. Você recebe três blocos: CONVERSAS RECENTES, HISTÓRICO e PERGUNTA ATUAL.
2. Cada item tem tempo relativo entre colchetes ([há 3 segundos], [há 2 minutos]).
3. Foque na PERGUNTA ATUAL. Use o contexto para manter coerência.
4. NUNCA cite literalmente o contexto. Responda naturalmente.
5. Se o contexto não tiver nada relevante, apenas responda normalmente.
6. Estilo: direto, natural, sem enrolação.
"""


# =============================================================================
# FORMATAÇÃO DE TEXTO (para resposta)
# =============================================================================


def _formatar_resultados(query: str, resultados: list, engine: str) -> str:
    linhas = [
        "=" * 70,
        "ZEARCH WEB",
        "=" * 70,
        f"Query : {query}",
        f"Data  : {_agora().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Engine: {engine}",
        f"Total : {len(resultados)}",
        "=" * 70,
        "",
    ]
    for i, r in enumerate(resultados, 1):
        linhas.append(f"[{i}] {r.get('titulo','')}")
        linhas.append(f"    {r.get('url','')}")
        if r.get("snippet"):
            linhas.append(f"    {r['snippet']}")
        linhas.append("")
    return "\n".join(linhas)


def _texto_amigavel_falha(query: str, erro: str) -> str:
    return "\n".join(
        [
            "=" * 70,
            "ZEARCH WEB",
            "=" * 70,
            f"Query: {query}",
            f"Data : {_agora().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 70,
            "",
            "⚠️  Não foi possível buscar agora.",
            f"Motivo: {erro}",
            "",
            "Isso normalmente é bloqueio temporário do provedor de busca.",
            "Tente novamente em alguns segundos.",
        ]
    )


# =============================================================================
# ROTAS
# =============================================================================


@app.route("/", methods=["GET"])
def raiz():
    return jsonify(
        {
            "status": "ok",
            "service": "ZEARCH WEB",
            "endpoints": {
                "GET  /search?q=<termo>": "busca no DDG",
                "GET  /perguntar?q=<termo>": "busca + memória (contexto)",
                "POST /perguntar": "idem, body {q}",
                "GET  /relembrar?q=<termo>": "só o contexto montado (debug)",
                "POST /limpar": "zera o histórico",
                "GET  /health": "ping",
            },
            "engines_disponiveis": [
                "ddg-lite",
                "ddg-html",
                "ddgs" if _TEM_DDGS else "ddgs (indisponível)",
            ],
        }
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "ts": _iso(_agora())})


@app.route("/search", methods=["GET"])
def rota_search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "Parâmetro 'q' é obrigatório"}), 400

    _log("")
    _log("=" * 70)
    _log(f"REQ /search q={query}")
    _log("=" * 70)

    t0 = time.time()
    try:
        r = buscar(query)
    except Exception as e:
        r = {
            "ok": False,
            "erro": f"{type(e).__name__}: {e}",
            "resultados": [],
            "engine": "none",
        }
    total = round(time.time() - t0, 2)

    if not r["ok"]:
        texto = _texto_amigavel_falha(query, r.get("erro", "desconhecido"))
        return (
            jsonify(
                {
                    "response": texto,
                    "meta": {
                        "query": query,
                        "tempo_s": total,
                        "engine": r.get("engine"),
                        "ok": False,
                        "erro": r.get("erro"),
                    },
                }
            ),
            200,
        )

    texto = _formatar_resultados(query, r["resultados"], r["engine"])
    _log(
        f"✅ FIM ({total}s, {len(texto)} chars, engine={r['engine']}, cache={r.get('cache')})"
    )

    return jsonify(
        {
            "response": texto,
            "meta": {
                "query": query,
                "chars": len(texto),
                "total": len(r["resultados"]),
                "tempo_s": total,
                "engine": r["engine"],
                "cache": r.get("cache", False),
                "capturado_em": _agora().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
    )


@app.route("/perguntar", methods=["GET"])
def rota_perguntar_get():
    pergunta = (request.args.get("q") or "").strip()
    if not pergunta:
        return jsonify({"error": "Parâmetro 'q' é obrigatório"}), 400
    return _processar(pergunta)


@app.route("/perguntar", methods=["POST"])
def rota_perguntar_post():
    payload = request.get_json(silent=True) or {}
    pergunta = (payload.get("q") or payload.get("pergunta") or "").strip()
    if not pergunta:
        return jsonify({"error": "Campo 'q' é obrigatório"}), 400
    return _processar(pergunta)


def _processar(pergunta: str):
    t0 = time.time()
    try:
        item = registrar_pergunta(pergunta)
    except Exception as e:
        _log(f"⚠️  registrar_pergunta falhou: {e}")
        item = {"id": int(time.time() * 1000)}

    try:
        contexto = Relembrar(pergunta)
    except Exception as e:
        contexto = f"(contexto indisponível: {e})"

    r = buscar(pergunta)
    if r["ok"]:
        texto_busca = "\n".join(
            f"[{i}] {x.get('titulo','')}\n    {x.get('url','')}\n    {x.get('snippet','')}"
            for i, x in enumerate(r["resultados"], 1)
        )
    else:
        texto_busca = f"(busca falhou: {r.get('erro')})"

    resposta = (
        "=== CONTEXTO (memória) ===\n"
        + contexto
        + "\n\n=== RESULTADOS DA BUSCA ===\n"
        + texto_busca
    )

    try:
        registrar_resposta(item["id"], resposta)
    except Exception as e:
        _log(f"⚠️  registrar_resposta falhou: {e}")

    total = round(time.time() - t0, 2)
    return jsonify(
        {
            "response": resposta,
            "meta": {
                "pergunta_id": item["id"],
                "pergunta": pergunta,
                "tempo_s": total,
                "ok": r["ok"],
                "engine": r.get("engine"),
                "capturado_em": _agora().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
    )


@app.route("/relembrar", methods=["GET"])
def rota_relembrar():
    pergunta = (request.args.get("q") or "teste").strip()
    try:
        return jsonify({"contexto": Relembrar(pergunta)})
    except Exception as e:
        return jsonify({"erro": f"{type(e).__name__}: {e}"}), 500


@app.route("/limpar", methods=["POST"])
def rota_limpar():
    try:
        db_limpar()
        return jsonify({"status": "ok", "msg": "Histórico limpo."})
    except Exception as e:
        return jsonify({"status": "erro", "msg": str(e)}), 500


# =============================================================================
# ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    # Apenas para dev local. Em produção use gunicorn.
    app.run(host="0.0.0.0", port=PORT, threaded=True)
