# =============================================================================
# ZEARCH WEB - ZW-1 PROFESSIONAL (Flash Mode)
# Servidor Flask (async) + Playwright mobile + Render-ready
# - 1 modo só: Flash (rápido, sem quebrar)
# - Browser persistente (abre uma vez, reusa entre requests)
# - Logs detalhados por requisição (IP, query, tempo, tamanho)
# - Foto do viewport na resposta (base64)
# =============================================================================

import os
import re
import time
import base64
import asyncio
import logging
import psutil
from datetime import datetime
from urllib.parse import quote_plus

from flask import Flask, request, jsonify
from playwright.async_api import async_playwright

# =============================================================================
# 1. CONFIGURAÇÃO
# =============================================================================

app = Flask(__name__)

PORT = int(os.environ.get("PORT", 5000))
HEADLESS = True

URL_TEMPLATE = "https://www.bing.com/search?FORM=HDRSC1&q={q}"

UA_MOBILE = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Mobile Safari/537.36"
)

VIEWPORT = {"width": 412, "height": 915}
DEVICE_SCALE = 2.625

COOKIES = {
    "MUID": "1A41BE0E6A7362A90231A9916B8863E3",
    "MUIDB": "1A41BE0E6A7362A90231A9916B8863E3",
    "_EDGE_S": "SID=3B3D3BB40694616A20B32C61074E6057",
    "SRCHD": "AF=MA13QZ",
    "SRCHUID": "V=2&GUID=0BD21B52D09540DA843F3ED871C644F5&dmnchg=1",
    "SRCHUSR": "DOB=20260915&DS=1",
    "_SS": "PC=U645&SID=3B3D3BB40694616A20B32C61074E6057&R=6&RB=0&GB=0&RG=200&RP=3",
    "SRCHS": "PC=U645",
    "USRLOC": "HS=1&ELOC=LAT=-23.96164894104004|LON=-46.350807189941406|N=Santos%2C%20S%C3%A3o%20Paulo|ELT=4|",
}

BANNER = "ZEARCH WEB • ZW-1 • Professional (Flash)"


# =============================================================================
# 2. LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("zearch")


