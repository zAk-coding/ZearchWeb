# =============================================================================
# ZEARCH WEB - Backend Flask robusto (multi-engine + proxy rotation + cache)
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

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

IMPERSONATES = ["chrome131", "chrome124", "chrome120", "safari17_0"]

ORCAMENTO_BUSCA_S = 30.0
TIMEOUT_HTTP = 8
MAX_TENTATIVAS = 2
BACKOFF_MAX_S = 2.0

CACHE_TTL_S = 300
CACHE_MAX_ITENS = 200

DB_PATH = Path(os.environ.get("ZEARCH_DB", "database.json"))
DB_LOCK = threading.Lock()
JANELA_RECENTES_SEGUNDOS = 5 * 60


# =============================================================================
# PROXIES — rotação + health check temporário
# =============================================================================

# Lista default (do que você passou). Pode sobrescrever via env PROXIES="host:port:user:pass\n..."
_PROXIES_DEFAULT = """
31.59.20.176:6754:vkxmthnf:bmq3z16z07nt
45.38.107.97:6014:vkxmthnf:bmq3z16z07nt
198.105.121.200:6462:vkxmthnf:bmq3z16z07nt
64.137.96.74:6641:vkxmthnf:bmq3z16z07nt
198.23.243.226:6361:vkxmthnf:bmq3z16z07nt
38.154.185.97:6370:vkxmthnf:bmq3z16z07nt
84.247.60.125:6095:vkxmthnf:bmq3z16z07nt
142.111.67.146:5611:vkxmthnf:bmq3z16z07nt
191.96.254.138:6185:vkxmthnf:bmq3z16z07nt
31.58.9.4:6077:vkxmthnf:bmq3z16z07nt
""".strip()


def _parse_proxies(texto: str):
    """Aceita linhas 'host:port:user:pass' ou 'host:port' ou 'http://...'."""
    out = []
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        if (
            linha.startswith("http://")
            or linha.startswith("https://")
            or linha.startswith("socks")
        ):
            out.append(linha)
            continue
        partes = linha.split(":")
        if len(partes) == 4:
            host, porta, user, senha = partes
            out.append(f"http://{user}:{senha}@{host}:{porta}")
        elif len(partes) == 2:
            host, porta = partes
            out.append(f"http://{host}:{porta}")
    return out


class PoolProxies:
    """Pool com blacklist temporária para proxies que falharam."""

    BAN_SEGUNDOS = 180  # proxy fica "de castigo" por 3 min após falhar

    def __init__(self, lista):
        self._todos = list(lista)
        self._ruins = {}  # proxy -> timestamp de quando pode voltar
        self._lock = threading.Lock()

    def todos(self):
        return list(self._todos)

    def disponiveis(self):
        agora = time.time()
        with self._lock:
            # limpa expirados
            self._ruins = {p: t for p, t in self._ruins.items() if t > agora}
            bons = [p for p in self._todos if p not in self._ruins]
        return bons

    def marcar_ruim(self, proxy: str):
        if not proxy:
            return
        with self._lock:
            self._ruins[proxy] = time.time() + self.BAN_SEGUNDOS

    def aleatorio(self):
        bons = self.disponiveis()
        if not bons:
            # se todos estiverem banidos, libera geral (tenta de novo)
            with self._lock:
                self._ruins.clear()
            bons = list(self._todos)
        return random.choice(bons) if bons else None


_texto_proxies = os.environ.get("PROXIES", "").strip() or _PROXIES_DEFAULT
_lista_proxies = _parse_proxies(_texto_proxies)
POOL = PoolProxies(_lista_proxies)

_log_inicial = f"[ZEARCH] Proxies carregados: {len(_lista_proxies)}"


# =============================================================================
# HELPERS
# =============================================================================


def _agora():
    return datetime.now()


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _hash_query(q):
    return hashlib.sha1(q.strip().lower().encode("utf-8")).hexdigest()


def _log(msg):
    try:
        print(msg, flush=True)
    except Exception:
        pass


# =============================================================================
# DATABASE
# =============================================================================


def db_carregar():
    if not DB_PATH.exists():
        return {
            "meta": {"criado_em": _iso(_agora()), "ultima_atualizacao": _iso(_agora())},
            "historico": [],
            "recentes": [],
        }
    try:
        return json.loads(DB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"meta": {}, "historico": [], "recentes": []}


def db_salvar(db):
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
    db_salvar(
        {
            "meta": {"criado_em": _iso(_agora()), "ultima_atualizacao": _iso(_agora())},
            "historico": [],
            "recentes": [],
        }
    )
    return db


def tempo_relativo(iso_str):
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


def _atualizar_recentes(db):
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


def registrar_pergunta(pergunta):
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


def registrar_resposta(pergunta_id, resposta):
    with DB_LOCK:
        db = db_carregar()
        for lista in ("recentes", "historico"):
            for item in db.get(lista, []):
                if item.get("id") == pergunta_id:
                    item["resposta"] = resposta
                    item["respondida_em"] = _iso(_agora())
                    break
        db_salvar(db)


