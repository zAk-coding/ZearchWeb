# =============================================================================
# ZEARCH WEB - Backend Flask (Render-ready)
# DDG Lite → texto puro do 1º resultado
# Resposta: JSON com campo "response" (texto pronto pra salvar em .txt)
# =============================================================================

import os
import re
import sys
import time
import requests
from datetime import datetime
from urllib.parse import quote_plus, unquote, urlparse, parse_qs
from bs4 import BeautifulSoup

from flask import Flask, request, jsonify

# =============================================================================
# CONFIG
# =============================================================================

app = Flask(__name__)
PORT = int(os.environ.get("PORT", 5000))

DDG_URL = "https://lite.duckduckgo.com/lite/?q={q}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
}

TIMEOUT = 30


# =============================================================================
# ENTIDADES
# =============================================================================

ENTIDADES = {
    "&nbsp;": " ",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
    "&aacute;": "á",
    "&eacute;": "é",
    "&iacute;": "í",
    "&oacute;": "ó",
    "&uacute;": "ú",
    "&atilde;": "ã",
    "&otilde;": "õ",
    "&ccedil;": "ç",
    "&acirc;": "â",
    "&ecirc;": "ê",
    "&ocirc;": "ô",
    "&agrave;": "à",
    "&Aacute;": "Á",
    "&Eacute;": "É",
    "&Iacute;": "Í",
    "&Oacute;": "Ó",
    "&Uacute;": "Ú",
    "&Atilde;": "Ã",
    "&Otilde;": "Õ",
    "&Ccedil;": "Ç",
    "&#160;": " ",
    "&#8217;": "'",
    "&#8211;": "-",
    "&#8212;": "—",
    "&#8220;": '"',
    "&#8221;": '"',
    "&#8230;": "...",
}


def decodificar_entidades(s: str) -> str:
    for ent, ch in ENTIDADES.items():
        s = s.replace(ent, ch)
    return s


# =============================================================================
# HELPERS
# =============================================================================


def decodificar_link_ddg(href: str) -> str:
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


def normalizar_texto(texto: str) -> str:
    linhas = []
    for linha in texto.splitlines():
        linha = re.sub(r"[ \t]+", " ", linha).strip()
        if not linha:
            continue
        if re.fullmatch(r"[\W_]+", linha):
            continue
        linhas.append(linha)
    return "\n".join(linhas)


# =============================================================================
# EXTRAÇÃO DE TEXTO PURO
# =============================================================================

TAGS_REMOVER = [
    "script",
    "style",
    "noscript",
    "svg",
    "head",
    "meta",
    "link",
    "iframe",
    "template",
    "object",
    "embed",
    "canvas",
    "header",
    "nav",
    "footer",
    "aside",
    "form",
    "button",
    "input",
    "select",
    "textarea",
    "label",
    "video",
    "audio",
    "source",
    "track",
    "picture",
]

CLASSES_LIXO = [
    "ad",
    "ads",
    "advert",
    "adsbygoogle",
    "banner",
    "sponsor",
    "menu",
    "nav",
    "navbar",
    "breadcrumb",
    "pagination",
    "comment",
    "comments",
    "social",
    "share",
    "disqus",
    "footer",
    "newsletter",
    "cookie",
    "gdpr",
    "consent",
    "sidebar",
    "widget",
    "related",
    "recommend",
    "notice",
    "alert",
    "popup",
    "modal",
]


