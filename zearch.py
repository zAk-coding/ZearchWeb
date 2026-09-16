# =============================================================================
# ZEARCH WEB - ZW-1 PROFESSIONAL (Screenshot Only)
# Abre o Bing, espera carregar e tira um print.
# =============================================================================

import os
import sys
import time
import uuid
import asyncio
import logging
import threading
from urllib.parse import quote_plus
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from playwright.async_api import async_playwright

# =============================================================================
# 1. CONFIGURAÇÃO
# =============================================================================

app = Flask(__name__)

PORT = int(os.environ.get("PORT", 5000))

URL_TEMPLATE = "https://www.bing.com/search?q={q}"

UA_MOBILE = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Mobile Safari/537.36"
)

VIEWPORT = {"width": 412, "height": 915}
DEVICE_SCALE = 2.625

PHOTOS_DIR = Path(os.environ.get("PHOTOS_DIR", "./photos"))
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

BANNER = "ZEARCH WEB • ZW-1 • Screenshot"


# =============================================================================
# 2. LOGGING COLORIDO
# =============================================================================


class ColorFormatter(logging.Formatter):
    CINZA = "\033[90m"
    AZUL = "\033[94m"
    VERDE = "\033[92m"
    AMARELO = "\033[93m"
    VERMELHO = "\033[91m"
    CIANO = "\033[96m"
    MAGENTA = "\033[95m"
    RESET = "\033[0m"

    def format(self, record):
        ts = self.formatTime(record, "%H:%M:%S")
        nivel = record.levelname
        cores = {
            "INFO": self.AZUL,
            "WARNING": self.AMARELO,
            "ERROR": self.VERMELHO,
            "CRITICAL": self.VERMELHO,
        }
        cor = cores.get(nivel, self.RESET)
        msg = record.getMessage()
        return f"{self.CINZA}[{ts}]{self.RESET} {cor}{nivel:<5}{self.RESET} {msg}"


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColorFormatter())
log = logging.getLogger("zearch")
log.handlers = [handler]
log.setLevel(logging.INFO)
log.propagate = False


def ip_do_cliente() -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "?"


# =============================================================================
# 3. EVENT LOOP PERSISTENTE
# =============================================================================


class LoopBackground:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout=120):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=timeout)


LOOP_BG = LoopBackground()


# =============================================================================
# 4. BROWSER
# =============================================================================


