"""
frontend_design.py — Geração de imagens para redes sociais via HTML → PNG.

Tier: POWERFUL
Input:
  format        (str, obrigatório) — "instagram_post" | "instagram_carousel" |
                                     "story" | "instagram_feed"
  theme         (str, obrigatório) — tema visual (ex: "minimalista escuro", "tropical vibrante")
  content       (str, obrigatório) — conteúdo a exibir (título, texto, CTA)
  brand_colors  (list[str], default []) — cores em hex (ex: ["#FF6B35", "#1A1A2E"])
  output_path   (str, default "") — caminho para salvar PNG (usa tmp/ se vazio)
  nexus_dir     (str, default "/opt/nexus") — diretório base

Output: caminho do arquivo PNG gerado (str) ou mensagem de erro
"""

import json
import re
import subprocess
import tempfile
from pathlib import Path

DESCRIPTION = "Gera imagens para Instagram/Stories via HTML→PNG (Playwright)"
DEFAULT_TIER = "powerful"

_FORMATS = {
    "instagram_post":     (1080, 1080),
    "instagram_feed":     (1080, 1080),
    "instagram_carousel": (1080, 1440),
    "story":              (1080, 1920),
}

_BP_FEED    = Path(__file__).parent.parent / "_opensquad/core/best-practices/instagram-feed.md"
_BP_STORIES = Path(__file__).parent.parent / "_opensquad/core/best-practices/instagram-stories.md"
_BP_IMAGE   = Path(__file__).parent.parent / "skills/image-creator/SKILL.md"
_BP_TMPL    = Path(__file__).parent.parent / "skills/template-designer/SKILL.md"


def _load_bp(path: Path, max_chars: int = 1000) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")[:max_chars]
    return ""


def _build_bp_block(fmt: str) -> str:
    parts = []
    if "story" in fmt:
        bp = _load_bp(_BP_STORIES)
        if bp:
            parts.append(f"DIRETRIZES STORIES:\n{bp}")
    else:
        bp = _load_bp(_BP_FEED)
        if bp:
            parts.append(f"DIRETRIZES FEED:\n{bp}")
    image_bp = _load_bp(_BP_IMAGE)
    if image_bp:
        parts.append(f"DIRETRIZES IMAGE CREATOR:\n{image_bp}")
    tmpl_bp = _load_bp(_BP_TMPL)
    if tmpl_bp:
        parts.append(f"DIRETRIZES TEMPLATE DESIGNER:\n{tmpl_bp}")
    return "\n\n".join(parts)


def _render_png(html: str, output_path: Path, width: int, height: int) -> bool:
    """Renderiza HTML para PNG usando Playwright."""
    try:
        script = (
            "const {{ chromium }} = require('playwright');\n"
            "(async () => {{\n"
            "  const browser = await chromium.launch();\n"
            "  const page = await browser.newPage();\n"
            "  await page.setViewportSize({{ width: {w}, height: {h} }});\n"
            "  await page.setContent(`{html}`, {{ waitUntil: 'networkidle' }});\n"
            "  await page.screenshot({{ path: '{out}', fullPage: false }});\n"
            "  await browser.close();\n"
            "}})();\n"
        ).format(
            w=width, h=height,
            html=html.replace("`", "\\`").replace("${", "\\${"),
            out=str(output_path),
        )
        with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False, encoding="utf-8") as f:
            f.write(script)
            script_path = f.name

        result = subprocess.run(
            ["node", script_path],
            capture_output=True, text=True, timeout=30,
        )
        Path(script_path).unlink(missing_ok=True)
        return result.returncode == 0 and output_path.exists()
    except Exception:
        return False


def _extract_html(text: str) -> str:
    """Extrai HTML de resposta do modelo."""
    m = re.search(r'```html\s*(.*?)\s*```', text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r'(<!DOCTYPE.*?</html>)', text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1)
    return text


def run(context: dict, provider) -> str:
    fmt = context.get("format", "")
    theme = context.get("theme", "")
    content = context.get("content", "")

    if not all([fmt, theme, content]):
        return "**Erro:** Parâmetros `format`, `theme` e `content` são obrigatórios."

    fmt = fmt.lower().replace("-", "_").replace(" ", "_")
    if fmt not in _FORMATS:
        return f"**Erro:** Formato inválido. Use: {', '.join(_FORMATS.keys())}"

    width, height = _FORMATS[fmt]
    brand_colors = context.get("brand_colors", [])
    nexus_dir = Path(context.get("nexus_dir", "/opt/nexus"))
    output_path_str = context.get("output_path", "")
    tier = context.get("_tier", DEFAULT_TIER)

    if output_path_str:
        output_path = Path(output_path_str)
    else:
        output_path = nexus_dir / "tmp" / f"design_{fmt}_{hash(content) % 99999:05d}.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    bp_block = _build_bp_block(fmt)
    colors_str = f"Cores da marca: {', '.join(brand_colors)}" if brand_colors else ""

    prompt = (
        f"Crie um design visual para {fmt.replace('_', ' ')} ({width}×{height}px).\n\n"
        f"TEMA VISUAL: {theme}\n"
        f"CONTEÚDO: {content}\n"
        f"{colors_str}\n\n"
        f"{bp_block}\n\n"
        f"ESPECIFICAÇÕES TÉCNICAS:\n"
        f"- Tamanho: {width}×{height}px\n"
        f"- Tipografia mínima: Hero 58px, Heading 43px, Body 34px, Caption 24px\n"
        f"- Google Fonts via @import no CSS (não usar <link>)\n"
        f"- CSS inline no <style>, sem arquivos externos\n"
        f"- Layout com visual impact — hierarquia clara\n"
        f"- Fundo com gradiente ou cor sólida impactante\n\n"
        f"Gere um HTML completo e self-contained (<!DOCTYPE html>.....</html>).\n"
        f"Retorne APENAS o HTML dentro de ```html ... ```"
    )

    html_response = provider.generate(prompt, tier=tier)
    html = _extract_html(html_response)

    if not html.strip().startswith("<!"):
        html = f"<!DOCTYPE html><html><head><style>body{{margin:0;width:{width}px;height:{height}px;overflow:hidden;}}</style></head><body>{html}</body></html>"

    # Salva HTML auxiliar
    html_path = output_path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")

    # Tenta renderizar PNG
    rendered = _render_png(html, output_path, width, height)

    if rendered:
        return str(output_path)
    else:
        # Playwright não disponível — retorna caminho do HTML para preview manual
        return (
            f"⚠️ Playwright não disponível. HTML gerado em: `{html_path}`\n\n"
            f"Para renderizar: `npx playwright screenshot --viewport-size={width},{height} {html_path} {output_path}`"
        )