def extrair_texto_puro(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    if soup.head:
        soup.head.decompose()

    for tag in soup(TAGS_REMOVER):
        tag.decompose()

    for classe in CLASSES_LIXO:
        for el in soup.find_all(class_=re.compile(rf"\b{classe}\b", re.I)):
            el.decompose()

    for a in soup.find_all("a"):
        a.replace_with(a.get_text(" ", strip=True))

    for br in soup.find_all("br"):
        br.replace_with("\n")

    texto = soup.get_text("\n", strip=True)
    texto = decodificar_entidades(texto)
    return normalizar_texto(texto)


# =============================================================================
# ETAPA 1 — BUSCA NO DDG LITE
# =============================================================================


def extrair_resultados_ddg(html: str) -> list:
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
        href = a.get("href", "")
        url = decodificar_link_ddg(href)

        if not titulo or not url.startswith("http"):
            continue

        snippet = ""
        tr = a.find_parent("tr")
        if tr:
            tr_next = tr.find_next_sibling("tr")
            if tr_next:
                td = tr_next.select_one("td.result-snippet")
                if td:
                    snippet = td.get_text(" ", strip=True)

        dominio = ""
        if tr:
            dom_el = tr.select_one(".link-text")
            if dom_el:
                dominio = dom_el.get_text(" ", strip=True)

        resultados.append(
            {
                "titulo": titulo,
                "url": url,
                "dominio": dominio,
                "snippet": snippet,
            }
        )

    vistos = set()
    unicos = []
    for r in resultados:
        if r["url"] not in vistos:
            vistos.add(r["url"])
            unicos.append(r)
    return unicos


def buscar_ddg(query: str) -> dict:
    url = DDG_URL.format(q=quote_plus(query))
    print(f"🌐 [1/2] Buscando no DuckDuckGo Lite...")
    print(f"       {url}")

    t0 = time.time()
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.exceptions.RequestException as e:
        return {"ok": False, "erro": f"{type(e).__name__}: {e}", "url": url}

    tempo = round(time.time() - t0, 2)
    resultados = extrair_resultados_ddg(r.text)

    print(
        f"       Status: {r.status_code}  Resultados: {len(resultados)}  "
        f"Tempo: {tempo}s"
    )

    return {
        "ok": True,
        "query": query,
        "url": url,
        "status": r.status_code,
        "html": r.text,
        "resultados": resultados,
        "tempo_s": tempo,
    }


# =============================================================================
# ETAPA 2 — FETCH DO 1º LINK
# =============================================================================


def fetch_pagina(url: str) -> dict:
    print(f"🌐 [2/2] Abrindo 1º link:")
    print(f"       {url}")

    t0 = time.time()
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.exceptions.Timeout:
        return {"ok": False, "erro": "Timeout", "url": url}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "erro": f"{type(e).__name__}: {e}", "url": url}

    tempo = round(time.time() - t0, 2)
    texto = extrair_texto_puro(r.text)

    print(
        f"       Status: {r.status_code}  Bytes: {len(r.content)}  "
        f"Texto: {len(texto)}c  Tempo: {tempo}s"
    )

    return {
        "ok": True,
        "url": url,
        "url_final": r.url,
        "status": r.status_code,
        "html_bytes": len(r.content),
        "texto": texto,
        "chars": len(texto),
        "tempo_s": tempo,
    }


# =============================================================================
# NÚCLEO — chamado pelas rotas
# =============================================================================


