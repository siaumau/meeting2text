import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

from export_word import export_docx

load_dotenv()

import os

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
WHISPER_MODEL = "whisper-large-v3-turbo"
LLM_MODEL = "openai/gpt-oss-120b"
MAX_BYTES = 25 * 1024 * 1024

# Groq on-demand tier 的 TPM（每分鐘 token 數）上限較低（約 8000），
# 長逐字稿必須切段分批送出，避免單次請求就超過限制。
CHUNK_CHAR_LIMIT = 3000

MINUTES_PROMPT = """你是專業的會議紀錄助理。請根據以下會議逐字稿，用繁體中文整理成結構化的會議紀錄，嚴格使用以下格式（Markdown）：

# 會議紀錄

## 重點摘要
- （3~5 點）

## 討論事項
1. （條列重要討論內容）

## 決議事項
1. （已達成的結論；若無則寫「無」）

## 待辦事項
| 項目 | 負責人 | 期限 |
| --- | --- | --- |
| （若無則寫「無」） | | |

## 待確認事項
- （逐字稿中語意不清、需要向與會者確認的部分；若無則寫「無」）

注意：逐字稿由 AI 語音辨識產生，可能有錯字或同音誤植，請依上下文合理推斷並修正，但不要自行捏造未提及的內容。

逐字稿：
---
{transcript}
---"""


def get_client() -> OpenAI:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or "貼上" in api_key:
        sys.exit("錯誤：請先在 .env 設定 GROQ_API_KEY")
    return OpenAI(api_key=api_key, base_url=GROQ_BASE_URL)


def resolve_ffmpeg() -> str:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        candidate = Path(bundled) / "ffmpeg.exe"
        if candidate.exists():
            return str(candidate)
    found = shutil.which("ffmpeg")
    if found:
        return found
    sys.exit("錯誤：找不到 ffmpeg，請安裝後再試")


GROQ_ALLOWED_EXTS = {
    ".flac",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".m4a",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}


def needs_conversion(audio_path: Path) -> bool:
    size = audio_path.stat().st_size
    ext = audio_path.suffix
    return (
        size > MAX_BYTES or ext.lower() not in GROQ_ALLOWED_EXTS or ext != ext.lower()
    )


def prepare_audio(audio_path: Path, dest_dir: Path | None = None) -> Path:
    if not needs_conversion(audio_path):
        return audio_path
    size_mb = audio_path.stat().st_size / 1024 / 1024
    ext = audio_path.suffix or "無副檔名"
    dest = dest_dir or Path("output")
    dest.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    def convert(target_ext: str, codec_args: list[str]) -> Path:
        out = dest / f"{audio_path.stem}.{target_ext}"
        proc = subprocess.run(
            [
                resolve_ffmpeg(),
                "-y",
                "-i",
                str(audio_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                *codec_args,
                str(out),
            ],
            check=False,
            capture_output=True,
            creationflags=flags,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "ignore")
            raise RuntimeError(f"音檔轉檔失敗（ffmpeg 錯誤）:\n{err[-800:]}")
        return out

    # 策略一：轉成 opus（壓縮率最高、whisper 原生支援）
    print(f"[0/2] 音檔 {size_mb:.1f}MB（{ext}）需轉檔，先轉成 opus 壓縮 ...")
    compressed = convert("opus", ["-c:a", "libopus", "-b:a", "16k"])

    # 若 opus 仍超過 25MB，再降 bitrate 轉 mp3
    if compressed.stat().st_size > MAX_BYTES:
        print("  opus 仍偏大，改轉 mp3 並降低音質確保 25MB 內 ...")
        try:
            compressed.unlink()
        except OSError:
            pass
        compressed = convert("mp3", ["-c:a", "libmp3lame", "-b:a", "16k"])

    new_size = compressed.stat().st_size
    if new_size > MAX_BYTES:
        raise RuntimeError(
            f"轉檔後仍為 {new_size / 1024 / 1024:.1f}MB，超過 Groq 免費上限 25MB。"
            "音檔可能非常長，請先分段處理。"
        )
    print(f"  轉檔完成：{compressed}（{new_size / 1024 / 1024:.1f}MB）")
    return compressed


def transcribe(client: OpenAI, audio_path: Path, language: str | None) -> str:
    print(f"[1/2] 使用 {WHISPER_MODEL} 轉文字中 ...")
    with audio_path.open("rb") as f:
        if language:
            resp = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=(audio_path.name, f),
                temperature=0,
                language=language,
            )
        else:
            resp = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=(audio_path.name, f),
                temperature=0,
            )
    text = resp.text
    if not text.strip():
        sys.exit("錯誤：轉文字結果為空")
    return text


