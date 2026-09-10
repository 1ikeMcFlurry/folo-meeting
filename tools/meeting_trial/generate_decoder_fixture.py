"""Generate non-speech MP3 used by the Android codec test. No microphones or downloads."""
from pathlib import Path
import array
import math
import subprocess

root = Path(__file__).resolve().parents[2]
output = root / "companion/android/app/src/androidTest/assets/decoder-tones.mp3"
output.parent.mkdir(exist_ok=True, parents=True)
pcm = array.array("h", (round(9800 * math.sin(2 * math.pi * (440 if i < 480000 else 880) * i / 48000))
                        for i in range(960000) for channel in range(2)))
subprocess.run(["D:/Software/ffmpeg/bin/ffmpeg.exe", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", "pipe:0",
                "-codec:a", "libmp3lame", "-b:a", "128k", str(output)], input=pcm.tobytes(), check=True)
print("Generated decoder test fixture:", output)