def executar_busca(query: str) -> str:
    """
    Executa a busca completa e devolve o TEXTO FINAL (pronto pra salvar).
    Nunca dá exceção — devolve string com o resultado ou o erro.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1) Busca DDG
    ddg = buscar_ddg(query)
    if not ddg.get("ok"):
        return f"[ERRO] Busca falhou: {ddg.get('erro')}"

    resultados = ddg["resultados"]

    # ---------------- CASO A: sem resultados ----------------
    if not resultados:
        print("⚠️  Nenhum resultado estruturado. Usando texto puro do DDG.")
        texto_ddg = extrair_texto_puro(ddg["html"])
        return (
            "=" * 80
            + "\n"
            + "ZEARCH WEB\n"
            + "=" * 80
            + "\n"
            + f"Query     : {query}\n"
            + f"Data      : {ts}\n"
            + f"Status    : {ddg['status']}\n"
            + f"Fonte     : {ddg['url']}\n"
            + "Modo      : texto puro do DDG (sem resultados estruturados)\n"
            + "=" * 80
            + "\n\n"
            + texto_ddg
            + "\n"
        )

    # 2) Pega 1º link
    primeiro = resultados[0]
    url_1 = primeiro["url"]

    # 3) Fetch
    fetch = fetch_pagina(url_1)

    # Fallback: tenta próximos
    if not fetch.get("ok") or fetch.get("chars", 0) < 100:
        print("⚠️  1º link falhou. Tentando próximos...")
        for r in resultados[1:6]:
            print(f"   → {r['url'][:70]}")
            fetch = fetch_pagina(r["url"])
            if fetch.get("ok") and fetch.get("chars", 0) >= 100:
                url_1 = r["url"]
                primeiro = r
                break

    # ---------------- CASO B3: nenhum link funcionou ----------------
    if not fetch.get("ok") or fetch.get("chars", 0) < 100:
        print("⚠️  Nenhum link funcionou. Usando texto puro do DDG.")
        texto_ddg = extrair_texto_puro(ddg["html"])
        linhas = [
            "=" * 80,
            "ZEARCH WEB",
            "=" * 80,
            f"Query     : {query}",
            f"Data      : {ts}",
            f"Resultados: {len(resultados)}",
            "Modo      : nenhum link acessível → texto puro do DDG",
            "=" * 80,
            "",
            "RESULTADOS DA BUSCA",
            "=" * 80,
            "",
        ]
        for i, r in enumerate(resultados, 1):
            linhas.append(f"[{i}] {r['titulo']}")
            linhas.append(f"    {r['url']}")
            if r.get("snippet"):
                linhas.append(f"    {r['snippet']}")
            linhas.append("")
        linhas.append("=" * 80)
        linhas.append("TEXTO PURO DO DDG (fallback)")
        linhas.append("=" * 80)
        linhas.append("")
        linhas.append(texto_ddg)
        return "\n".join(linhas)

    # ---------------- CASO B4: sucesso ----------------
    linhas = [
        "=" * 80,
        "ZEARCH WEB",
        "=" * 80,
        f"Query     : {query}",
        f"Data      : {ts}",
        f"Resultados: {len(resultados)}",
        f"Fonte     : {url_1}",
        "=" * 80,
        "",
        "RESULTADOS DA BUSCA",
        "=" * 80,
        "",
    ]
    for i, r in enumerate(resultados, 1):
        linhas.append(f"[{i}] {r['titulo']}")
        linhas.append(f"    {r['url']}")
        if r.get("snippet"):
            linhas.append(f"    {r['snippet']}")
        linhas.append("")

    linhas.append("")
    linhas.append("=" * 80)
    linhas.append(f"RESULTADOS DO LINK: {url_1}")
    linhas.append("=" * 80)
    linhas.append("")
    linhas.append(fetch["texto"])

    return "\n".join(linhas)


# =============================================================================
# ROTAS FLASK
# =============================================================================


@app.route("/", methods=["GET"])
def raiz():
    return jsonify(
        {
            "status": "ok",
            "service": "ZEARCH WEB",
            "endpoints": {
                "GET  /": "status",
                "GET  /search?q=<termo>": "busca e devolve texto em 'response'",
                "POST /search": 'idem, JSON body {"q":"..."}',
            },
        }
    )


@app.route("/search", methods=["GET"])
def rota_search_get():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "Parâmetro 'q' é obrigatório"}), 400

    print()
    print("=" * 80)
    print(f"REQ GET /search?q={query}")
    print("=" * 80)

    t0 = time.time()
    texto = executar_busca(query)
    total = round(time.time() - t0, 2)

    print(f"✅ FIM  ({total}s, {len(texto)} chars)")

    return jsonify(
        {
            "response": texto,
            "meta": {
                "query": query,
                "chars": len(texto),
                "tempo_s": total,
                "capturado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
    )


@app.route("/search", methods=["POST"])
def rota_search_post():
    payload = request.get_json(silent=True) or {}
    query = (payload.get("q") or payload.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Campo 'q' é obrigatório no JSON"}), 400

    print()
    print("=" * 80)
    print(f"REQ POST /search q={query}")
    print("=" * 80)

    t0 = time.time()
    texto = executar_busca(query)
    total = round(time.time() - t0, 2)

    print(f"✅ FIM  ({total}s, {len(texto)} chars)")

    return jsonify(
        {
            "response": texto,
            "meta": {
                "query": query,
                "chars": len(texto),
                "tempo_s": total,
                "capturado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
    )


# =============================================================================
# ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
