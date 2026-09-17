# =============================================================================
# ZEARCH WEB - DDG Search indetectável (curl_cffi + impersonate)
# =============================================================================

import re
import sys
import time
import random
from datetime import datetime
from urllib.parse import quote_plus, unquote, urlparse, parse_qs
from bs4 import BeautifulSoup
from curl_cffi import requests


# =============================================================================
# CONFIG
# =============================================================================

DDG_URL = "https://lite.duckduckgo.com/lite/?q={q}"

# User-agents variados (rotação leve)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# Delay entre requests (segundos) — obrigatório pra não bloquear
MIN_DELAY = 1.5
MAX_DELAY = 3.0

# Retry em caso de 202/429
MAX_RETRIES = 3
BACKOFF_BASE = 2  # 2s, 4s, 8s

TIMEOUT = 15


# =============================================================================
# HELPERS
# =============================================================================

def esperar():
    """Delay aleatório entre requests."""
    delay = random.uniform(MIN_DELAY, MAX_DELAY)
    time.sleep(delay)


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


def limpar_texto(texto: str) -> str:
    linhas = []
    for linha in texto.splitlines():
        linha = re.sub(r"[ \t]+", " ", linha).strip()
        if linha and not re.fullmatch(r"[\W_]+", linha):
            linhas.append(linha)
    return "\n".join(linhas)


# =============================================================================
# EXTRAÇÃO
# =============================================================================

def extrair_resultados(html: str) -> list:
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

        resultados.append({
            "titulo": titulo,
            "url": url,
            "dominio": dominio,
            "snippet": snippet,
        })

    vistos = set()
    unicos = []
    for r in resultados:
        if r["url"] not in vistos:
            vistos.add(r["url"])
            unicos.append(r)
    return unicos


def extrair_texto_puro(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.head:
        soup.head.decompose()
    for tag in soup(["script", "style", "noscript", "svg", "header", "nav", "footer", "aside", "form", "button"]):
        tag.decompose()
    for a in soup.find_all("a"):
        a.replace_with(a.get_text(" ", strip=True))
    for br in soup.find_all("br"):
        br.replace_with("\n")
    return limpar_texto(soup.get_text("\n", strip=True))


# =============================================================================
# BUSCA COM RETRY
# =============================================================================

def buscar_ddg(query: str) -> dict:
    """Faz GET no DDG Lite com curl_cffi + impersonate + retry."""
    url = DDG_URL.format(q=quote_plus(query))
    print(f"🌐 Buscando: {query}")

    for tentativa in range(1, MAX_RETRIES + 1):
        try:
            esperar()

            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
                "Referer": "https://lite.duckduckgo.com/",
            }

            # impersonate="chrome" → imita TLS fingerprint do Chrome real
            r = requests.get(
                url,
                headers=headers,
                impersonate="chrome131",
                timeout=TIMEOUT,
                allow_redirects=True,
            )

            # 202 ou 429 = bloqueio → retry
            if r.status_code in (202, 429):
                print(f"   ⚠️  Status {r.status_code} (bloqueio), retry {tentativa}/{MAX_RETRIES}...")
                time.sleep(BACKOFF_BASE ** tentativa)
                continue

            if r.status_code != 200:
                print(f"   ⚠️  Status {r.status_code}, retry {tentativa}/{MAX_RETRIES}...")
                time.sleep(BACKOFF_BASE ** tentativa)
                continue

            # Verifica se veio HTML real (não página de challenge)
            if len(r.text) < 500:
                print(f"   ⚠️  HTML muito curto ({len(r.text)}b), retry...")
                time.sleep(BACKOFF_BASE ** tentativa)
                continue

            resultados = extrair_resultados(r.text)
            if not resultados:
                print(f"   ⚠️  0 resultados, retry {tentativa}/{MAX_RETRIES}...")
                time.sleep(BACKOFF_BASE ** tentativa)
                continue

            print(f"   ✅ Status {r.status_code}  Resultados: {len(resultados)}")
            return {
                "ok": True,
                "html": r.text,
                "resultados": resultados,
            }

        except Exception as e:
            print(f"   ⚠️  Erro: {type(e).__name__}: {e}")
            time.sleep(BACKOFF_BASE ** tentativa)

    return {"ok": False, "erro": "Todas as tentativas falharam"}


# =============================================================================
# MAIN
# =============================================================================

def main():
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = input("🔍 Pesquisar: ").strip()

    if not query:
        print("❌ Nada digitado.")
        return

    print()
    print("=" * 70)
    print(f"  ZEARCH • DDG Search (curl_cffi)")
    print(f"  Query: {query}")
    print("=" * 70)
    print()

    ddg = buscar_ddg(query)

    if not ddg.get("ok"):
        print(f"\n❌ Falhou: {ddg.get('erro')}")
        return

    resultados = ddg["resultados"]

    # Salva resultados
    with open("resultado.txt", "w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write("ZEARCH WEB\n")
        f.write("=" * 70 + "\n")
        f.write(f"Query: {query}\n")
        f.write(f"Data : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total: {len(resultados)}\n")
        f.write("=" * 70 + "\n\n")

        for i, r in enumerate(resultados, 1):
            f.write(f"[{i}] {r['titulo']}\n")
            f.write(f"    {r['url']}\n")
            if r.get("snippet"):
                f.write(f"    {r['snippet']}\n")
            f.write("\n")

    print(f"\n💾 Salvo em resultado.txt ({len(resultados)} resultados)")
    print()
    for i, r in enumerate(resultados[:5], 1):
        print(f"[{i}] {r['titulo'][:70]}")
        print(f"    {r['url']}")


if __name__ == "__main__":
    main()