def Relembrar(pergunta_atual):
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
# CACHE
# =============================================================================


class CacheLRU:
    def __init__(self, ttl_s, max_itens):
        self.ttl = ttl_s
        self.max = max_itens
        self._d = {}
        self._lock = threading.Lock()

    def get(self, chave):
        with self._lock:
            item = self._d.get(chave)
            if not item:
                return None
            valor, expira = item
            if time.time() > expira:
                self._d.pop(chave, None)
                return None
            return valor

    def set(self, chave, valor):
        with self._lock:
            if len(self._d) >= self.max:
                try:
                    mais_antigo = min(self._d.items(), key=lambda kv: kv[1][1])[0]
                    self._d.pop(mais_antigo, None)
                except Exception:
                    self._d.clear()
            self._d[chave] = (valor, time.time() + self.ttl)


CACHE = CacheLRU(CACHE_TTL_S, CACHE_MAX_ITENS)


# =============================================================================
# REQUEST HTTP com rotação de proxy
# =============================================================================


def _http_request(url, method="GET", headers=None, data=None, deadline=None):
    """
    Tenta a request com proxy rotativo.
    Retorna (response, proxy_usado) ou (None, None) em caso de falha total.
    """
    proxies_para_tentar = []

    # monta a fila de proxies: aleatórios + 1 tentativa final sem proxy
    vistos = set()
    for _ in range(min(3, len(POOL.disponiveis()) or 1)):
        p = POOL.aleatorio()
        if p and p not in vistos:
            vistos.add(p)
            proxies_para_tentar.append(p)

    if not proxies_para_tentar:
        proxies_para_tentar = [None]  # sem proxy

    # por último, tenta sem proxy se ainda não tentou
    if None not in proxies_para_tentar:
        proxies_para_tentar.append(None)

    for proxy in proxies_para_tentar:
        if deadline and time.time() > deadline:
            _log("      ⏱️  deadline atingido, abortando request")
            return None, None

        try:
            kwargs = {
                "headers": headers or {},
                "impersonate": random.choice(IMPERSONATES),
                "timeout": TIMEOUT_HTTP,
                "allow_redirects": True,
            }
            if proxy:
                kwargs["proxy"] = proxy

            if method == "GET":
                r = curl_requests.get(url, **kwargs)
            else:
                r = curl_requests.post(url, data=data or {}, **kwargs)

            tag = "proxy" if proxy else "direto"
            if proxy:
                # mostra só o IP:porta pra não vazar credencial no log
                try:
                    ip_port = proxy.split("@")[-1]
                except Exception:
                    ip_port = "?"
                tag = f"proxy {ip_port}"
            _log(f"      ↳ {r.status_code} via {tag} ({len(r.text)}b)")
            return r, proxy

        except Exception as e:
            msg = str(e)[:100]
            tag = "direto"
            if proxy:
                try:
                    tag = f"proxy {proxy.split('@')[-1]}"
                    POOL.marcar_ruim(proxy)
                except Exception:
                    tag = "proxy"
            _log(f"      ✗ falhou em {tag}: {type(e).__name__} {msg}")
            continue

    return None, None


# =============================================================================
# ENGINE 1 — DDG LITE
# =============================================================================

DDG_LITE_URL = "https://lite.duckduckgo.com/lite/?q={q}"


def _decodificar_link(href):
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


def _extrair_resultados_lite(html):
    soup = BeautifulSoup(html, "html.parser")
    resultados = []
    links = soup.select("a.result-link")
    if not links:
        for a in soup.find_all("a", href=True):
            if a["href"].startswith("http") and "duckduckgo" not in a["href"]:
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


def _buscar_ddg_lite(query, deadline):
    url = DDG_LITE_URL.format(q=quote_plus(query))
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        if time.time() > deadline:
            _log("   ⏱️  ddg-lite: orçamento esgotado")
            return []
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
            "Referer": "https://lite.duckduckgo.com/",
        }
        r, _ = _http_request(url, "GET", headers=headers, deadline=deadline)
        if r is None:
            continue
        if r.status_code in (202, 429) or len(r.text) < 500:
            _log(
                f"   ⚠️  ddg-lite status {r.status_code} / {len(r.text)}b (tent {tentativa})"
            )
            time.sleep(min(BACKOFF_MAX_S, 0.8 * tentativa))
            continue
        res = _extrair_resultados_lite(r.text)
        if not res:
            _log(f"   ⚠️  ddg-lite 0 resultados (tent {tentativa})")
            time.sleep(min(BACKOFF_MAX_S, 0.8 * tentativa))
            continue
        _log(f"   ✅ ddg-lite {len(res)} resultados")
        return res
    return []


# =============================================================================
# ENGINE 2 — DDG HTML
# =============================================================================