def ip_do_cliente() -> str:
    """Pega o IP real do cliente (Render usa proxy, olha X-Forwarded-For)."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "?"


# =============================================================================
# 3. JS DE EXTRAÇÃO (Flash — Copilot → Sports → Página)
# =============================================================================

JS_CAPTURAR = r"""
async () => {
  async function tryCopilot() {
    const wrapper =
        document.querySelector('#b_mcw') ||
        document.querySelector('#copans_container');
    if (!wrapper) return null;

    const caMain =
        wrapper.querySelector('#ca_main') ||
        wrapper.querySelector('.ca_main') ||
        wrapper;
    if (!caMain || (caMain.innerText || '').trim().length < 100) return null;

    if (caMain.querySelector('.bsp_mgz_schedule, .bsp_mgz_standings, #b_wpt_container')) {
      return null;
    }

    const clone = caMain.cloneNode(true);
    clone.querySelectorAll([
      'script', 'style', 'noscript', 'iframe',
      '.b_ad', '.sb_ad', 'footer', '.rms_img',
      '.gs_infobbl', '#gs_infobbl',
      '.gs_ai_disclaimer',
      '.gs_secctrl', '.gs_secctrl_items',
      'acf-thumbs-up-down-feedback',
      'acf-button-standard',
      '.b_acf_answer_expansion_control',
      '.b_module_expansion_control',
      '.b_gs_top_gradient', '.b_gs_bottom_cover',
      '.gs_sm_cit', '.gs_sup_cit',
      '.gs_mdlink', '.gs_cit_txt',
    ].join(',')).forEach(el => el.remove());

    let texto = (clone.innerText || clone.textContent || '').trim();

    const lixos = [
      /Este resumo foi gerado pela IA[^\n]*/gi,
      /Localize os links de origem[^\n]*/gi,
      /Saiba mais sobre os resultados[^\n]*/gi,
      /como o\s+Bing entrega os resultados da pesquisa/gi,
      /Curtir\s*Não gosto/gi,
      /Com base em fontes/gi,
      /^\s*Saiba mais\s*$/gim,
    ];
    for (const r of lixos) texto = texto.replace(r, '');

    texto = texto.split(/Wikipedia\s*›\s*wiki/i)[0].trim();
    texto = texto.split(/Mostrar tudo\s*Referências/i)[0].trim();
    texto = texto.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();

    const links = Array.from(wrapper.querySelectorAll('a'))
      .filter(a => a.href && a.href.startsWith('http') && !a.href.includes('bing.com'))
      .map(a => a.href);
    const sources = [...new Set(links)];

    return { modo: 'copilot', text: texto, sources: sources, jogos: [] };
  }

  async function trySports() {
    const ac = document.querySelector('.answer_container');
    if (!ac) return null;
    const container = ac.querySelector('#b_wpt_container') || ac;
    if (!container) return null;

    const jogos = [];
    const cards = container.querySelectorAll('.bsp_schedule_mtch_crd .bsp_match_card');

    cards.forEach(card => {
      try {
        const compEl = card.querySelector('.bsp_mtc_tps div');
        const competicao = compEl ? (compEl.getAttribute('title') || compEl.innerText.trim()) : '';
        const times = card.querySelectorAll('.bsp_team');
        if (times.length < 2) return;
        const time_casa = (times[0].querySelector('.team-name-ellipsis')?.innerText || '').trim();
        const time_fora = (times[1].querySelector('.team-name-ellipsis')?.innerText || '').trim();
        const scores = card.querySelectorAll('.bsp_team_scr');
        const placar_casa = scores[0] ? scores[0].innerText.trim() : '';
        const placar_fora = scores[1] ? scores[1].innerText.trim() : '';
        const status = (card.querySelector('.bsp_game_info > div:first-child')?.innerText || '').trim();
        const data = (card.querySelector('.bsp_game_time')?.innerText || '').trim();
        jogos.push({
          competicao, time_casa, time_fora,
          placar: `${placar_casa} - ${placar_fora}`,
          status, data, horario: 'N/A'
        });
      } catch (e) {}
    });

    if (jogos.length === 0) return null;
    return { modo: 'sports', text: container.innerText.trim(), sources: [], jogos };
  }

  async function tryWholePage() {
    const clone = document.body.cloneNode(true);
    clone.querySelectorAll([
      'script', 'style', 'noscript', 'iframe', 'svg', 'head', 'meta',
      'link', 'template', 'object', 'embed', 'canvas',
      '.b_ad', '.sb_ad', '.b_adTop', '.b_adBottom',
      '.rms_img', 'img', 'video', 'audio',
      '#b_header', '#b_footer', 'header', 'nav', 'footer',
      '.b_hide', '[aria-hidden="true"]',
    ].join(',')).forEach(el => el.remove());

    let texto = (clone.innerText || clone.textContent || '').trim();
    const lixos = [
      /Este resumo foi gerado pela IA[^\n]*/gi,
      /Localize os links de origem[^\n]*/gi,
      /Saiba mais sobre os resultados[^\n]*/gi,
      /Curtir\s*Não gosto/gi,
      /Com base em fontes/gi,
      /^\s*Saiba mais\s*$/gim,
      /^\s*Veja mais\s*$/gim,
      /^\s*Feedback\s*$/gim,
    ];
    for (const r of lixos) texto = texto.replace(r, '');
    texto = texto.split('Nova pesquisa')[0].trim();
    texto = texto.split('Experimente a Pesquisa Visual')[0].trim();
    texto = texto.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();

    if (texto.length < 50) return null;

    const links = Array.from(document.body.querySelectorAll('a'))
      .filter(a => a.href && a.href.startsWith('http') && !a.href.includes('bing.com'))
      .map(a => a.href);
    const sources = [...new Set(links)];

    return { modo: 'pagina', text: texto, sources: sources, jogos: [] };
  }

  // Flash: tenta Copilot (até 6s) → Sports → Página
  return await new Promise((resolve) => {
    let attempts = 0;
    const interval = setInterval(async () => {
      attempts++;
      const c = await tryCopilot();
      if (c) { clearInterval(interval); resolve(c); return; }
      if (attempts > 12) {
        const s = await trySports();
        if (s) { clearInterval(interval); resolve(s); return; }
        const p = await tryWholePage();
        clearInterval(interval);
        resolve(p || { modo: 'nenhum', text: '', sources: [], jogos: [] });
      }
    }, 500);
  });
}
"""


# =============================================================================
# 4. HELPERS
# =============================================================================


def limpar_texto(texto: str) -> str:
    linhas, vazias = [], 0
    for linha in texto.splitlines():
        linha = linha.rstrip()
        if not linha.strip():
            vazias += 1
            if vazias <= 1:
                linhas.append("")
        else:
            vazias = 0
            linhas.append(linha)
    return "\n".join(linhas).strip()


def ram_atual_mb() -> float:
    return psutil.Process().memory_info().rss / 1024 / 1024


def ram_sistema_mb():
    vm = psutil.virtual_memory()
    return vm.used / 1024 / 1024, vm.total / 1024 / 1024


# =============================================================================
# 5. BROWSER PERSISTENTE (abre uma vez, reusa)
# =============================================================================


class BrowserPool:
    """
    Guarda 1 browser + 1 context + 1 page abertos e os reusa entre requests.
    Só recria se o Chromium morrer.
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()

    async def _criar(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-sync",
                "--no-first-run",
                "--disable-features=Translate,BackForwardCache",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=UA_MOBILE,
            viewport=VIEWPORT,
            device_scale_factor=DEVICE_SCALE,
            is_mobile=True,
            has_touch=True,
            locale="pt-BR",
            java_script_enabled=True,
        )
        await self._context.add_cookies(
            [
                {"name": k, "value": v, "domain": ".bing.com", "path": "/"}
                for k, v in COOKIES.items()
            ]
        )
        self._page = await self._context.new_page()
        log.info("BROWSER_POOL: Chromium iniciado (reuso ativado)")

    async def get_page(self):
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                await self._criar()
            return self._page

    async def warmup(self):
        """Só garante que o Chromium está aberto."""
        await self.get_page()


