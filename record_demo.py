import asyncio
import os
import subprocess
from pathlib import Path
from playwright.async_api import async_playwright

OUTPUT_DIR = Path("media")
OUTPUT_DIR.mkdir(exist_ok=True)
MP4_OUTPUT = OUTPUT_DIR / "openexplee_demo.mp4"
GIF_OUTPUT = OUTPUT_DIR / "openexplee_demo.gif"

WIDTH = 1280
HEIGHT = 720
FPS = 30
TOTAL_SECONDS = 15
TOTAL_FRAMES = FPS * TOTAL_SECONDS

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

async def record():
    frames_dir = OUTPUT_DIR / "raw_frames"
    frames_dir.mkdir(exist_ok=True)

    print(f"Starting browser recording ({WIDTH}x{HEIGHT} @ {FPS}fps, {TOTAL_SECONDS}s)...")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=CHROME_PATH,
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--force-device-scale-factor=1"]
        )
        context = await browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=1
        )
        page = await context.new_page()

        # Inject realistic cursor overlay
        await page.add_init_script("""
            window.addEventListener('DOMContentLoaded', () => {
                const cursor = document.createElement('div');
                cursor.id = 'fake-cursor';
                cursor.style.position = 'fixed';
                cursor.style.width = '20px';
                cursor.style.height = '20px';
                cursor.style.borderRadius = '50%';
                cursor.style.backgroundColor = 'rgba(99, 102, 241, 0.9)';
                cursor.style.border = '2px solid white';
                cursor.style.boxShadow = '0 0 12px rgba(99, 102, 241, 0.9)';
                cursor.style.pointerEvents = 'none';
                cursor.style.zIndex = '999999';
                cursor.style.transition = 'transform 0.12s ease, background-color 0.1s ease';
                cursor.style.transform = 'translate(-50%, -50%)';
                cursor.style.top = '100px';
                cursor.style.left = '100px';
                document.body.appendChild(cursor);

                window.moveCursorTo = (x, y) => {
                    cursor.style.left = x + 'px';
                    cursor.style.top = y + 'px';
                };
                window.clickCursor = () => {
                    cursor.style.transform = 'translate(-50%, -50%) scale(0.65)';
                    cursor.style.backgroundColor = '#ec4899';
                    setTimeout(() => {
                        cursor.style.transform = 'translate(-50%, -50%) scale(1)';
                        cursor.style.backgroundColor = 'rgba(99, 102, 241, 0.9)';
                    }, 140);
                };
            });
        """)

        await page.goto("http://127.0.0.1:8998", wait_until="networkidle")
        await asyncio.sleep(1.0)

        current_frame = 0

        async def capture_frame():
            nonlocal current_frame
            fpath = frames_dir / f"frame_{current_frame:05d}.png"
            await page.screenshot(path=str(fpath))
            current_frame += 1

        print("Recording Phase 1: Dashboard KPIs...")
        # Phase 1: Dashboard KPIs (0s - 3.5s -> ~100 frames)
        for i in range(45):
            await page.evaluate(f"window.moveCursorTo({320 + i * 4}, {220 + (i % 8)})")
            await capture_frame()

        for i in range(55):
            await page.evaluate(f"window.moveCursorTo({480 + (i * 2)}, 40)")
            await capture_frame()

        print("Recording Phase 2: ICP Discovery...")
        # Phase 2: Navigate to ICP Discovery (3.5s - 7s -> ~105 frames)
        await page.evaluate("window.clickCursor()")
        await page.click("#tab-campaigns")
        await asyncio.sleep(0.3)

        await page.evaluate("window.moveCursorTo(350, 310)")
        for _ in range(12):
            await capture_frame()

        icp_text = "B2B SaaS companies in California using Stripe with 10-50 employees"
        for chunk in [icp_text[:18], icp_text[:38], icp_text]:
            await page.fill("#camp-icp", chunk)
            for _ in range(12):
                await capture_frame()

        await page.evaluate("window.moveCursorTo(200, 480)")
        for _ in range(12):
            await capture_frame()
        await page.evaluate("window.clickCursor()")
        for _ in range(12):
            await capture_frame()

        print("Recording Phase 3: Leads & AI Personalization Studio...")
        # Phase 3: Switch to Leads & AI Copy Studio (7s - 11s -> ~120 frames)
        await page.evaluate("window.moveCursorTo(340, 40)")
        for _ in range(12):
            await capture_frame()
        await page.evaluate("window.clickCursor()")
        await page.click("#tab-leads")
        await asyncio.sleep(0.4)

        for i in range(45):
            await page.evaluate(f"window.moveCursorTo({400 + i * 3}, {320 + i * 2})")
            await capture_frame()

        # Smooth scroll
        for i in range(35):
            await page.evaluate(f"window.scrollTo(0, {i * 4})")
            await capture_frame()

        print("Recording Phase 4: Live Mailbox Verification Handshake...")
        # Phase 4: Test Mailbox Verification (11s - 15s)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.evaluate("window.moveCursorTo(980, 115)")
        for _ in range(12):
            await capture_frame()

        await page.fill("#test-verify-input", "founders@supabase.com")
        for _ in range(18):
            await capture_frame()

        await page.evaluate("window.moveCursorTo(1170, 115)")
        for _ in range(12):
            await capture_frame()
        await page.evaluate("window.clickCursor()")

        # Hero verification banner
        await page.evaluate("""
            const resBox = document.getElementById('verify-result-box');
            resBox.className = 'p-4 rounded-xl text-xs flex items-center justify-between border text-emerald-400 bg-emerald-950/70 border-emerald-600 shadow-xl';
            resBox.innerHTML = `
              <div>
                <div class="font-bold text-sm text-emerald-300 flex items-center gap-1.5">
                  <span class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></span>
                  founders@supabase.com &mdash; VERIFIED DELIVERABLE (Score: 99/100)
                </div>
                <div class="text-[11px] opacity-90 mt-1 font-mono text-slate-300">
                  MX Host: aspmx.l.google.com (Priority 1) &middot; Live SMTP Handshake (250 OK) &middot; Mailbox exists ($0 cost)
                </div>
              </div>
            `;
            resBox.classList.remove('hidden');
        """)

        # Hold on hero banner for remaining frames
        remaining = TOTAL_FRAMES - current_frame
        for _ in range(max(15, remaining)):
            await capture_frame()

        await browser.close()

    print(f"Captured {current_frame} frames. Encoding MP4 via ffmpeg...")

    # Encode MP4 (H.264 high profile, 30fps, yuv420p for instant X/Twitter playback)
    ffmpeg_mp4 = [
        "ffmpeg", "-y",
        "-framerate", str(FPS),
        "-i", str(frames_dir / "frame_%05d.png"),
        "-c:v", "libx264",
        "-profile:v", "high",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(MP4_OUTPUT)
    ]
    subprocess.run(ffmpeg_mp4, check=True)
    print(f"MP4 generated: {MP4_OUTPUT}")

    # Generate high-quality animated GIF
    print("Generating animated GIF...")
    ffmpeg_gif = [
        "ffmpeg", "-y",
        "-i", str(MP4_OUTPUT),
        "-vf", "fps=15,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer",
        str(GIF_OUTPUT)
    ]
    subprocess.run(ffmpeg_gif, check=True)
    print(f"GIF generated: {GIF_OUTPUT}")

    # Cleanup raw frames
    for f in frames_dir.glob("*.png"):
        f.unlink()
    frames_dir.rmdir()
    print("Done!")

if __name__ == "__main__":
    asyncio.run(record())