class BrowserPool:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _criar(self):
        log.info("BOOT Chromium iniciando...")
        t0 = time.time()
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-sync",
                "--no-first-run",
                "--disable-features=Translate,BackForwardCache,AutomationControlled",
                "--disable-blink-features=AutomationControlled",
                "--exclude-switches=enable-automation",
            ],
        )
        log.info(f"BOOT Chromium pronto em {round(time.time()-t0, 2)}s")

    async def novo_contexto(self):
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                await self._criar()

            context = await self._browser.new_context(
                user_agent=UA_MOBILE,
                viewport=VIEWPORT,
                device_scale_factor=DEVICE_SCALE,
                is_mobile=True,
                has_touch=True,
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                java_script_enabled=True,
                extra_http_headers={
                    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Sec-Ch-Ua": '"Chromium";v="152", "Not_A Brand";v="24"',
                    "Sec-Ch-Ua-Mobile": "?1",
                    "Sec-Ch-Ua-Platform": '"Android"',
                },
            )
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['pt-BR', 'pt', 'en-US', 'en']
                });
                window.chrome = window.chrome || { runtime: {} };
            """)
            return context

    async def warmup(self):
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                await self._criar()


POOL = BrowserPool()


# =============================================================================
# 5. SCREENSHOT
# =============================================================================


async def tirar_screenshot(query: str) -> dict:
    """
    Abre o Bing, espera carregar e tira um print da página.
    Devolve dict com nome do arquivo + tempo.
    """
    url = URL_TEMPLATE.format(q=quote_plus(query))
    t_ini = time.time()

    context = await POOL.novo_contexto()
    page = await context.new_page()

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)

        # Espera o body existir e ter algo visível
        try:
            await page.wait_for_function(
                """() => {
                    const b = document.body;
                    return b && b.innerText && b.innerText.trim().length > 50;
                }""",
                timeout=10000,
            )
        except Exception:
            log.warning("BOOT body vazio após 10s")

        # Espera final pra render terminar
        await page.wait_for_timeout(2000)

        # Log de diagnóstico
        try:
            html_len = len(await page.content())
            n_content = await page.locator("#b_content").count()
            n_results = await page.locator("#b_results").count()
            n_algo = await page.locator(".b_algo").count()
            n_ca = await page.locator("#ca_main, #copans_container").count()
            log.info(
                f"BOOT HTML={html_len}b #b_content={n_content} "
                f"#b_results={n_results} .b_algo={n_algo} #ca_main={n_ca}"
            )
        except Exception:
            pass

        # Print
        nome_foto = f"{uuid.uuid4().hex}.png"
        caminho = PHOTOS_DIR / nome_foto
        await page.screenshot(path=str(caminho), full_page=True, type="png")

    except Exception as e:
        import traceback

        traceback.print_exc()
        nome_foto = None
    finally:
        try:
            await page.close()
        except Exception:
            pass
        try:
            await context.close()
        except Exception:
            pass

    t_total = time.time() - t_ini
    return {
        "query": query,
        "url": url,
        "arquivo": nome_foto,
        "tempo_s": round(t_total, 3),
    }


# =============================================================================
# 6. ROTAS
# =============================================================================


@app.route("/photos/<path:nome>", methods=["GET"])
def servir_foto(nome):
    return send_from_directory(PHOTOS_DIR.resolve(), nome)


@app.route("/", methods=["GET"])
def raiz():
    return jsonify(
        {
            "status": "ok",
            "service": BANNER,
            "endpoints": {
                "GET /": "status",
                "GET /screenshot?q=<termo>": "abre o Bing e tira print",
                "GET /photos/<file>": "baixa o PNG",
                "GET /warmup": "esquenta o Chromium",
            },
        }
    )


@app.route("/warmup", methods=["GET"])
def warmup():
    try:
        LOOP_BG.run(POOL.warmup())
        return jsonify({"status": "ok", "browser": "warm"})
    except Exception as e:
        return jsonify({"status": "erro", "erro": f"{type(e).__name__}: {e}"}), 500


@app.route("/screenshot", methods=["GET"])
def rota_screenshot():
    t0 = time.time()
    ip = ip_do_cliente()
    query = (request.args.get("q") or "").strip()

    if not query:
        return (
            jsonify(
                {
                    "error": "Parâmetro 'q' é obrigatório",
                    "example": "/screenshot?q=quem e maciel",
                }
            ),
            400,
        )

    log.info(f'REQ ip={ip} GET q="{query}"')

    try:
        dados = LOOP_BG.run(tirar_screenshot(query))
    except Exception as e:
        log.error(f'ERR ip={ip} q="{query}" {type(e).__name__}: {e}')
        return jsonify({"erro": f"{type(e).__name__}: {e}"}), 500

    total = round(time.time() - t0, 2)
    log.info(
        f"RES ip={ip} q=\"{query}\" arquivo={dados['arquivo']} "
        f"tempo={dados['tempo_s']}s total={total}s"
    )

    if not dados["arquivo"]:
        return jsonify({"erro": "Falha ao capturar screenshot"}), 500

    return jsonify(
        {
            "query": dados["query"],
            "url": dados["url"],
            "foto": {
                "arquivo": dados["arquivo"],
                "url": f"/photos/{dados['arquivo']}",
            },
            "meta": {
                "tempo_s": dados["tempo_s"],
                "ip": ip,
            },
        }
    )


# =============================================================================
# 7. ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