POOL = BrowserPool()


# =============================================================================
# 6. NÚCLEO FLASH
# =============================================================================


async def executar_pesquisa_flash(query: str) -> dict:
    """Modo flash: reaproveita browser, navega, extrai, tira foto."""
    url = URL_TEMPLATE.format(q=quote_plus(query))

    t_ini = time.time()
    ram_ini = ram_atual_mb()

    page = await POOL.get_page()

    resultado = None
    foto_b64 = None

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        await page.wait_for_timeout(800)

        # evaluate com 2 retries rápidos
        for tentativa in range(1, 3):
            try:
                resultado = await page.evaluate(JS_CAPTURAR)
                break
            except Exception as e:
                msg = str(e)
                if (
                    "Execution context was destroyed" in msg
                    or "navigation" in msg.lower()
                ) and tentativa < 2:
                    await page.wait_for_timeout(1000)
                    continue
                raise

        # Foto do viewport (sem full_page pra ser rápido)
        try:
            shot = await page.screenshot(full_page=False, type="png")
            foto_b64 = base64.b64encode(shot).decode("ascii")
        except Exception:
            foto_b64 = None

        if resultado is None:
            resultado = {
                "modo": "erro",
                "text": "Sem resultado",
                "sources": [],
                "jogos": [],
            }

    except Exception as e:
        import traceback

        traceback.print_exc()
        resultado = {
            "modo": "erro",
            "text": f"Falha: {type(e).__name__}: {e}",
            "sources": [],
            "jogos": [],
        }

    t_total = time.time() - t_ini
    ram_fim = ram_atual_mb()

    modo = (resultado or {}).get("modo", "nenhum")
    texto = limpar_texto((resultado or {}).get("text", "") or "")
    fontes = (resultado or {}).get("sources", []) or []
    jogos = (resultado or {}).get("jogos", []) or []

    return {
        "query": query,
        "url": url,
        "modo": modo,
        "texto": texto,
        "fontes": fontes,
        "jogos": jogos,
        "foto_b64": foto_b64,
        "tempo_s": round(t_total, 3),
        "ram_delta_mb": round(ram_fim - ram_ini, 2),
    }


