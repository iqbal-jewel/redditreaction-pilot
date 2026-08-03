"""Render Reddit-style cards: the situation post and its verdict reveal.

Facebook takes image bytes directly in the publish request, so cards are
rendered on demand and never need to be committed (unlike wildnatureusa's
Instagram cards, which must be fetchable by URL). See automation/wildnatureusa
/README.md for why that distinction matters.

    python -m src.render situation <plan_row_json>
    python -m src.render verdict <plan_row_json>
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "images"
FONTS = ROOT / "fonts"

W, H = 1080, 1350
MARGIN = 64

BG = "#FFFFFF"
CARD_BG = "#FFFFFF"
BORDER = "#EDEFF1"
TEXT = "#1A1A1B"
MUTED = "#787C7E"
ORANGE = "#FF4500"
UPVOTE = "#FF8717"
FOOTER_BG = "#FF4500"
FOOTER_FG = "#FFFFFF"
VERDICT_BG = "#1A1A1B"
VERDICT_FG = "#FFFFFF"
VERDICT_ACCENT = "#46D160"  # NTA green; swapped per-verdict in render_verdict

VERDICT_COLOR = {
    "NTA": "#46D160",
    "YTA": "#FF4500",
    "ESH": "#FFB000",
    "NAH": "#0079D3",
    "INFO": "#787C7E",
}

FONT_BOLD = FONTS / "Poppins-Bold.ttf"
FONT_SEMIBOLD = FONTS / "Poppins-SemiBold.ttf"
FONT_REGULAR = FONTS / "Poppins-Regular.ttf"


def _font(path: Path, size: int):
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fit_block(draw, text, font_path, max_width, max_height, start, minimum=22):
    """Largest size at which the wrapped text fits the box, no truncation."""
    size = start
    while size > minimum:
        font = _font(font_path, size)
        lines = _wrap(draw, text, font, max_width)
        line_h = int(size * 1.42)
        if len(lines) * line_h <= max_height:
            return font, lines, line_h
        size -= 2
    font = _font(font_path, minimum)
    lines = _wrap(draw, text, font, max_width)
    line_h = int(minimum * 1.42)
    max_lines = max(1, max_height // line_h)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while draw.textlength(last + "...", font=font) > max_width and len(last) > 1:
            last = last[:-1]
        lines[-1] = last.rstrip() + "..."
    return font, lines, line_h


def _fmt_count(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


def _footer_pill(draw, text, y, bg, fg):
    font = _font(FONT_SEMIBOLD, 34)
    tw = draw.textlength(text, font=font)
    pad_x, pad_y = 36, 20
    box = ((W - tw) / 2 - pad_x, y, (W + tw) / 2 + pad_x, y + 34 + pad_y * 2)
    draw.rounded_rectangle(box, radius=30, fill=bg)
    draw.text(((W - tw) / 2, y + pad_y), text, font=font, fill=fg)
    return box[3]


def render_situation(item: dict, out_path: Path) -> Path:
    """The AITA situation as a Reddit-post-styled card."""
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    inner = W - 2 * MARGIN

    # Header: subreddit tag + poster
    y = MARGIN
    draw.ellipse((MARGIN, y, MARGIN + 44, y + 44), fill=ORANGE)
    draw.text((MARGIN + 56, y + 4), f"r/{item['subreddit']}", font=_font(FONT_BOLD, 32), fill=TEXT)
    draw.text((MARGIN + 56, y + 42), "Posted by u/OP  ·  Reddit", font=_font(FONT_REGULAR, 24), fill=MUTED)
    y += 100

    # Badge row (Reddit's RSS feed doesn't expose live vote/comment counts,
    # so this stays qualitative rather than showing a stale or fake number)
    up_font = _font(FONT_BOLD, 28)
    draw.text((MARGIN, y), "🔥 TOP POST THIS MONTH", font=up_font, fill=UPVOTE)
    y += 56

    # Divider
    draw.line((MARGIN, y, W - MARGIN, y), fill=BORDER, width=2)
    y += 40

    # Title (bold, larger)
    title_font, title_lines, title_lh = _fit_block(
        draw, item["title"], FONT_BOLD, inner, 260, start=48, minimum=32)
    for line in title_lines:
        draw.text((MARGIN, y), line, font=title_font, fill=TEXT)
        y += title_lh
    y += 20

    # Reserve space for the footer pill so body text never collides with it.
    footer_zone = 140
    body_bottom = H - MARGIN - footer_zone
    body_font, body_lines, body_lh = _fit_block(
        draw, item["body"], FONT_REGULAR, inner, body_bottom - y, start=32, minimum=22)
    for line in body_lines:
        draw.text((MARGIN, y), line, font=body_font, fill=TEXT)
        y += body_lh

    _footer_pill(draw, "YTA or NTA? Vote in the comments ↓", H - MARGIN - 94, FOOTER_BG, FOOTER_FG)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def render_verdict(item: dict, out_path: Path) -> Path:
    """Reddit's verdict, revealed as a dark contrast card."""
    img = Image.new("RGB", (W, H), VERDICT_BG)
    draw = ImageDraw.Draw(img)
    inner = W - 2 * MARGIN
    accent = VERDICT_COLOR.get(item["verdict_code"], VERDICT_ACCENT)

    y = MARGIN
    draw.text((MARGIN, y), "REDDIT'S VERDICT", font=_font(FONT_SEMIBOLD, 30), fill=MUTED)
    y += 70

    label_font = _font(FONT_BOLD, 84)
    draw.text((MARGIN, y), item["verdict_code"], font=label_font, fill=accent)
    y += 100
    draw.text((MARGIN, y), item["verdict_label"], font=_font(FONT_SEMIBOLD, 40), fill=VERDICT_FG)
    y += 70

    draw.line((MARGIN, y, W - MARGIN, y), fill="#3A3A3B", width=2)
    y += 50

    quote_font, quote_lines, quote_lh = _fit_block(
        draw, item["verdict_text"], FONT_REGULAR, inner, H - MARGIN - 160 - y,
        start=36, minimum=24)
    for line in quote_lines:
        draw.text((MARGIN, y), line, font=quote_font, fill=VERDICT_FG)
        y += quote_lh

    _footer_pill(draw, "🏆 Top-voted reply", H - MARGIN - 94, accent, VERDICT_BG)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path
