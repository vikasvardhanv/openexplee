import subprocess
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OPENEXPLEE_DIR = Path("/Users/vikashvardhan/IdeaProjects/openExplee")
MEDIA_DIR = OPENEXPLEE_DIR / "media"
INPUT_DEMO = MEDIA_DIR / "openexplee_demo.mp4"
OUTPUT_SHORT = MEDIA_DIR / "openexplee_short_9x16.mp4"
TEMP_DIR = Path("/tmp/openexplee_short_build")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

WIDTH = 1080
HEIGHT = 1920
FPS = 30

def create_overlays():
    """Generates high-contrast vertical graphics for the Short."""
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (10, 15, 29, 255))
    draw = ImageDraw.Draw(overlay)

    # Gradient background lighting accents
    # Top glow (indigo/purple)
    for r in range(400, 0, -10):
        alpha = int(35 * (r / 400))
        draw.ellipse([WIDTH//2 - r, 200 - r//2, WIDTH//2 + r, 200 + r//2], fill=(79, 70, 229, alpha))

    # Bottom glow (emerald)
    for r in range(350, 0, -10):
        alpha = int(25 * (r / 350))
        draw.ellipse([WIDTH//2 - r, 1500 - r//2, WIDTH//2 + r, 1500 + r//2], fill=(16, 185, 129, alpha))

    # Try loading clean system fonts
    font_paths = [
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf"
    ]
    font_bold_path = font_paths[0]
    for p in font_paths:
        if os.path.exists(p):
            font_bold_path = p
            break

    try:
        font_pill = ImageFont.truetype(font_bold_path, 34)
        font_headline = ImageFont.truetype(font_bold_path, 66)
        font_sub = ImageFont.truetype(font_bold_path, 42)
        font_cta_header = ImageFont.truetype(font_bold_path, 52)
        font_cta_keyword = ImageFont.truetype(font_bold_path, 72)
        font_cta_sub = ImageFont.truetype(font_bold_path, 36)
    except Exception:
        font_pill = font_headline = font_sub = font_cta_header = font_cta_keyword = font_cta_sub = ImageFont.load_default()

    # 1. TOP SECTION (Hook)
    # Badge Pill
    pill_box = [WIDTH//2 - 220, 140, WIDTH//2 + 220, 210]
    draw.rounded_rectangle(pill_box, radius=35, fill=(99, 102, 241, 40), outline=(129, 140, 248, 180), width=3)
    draw.text((WIDTH//2, 175), "⚡ 100% FREE & SELF-HOSTED", font=font_pill, fill=(165, 180, 252), anchor="mm")

    # Hook Headline
    draw.text((WIDTH//2, 275), "STOP PAYING $300/MO", font=font_headline, fill=(248, 113, 113), anchor="mm")
    draw.text((WIDTH//2, 350), "FOR APOLLO & EXPLEE 🚫", font=font_headline, fill=(255, 255, 255), anchor="mm")
    draw.text((WIDTH//2, 425), "I Open-Sourced The Whole Engine", font=font_sub, fill=(52, 211, 153), anchor="mm")

    # 2. CENTER FRAME MOCKUP BORDER (Demo video fits at y=520, height=607, width=1040)
    demo_box = [WIDTH//2 - 524, 516, WIDTH//2 + 524, 516 + 615]
    draw.rounded_rectangle(demo_box, radius=24, outline=(99, 102, 241, 120), width=4)

    # 3. BOTTOM SECTION (Viral Lead Magnet CTA - matching user's image)
    cta_card_box = [70, 1220, WIDTH - 70, 1680]
    draw.rounded_rectangle(cta_card_box, radius=32, fill=(18, 24, 43, 230), outline=(255, 255, 255, 30), width=2)

    # Target Icon & CTA Text
    draw.text((WIDTH//2, 1290), "🎯 WANT THE FREE CODE?", font=font_cta_header, fill=(255, 255, 255), anchor="mm")

    # Comment Button Box
    btn_box = [WIDTH//2 - 380, 1360, WIDTH//2 + 380, 1480]
    draw.rounded_rectangle(btn_box, radius=28, fill=(79, 70, 229, 255), outline=(165, 180, 252, 255), width=3)
    draw.text((WIDTH//2, 1420), 'Comment "OPEN" Below 👇', font=font_cta_keyword, fill=(255, 255, 255), anchor="mm")

    draw.text((WIDTH//2, 1540), "And we'll auto-send you the GitHub repo link!", font=font_cta_sub, fill=(203, 213, 225), anchor="mm")
    draw.text((WIDTH//2, 1610), "github.com/vikasvardhanv/openexplee", font=font_pill, fill=(99, 102, 241), anchor="mm")

    overlay_path = TEMP_DIR / "overlay.png"
    overlay.save(overlay_path)
    print(f"Overlay created at {overlay_path}")
    return overlay_path

def build_short():
    overlay_path = create_overlays()

    # 1. Generate Voiceover
    vo_script = (
        "Stop paying three hundred dollars a month for Apollo and Explee. "
        "We just open sourced the entire platform. "
        "It gives you natural language lead discovery, live mailbox verification, and AI personalization for zero dollars. "
        "Comment OPEN below and we will send you the free GitHub repo link right now."
    )
    vo_aiff = TEMP_DIR / "vo.aiff"
    vo_mp3 = TEMP_DIR / "vo.mp3"
    subprocess.run(["say", "-v", "Samantha", vo_script, "-o", str(vo_aiff)], check=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(vo_aiff), "-af", "volume=1.4", str(vo_mp3)], check=True)

    # 2. Get Audio Duration
    probe = subprocess.run([
        "ffprobe", "-i", str(vo_mp3), "-show_entries", "format=duration", "-v", "quiet", "-of", "csv=p=0"
    ], capture_output=True, text=True)
    audio_duration = float(probe.stdout.strip())
    print(f"Voiceover duration: {audio_duration} seconds")

    # 3. Composite Video using FFmpeg
    # Demo video input: scale to exactly 1040:585
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        # Input 0: Demo video (looping)
        "-stream_loop", "-1",
        "-i", str(INPUT_DEMO),
        # Input 1: Overlay PNG
        "-i", str(overlay_path),
        # Input 2: Audio voiceover
        "-i", str(vo_mp3),
        # Filter complex
        "-filter_complex",
        "[0:v]scale=1040:585[demov];"
        "[1:v][demov]overlay=x=(W-w)/2:y=530[composed]",
        "-map", "[composed]",
        "-map", "2:a",
        "-t", str(audio_duration),
        "-c:v", "libx264",
        "-profile:v", "high",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "44100",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(OUTPUT_SHORT)
    ]

    print("Rendering 9:16 Short with FFmpeg...")
    subprocess.run(ffmpeg_cmd, check=True)
    print(f"Viral 9:16 Short successfully rendered at: {OUTPUT_SHORT}")

if __name__ == "__main__":
    build_short()