# =============================================================================
# 7. ROTAS
# =============================================================================


@app.route("/", methods=["GET"])
def raiz():
    return jsonify(
        {
            "status": "ok",
            "service": BANNER,
            "endpoints": {
                "GET /": "status",
                "GET /search?q=<termo>": "pesquisa flash",
                "POST /search": 'mesmo, JSON body {"q":"..."}',
                "GET /warmup": "esquenta o Chromium após deploy",
            },
        }
    )


@app.route("/warmup", methods=["GET"])
def warmup():
    """Chame isso logo após o deploy pra deixar o Chromium pronto."""
    try:
        asyncio.run(POOL.warmup())
        return jsonify({"status": "ok", "browser": "warm"})
    except Exception as e:
        return jsonify({"status": "erro", "erro": f"{type(e).__name__}: {e}"}), 500


@app.route("/search", methods=["GET"])
def rota_search_get():
    t0 = time.time()
    ip = ip_do_cliente()
    query = (request.args.get("q") or "").strip()

    if not query:
        log.warning(f"REQ ip={ip} GET /search SEM query")
        return jsonify({"error": "Parâmetro 'q' é obrigatório"}), 400

    log.info(f'REQ ip={ip} GET /search q="{query}"')

    dados = asyncio.run(executar_pesquisa_flash(query))

    log.info(
        f"RES ip={ip} q=\"{query}\" modo={dados['modo']} "
        f"chars={len(dados['texto'])} fontes={len(dados['fontes'])} "
        f"jogos={len(dados['jogos'])} tempo={dados['tempo_s']}s "
        f"total={round(time.time() - t0, 2)}s"
    )

    return jsonify(
        {
            "response": dados["texto"],
            "fontes": dados["fontes"],
            "jogos": dados["jogos"],
            "photo": dados["foto_b64"],
            "meta": {
                "query": dados["query"],
                "url": dados["url"],
                "modo": dados["modo"],
                "chars": len(dados["texto"]),
                "tempo_s": dados["tempo_s"],
                "ram_delta_mb": dados["ram_delta_mb"],
                "ip": ip,
                "capturado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
    )


@app.route("/search", methods=["POST"])
def rota_search_post():
    t0 = time.time()
    ip = ip_do_cliente()
    payload = request.get_json(silent=True) or {}
    query = (payload.get("q") or payload.get("query") or "").strip()

    if not query:
        log.warning(f"REQ ip={ip} POST /search SEM query")
        return jsonify({"error": "Campo 'q' é obrigatório"}), 400

    log.info(f'REQ ip={ip} POST /search q="{query}"')

    dados = asyncio.run(executar_pesquisa_flash(query))

    log.info(
        f"RES ip={ip} q=\"{query}\" modo={dados['modo']} "
        f"chars={len(dados['texto'])} fontes={len(dados['fontes'])} "
        f"jogos={len(dados['jogos'])} tempo={dados['tempo_s']}s "
        f"total={round(time.time() - t0, 2)}s"
    )

    return jsonify(
        {
            "response": dados["texto"],
            "fontes": dados["fontes"],
            "jogos": dados["jogos"],
            "photo": dados["foto_b64"],
            "meta": {
                "query": dados["query"],
                "url": dados["url"],
                "modo": dados["modo"],
                "chars": len(dados["texto"]),
                "tempo_s": dados["tempo_s"],
                "ram_delta_mb": dados["ram_delta_mb"],
                "ip": ip,
                "capturado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
    )


# =============================================================================
# 8. ENTRYPOINT (local — Render usa gunicorn)
# =============================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