DDG_HTML_URL = "https://html.duckduckgo.com/html/?q={q}"


def _extrair_resultados_html(html):
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
        try:
            dom = urlparse(url).netloc
        except Exception:
            dom = ""
        out.append({"titulo": titulo, "url": url, "dominio": dom, "snippet": snippet})
    vistos, unicos = set(), []
    for r in out:
        if r["url"] not in vistos:
            vistos.add(r["url"])
            unicos.append(r)
    return unicos


def _buscar_ddg_html(query, deadline):
    url = DDG_HTML_URL.format(q=quote_plus(query))
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        if time.time() > deadline:
            return []
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
            "Referer": "https://duckduckgo.com/",
        }
        r, _ = _http_request(
            url, "POST", headers=headers, data={"q": query, "b": ""}, deadline=deadline
        )
        if r is None:
            continue
        if r.status_code in (202, 429) or len(r.text) < 500:
            _log(
                f"   ⚠️  ddg-html status {r.status_code} / {len(r.text)}b (tent {tentativa})"
            )
            time.sleep(min(BACKOFF_MAX_S, 0.8 * tentativa))
            continue
        res = _extrair_resultados_html(r.text)
        if not res:
            _log(f"   ⚠️  ddg-html 0 resultados (tent {tentativa})")
            time.sleep(min(BACKOFF_MAX_S, 0.8 * tentativa))
            continue
        _log(f"   ✅ ddg-html {len(res)} resultados")
        return res
    return []


# =============================================================================
# ENGINE 3 — duckduckgo_search (biblioteca)
# =============================================================================


def _buscar_ddgs(query, deadline):
    if not _TEM_DDGS or time.time() > deadline:
        return []
    # tenta com proxy também
    proxy = POOL.aleatorio()
    try:
        kwargs = {"timeout": TIMEOUT_HTTP}
        if proxy:
            kwargs["proxy"] = proxy
        with DDGS(**kwargs) as ddgs:
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
        if proxy:
            POOL.marcar_ruim(proxy)
        return []


# =============================================================================
# ORQUESTRADOR
# =============================================================================


def buscar(query):
    chave = _hash_query(query)
    cacheado = CACHE.get(chave)
    if cacheado:
        _log(f"   💾 cache hit ({len(cacheado.get('resultados', []))} resultados)")
        return {**cacheado, "cache": True}

    deadline = time.time() + ORCAMENTO_BUSCA_S
    disponiveis = POOL.disponiveis()
    _log(
        f"🌐 Buscando: {query}  (orçamento {ORCAMENTO_BUSCA_S}s, proxies vivos: {len(disponiveis)}/{len(POOL.todos())})"
    )

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
        "erro": "Nenhuma engine retornou resultados (proxy/timeout/bloqueio).",
        "cache": False,
    }


# =============================================================================
# FORMATAÇÃO
# =============================================================================


def _formatar_resultados(query, resultados, engine):
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


def _texto_amigavel_falha(query, erro):
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
            "Isso normalmente é bloqueio temporário do provedor.",
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
            "proxies_total": len(POOL.todos()),
            "proxies_vivos": len(POOL.disponiveis()),
            "engines": [
                "ddg-lite",
                "ddg-html",
                "ddgs" if _TEM_DDGS else "ddgs(indisponível)",
            ],
            "endpoints": {
                "GET  /search?q=<termo>": "busca no DDG",
                "GET  /perguntar?q=<termo>": "busca + memória",
                "POST /perguntar": "idem, body {q}",
                "GET  /relembrar?q=<termo>": "contexto (debug)",
                "POST /limpar": "zera histórico",
                "GET  /health": "ping",
                "GET  /proxies": "status dos proxies",
            },
        }
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "ts": _iso(_agora())})


@app.route("/proxies", methods=["GET"])
def rota_proxies():
    """Mostra status dos proxies (sem vazar credenciais)."""
    agora = time.time()
    with POOL._lock:
        ruins = {p: t for p, t in POOL._ruins.items() if t > agora}

    def _safe(p):
        if not p:
            return "direto"
        try:
            return p.split("@")[-1]
        except Exception:
            return "?"

    return jsonify(
        {
            "total": len(POOL.todos()),
            "vivos": len(POOL.disponiveis()),
            "banidos_temporariamente": [
                {"proxy": _safe(p), "libera_em_s": int(t - agora)}
                for p, t in ruins.items()
            ],
            "lista": [_safe(p) for p in POOL.todos()],
        }
    )


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
        return (
            jsonify(
                {
                    "response": _texto_amigavel_falha(
                        query, r.get("erro", "desconhecido")
                    ),
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


def _processar(pergunta):
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

    return jsonify(
        {
            "response": resposta,
            "meta": {
                "pergunta_id": item["id"],
                "pergunta": pergunta,
                "tempo_s": round(time.time() - t0, 2),
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
    _log(_log_inicial)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
