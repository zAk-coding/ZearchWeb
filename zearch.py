# =============================================================================
# ZEARCH WEB - Backend Flask com DDG indetectável + Memória persistente
# =============================================================================

import os
import re
import sys
import json
import time
import random
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from flask import Flask, request, jsonify

# =============================================================================
# CONFIG
# =============================================================================

# --- Flask ---
app = Flask(__name__)
PORT = int(os.environ.get("PORT", 5000))

# --- DuckDuckGo ---
DDG_URL = "https://lite.duckduckgo.com/lite/?q={q}"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

MIN_DELAY = 1.2
MAX_DELAY = 2.5
MAX_RETRIES = 3
BACKOFF_BASE = 2
TIMEOUT = 15

# --- Memória ---
DB_PATH = Path("database.json")
DB_LOCK = threading.Lock()
JANELA_RECENTES_SEGUNDOS = 5 * 60  # 5 minutos


# =============================================================================
# DATABASE — ordem: meta → historico → recentes
# =============================================================================


def db_carregar() -> dict:
    if not DB_PATH.exists():
        padrao = {
            "meta": {
                "criado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "ultima_atualizacao": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            "historico": [],
            "recentes": [],
        }
        DB_PATH.write_text(
            json.dumps(padrao, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return padrao

    try:
        return json.loads(DB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"meta": {}, "historico": [], "recentes": []}


def db_salvar(db: dict):
    db["meta"]["ultima_atualizacao"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db_ordenado = {
        "meta": db.get("meta", {}),
        "historico": db.get("historico", []),
        "recentes": db.get("recentes", []),
    }
    DB_PATH.write_text(
        json.dumps(db_ordenado, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def db_limpar():
    db = {
        "meta": {
            "criado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ultima_atualizacao": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
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
    secs = int((datetime.now() - quando).total_seconds())
    if secs < 60:
        return f"há {secs} segundos"
    if secs < 3600:
        return f"há {secs // 60} minutos"
    if secs < 86400:
        return f"há {secs // 3600} horas"
    return f"há {secs // 86400} dias"


def _atualizar_recentes(db: dict):
    agora = datetime.now()
    ficam = []
    vao = []
    for item in db.get("recentes", []):
        try:
            quando = datetime.fromisoformat(item["enviada_em"])
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
        agora = datetime.now()
        item = {
            "id": int(agora.timestamp() * 1000),
            "pergunta": pergunta,
            "enviada_em": agora.isoformat(timespec="seconds"),
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
                    item["respondida_em"] = datetime.now().isoformat(timespec="seconds")
                    break
        db_salvar(db)


def Relembrar(pergunta_atual: str) -> str:
    """Monta o contexto de memória pra IA."""
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
# DDG — busca com curl_cffi (indetectável)
# =============================================================================


def esperar():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


def decodificar_link(href: str) -> str:
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
        url = decodificar_link(a.get("href", ""))
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


def extrair_texto_puro(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.head:
        soup.head.decompose()
    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "header",
            "nav",
            "footer",
            "aside",
            "form",
            "button",
        ]
    ):
        tag.decompose()
    for a in soup.find_all("a"):
        a.replace_with(a.get_text(" ", strip=True))
    for br in soup.find_all("br"):
        br.replace_with("\n")
    return limpar_texto(soup.get_text("\n", strip=True))


def buscar_ddg(query: str) -> dict:
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
            r = curl_requests.get(
                url,
                headers=headers,
                impersonate="chrome131",
                timeout=TIMEOUT,
                allow_redirects=True,
            )
            if r.status_code in (202, 429) or len(r.text) < 500:
                print(f"   ⚠️  {r.status_code} / {len(r.text)}b, retry {tentativa}")
                time.sleep(BACKOFF_BASE**tentativa)
                continue

            resultados = extrair_resultados(r.text)
            if not resultados:
                print(f"   ⚠️  0 resultados, retry {tentativa}")
                time.sleep(BACKOFF_BASE**tentativa)
                continue

            print(f"   ✅ {len(resultados)} resultados")
            return {"ok": True, "html": r.text, "resultados": resultados}

        except Exception as e:
            print(f"   ⚠️  {type(e).__name__}: {e}")
            time.sleep(BACKOFF_BASE**tentativa)

    return {"ok": False, "erro": "Todas as tentativas falharam"}


# =============================================================================
# PROMPT DO SISTEMA
# =============================================================================

PROMPT_SISTEMA = """Você é o ZEARCH, um assistente conversacional com MEMÓRIA PERSISTENTE.

REGRAS:
1. Você recebe três blocos: CONVERSAS RECENTES, HISTÓRICO e PERGUNTA ATUAL.
2. Cada item tem tempo relativo entre colchetes ([há 3 segundos], [há 2 minutos]).
   Use isso para saber o que é novo e o que é antigo.
3. Foque na PERGUNTA ATUAL. Use o contexto para manter coerência.
4. NUNCA cite literalmente o contexto. NUNCA diga "vejo no histórico que...".
   Responda naturalmente.
5. Se o contexto não tiver nada relevante, apenas responda normalmente.
6. Estilo: direto, natural, sem enrolação.
"""


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
                "GET  /relembrar?q=<termo>": "só o contexto montado (debug)",
                "POST /limpar": "zera o histórico",
            },
        }
    )


@app.route("/search", methods=["GET"])
def rota_search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "Parâmetro 'q' é obrigatório"}), 400

    print()
    print("=" * 70)
    print(f"REQ /search q={query}")
    print("=" * 70)

    t0 = time.time()
    ddg = buscar_ddg(query)
    total = round(time.time() - t0, 2)

    if not ddg.get("ok"):
        return (
            jsonify(
                {
                    "response": None,
                    "erro": ddg.get("erro"),
                    "meta": {"query": query, "tempo_s": total},
                }
            ),
            500,
        )

    resultados = ddg["resultados"]

    # Monta o texto final
    linhas = [
        "=" * 70,
        "ZEARCH WEB",
        "=" * 70,
        f"Query: {query}",
        f"Data : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Total: {len(resultados)}",
        "=" * 70,
        "",
    ]
    for i, r in enumerate(resultados, 1):
        linhas.append(f"[{i}] {r['titulo']}")
        linhas.append(f"    {r['url']}")
        if r.get("snippet"):
            linhas.append(f"    {r['snippet']}")
        linhas.append("")

    texto = "\n".join(linhas)
    print(f"✅ FIM ({total}s, {len(texto)} chars)")

    return jsonify(
        {
            "response": texto,
            "meta": {
                "query": query,
                "chars": len(texto),
                "total": len(resultados),
                "tempo_s": total,
                "capturado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
    """Fluxo: registra pergunta → monta contexto → busca → responde → salva."""
    t0 = time.time()
    item = registrar_pergunta(pergunta)

    # Contexto de memória
    contexto = Relembrar(pergunta)

    # Busca no DDG
    ddg = buscar_ddg(pergunta)
    if ddg.get("ok"):
        resultados = ddg["resultados"]
        texto_busca = "\n".join(
            f"[{i}] {r['titulo']}\n    {r['url']}\n    {r.get('snippet','')}"
            for i, r in enumerate(resultados, 1)
        )
    else:
        texto_busca = f"(busca falhou: {ddg.get('erro')})"

    # Monta resposta
    resposta = (
        "=== CONTEXTO (memória) ===\n"
        + contexto
        + "\n\n=== RESULTADOS DA BUSCA ===\n"
        + texto_busca
    )

    registrar_resposta(item["id"], resposta)
    total = round(time.time() - t0, 2)

    return jsonify(
        {
            "response": resposta,
            "meta": {
                "pergunta_id": item["id"],
                "pergunta": pergunta,
                "tempo_s": total,
                "capturado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
    )


@app.route("/relembrar", methods=["GET"])
def rota_relembrar():
    pergunta = (request.args.get("q") or "teste").strip()
    return jsonify({"contexto": Relembrar(pergunta)})


@app.route("/limpar", methods=["POST"])
def rota_limpar():
    db_limpar()
    return jsonify({"status": "ok", "msg": "Histórico limpo."})


# =============================================================================
# ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