PARTIAL_PROMPT = """你是專業的會議紀錄助理。以下是一段會議逐字稿的「部分內容」（第 {idx}/{total} 段），
請用繁體中文條列整理這一段出現的重點：討論內容、已達成的結論、待辦事項（含負責人與期限，若有提到）、需要向與會者確認的模糊之處。
只整理這一段實際出現的內容，不要編造，也不要下總結標題。

逐字稿片段：
---
{chunk}
---"""

MERGE_PROMPT = """你是專業的會議紀錄助理。以下是同一場會議逐字稿依序分段整理出的重點筆記，請將它們合併、去除重複，
整理成一份完整的繁體中文會議紀錄，嚴格使用以下格式（Markdown）：

# 會議紀錄

## 重點摘要
- （3~5 點）

## 討論事項
1. （條列重要討論內容）

## 決議事項
1. （已達成的結論；若無則寫「無」）

## 待辦事項
| 項目 | 負責人 | 期限 |
| --- | --- | --- |
| （若無則寫「無」） | | |

## 待確認事項
- （語意不清、需要向與會者確認的部分；若無則寫「無」）

分段筆記：
---
{notes}
---"""


def split_transcript(transcript: str, limit: int = CHUNK_CHAR_LIMIT) -> list[str]:
    sentences = re.split(r"(?<=[。！？\n])", transcript)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > limit:
            chunks.append(current)
            current = sentence
        else:
            current += sentence
    if current.strip():
        chunks.append(current)
    return chunks or [transcript]


def call_llm(client: OpenAI, system: str, user: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
            )
            return resp.choices[0].message.content or ""
        except RateLimitError:
            if attempt == retries - 1:
                raise
            print("  觸發速率限制，等待 20 秒後重試 ...")
            time.sleep(20)
    return ""


def summarize(client: OpenAI, transcript: str) -> str:
    system = "你是一位擅長整理會議紀錄的助理，輸出簡潔、條理分明的繁體中文。"
    chunks = split_transcript(transcript)

    if len(chunks) == 1:
        print(f"[2/2] 使用 {LLM_MODEL} 整理會議紀錄中 ...")
        return call_llm(client, system, MINUTES_PROMPT.format(transcript=transcript))

    print(
        f"[2/2] 逐字稿較長，切成 {len(chunks)} 段分批整理（避免超過 Groq 每分鐘 token 限制）..."
    )
    notes: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        print(f"  整理第 {i}/{len(chunks)} 段 ...")
        note = call_llm(
            client,
            system,
            PARTIAL_PROMPT.format(idx=i, total=len(chunks), chunk=chunk),
        )
        notes.append(f"【第 {i} 段】\n{note}")
        if i < len(chunks):
            time.sleep(8)

    print("  合併分段筆記為最終會議紀錄 ...")
    return call_llm(client, system, MERGE_PROMPT.format(notes="\n\n".join(notes)))


def main() -> None:
    out: Any = sys.stdout
    err: Any = sys.stderr
    out.reconfigure(encoding="utf-8")
    err.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="錄音轉文字 + 會議紀錄整理（Groq 免費模型）"
    )
    parser.add_argument("audio", help="音檔路徑（m4a/mp3/wav/ogg/webm 等）")
    parser.add_argument("--language", help="指定語言代碼，例如 zh（預設自動偵測）")
    parser.add_argument(
        "--reuse-transcript",
        action="store_true",
        help="若 output 已有同名逐字稿則跳過轉文字步驟",
    )
    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        sys.exit(f"錯誤：找不到檔案 {audio_path}")

    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    stem = audio_path.stem

    client = get_client()
    transcript_path = out_dir / f"{stem}_transcript.txt"

    if args.reuse_transcript and transcript_path.exists():
        transcript = transcript_path.read_text(encoding="utf-8")
        print("[1/2] 重用既有逐字稿：" + str(transcript_path))
    else:
        audio_for_api = prepare_audio(audio_path)
        transcript = transcribe(client, audio_for_api, args.language)
        transcript_path.write_text(transcript, encoding="utf-8")
        print(f"  已儲存逐字稿：{transcript_path}")

    minutes = summarize(client, transcript)

    minutes_path = out_dir / f"{stem}_minutes.md"
    minutes_path.write_text(minutes, encoding="utf-8")
    print(f"  已儲存會議紀錄：{minutes_path}")

    docx_path = out_dir / f"{stem}_minutes.docx"
    export_docx(minutes_path, docx_path)
    print(f"  已匯出 Word：{docx_path}")

    print("\n========== 會議紀錄 ==========\n")
    print(minutes)


if __name__ == "__main__":
    main()
