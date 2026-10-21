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
from export_html import export_html

load_dotenv()

import os

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
WHISPER_MODEL = "whisper-large-v3-turbo"
LLM_MODEL = "openai/gpt-oss-120b"
MAX_BYTES = 25 * 1024 * 1024

# Groq on-demand tier 的 TPM（每分鐘 token 數）上限較低（約 8000），
# 長逐字稿必須切段分批送出，避免單次請求就超過限制。
CHUNK_CHAR_LIMIT = 1500

# 輸出文件類型預設：key 是 GUI/CLI 選單顯示的名稱，value 是該類型的 Markdown 結構規格。
# 使用者也可以在 GUI 選「自訂範本」自行輸入一段規格文字取代下面任一項。
FORMAT_PRESETS: dict[str, str] = {
    "會議紀錄": """# 會議紀錄

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
- （逐字稿中語意不清、需要向與會者確認的部分；若無則寫「無」）""",
    "訪談紀錄": """# 訪談紀錄

## 受訪者背景與主題
- （訪談對象、主題、目的等，若逐字稿有提及）

## 重點問答整理
- （依討論順序條列「問題 → 回覆重點」）

## 關鍵引述
- （1~3 句最具代表性的原話，若有）

## 待確認 / 待追問事項
- （語意不清或值得進一步追問的地方；若無則寫「無」）""",
    "課程筆記": """# 課程筆記

## 主題摘要
- （這堂課／這段內容在講什麼，3~5 點）

## 重點內容
1. （依邏輯順序條列重點，可分小節）

## 重要名詞 / 定義
- （課程中出現的專有名詞與其定義；若無則寫「無」）

## 延伸問題 / 待釐清
- （聽完後值得進一步查證或提問的地方；若無則寫「無」）""",
    "通用摘要": """# 內容摘要

## 摘要
- （2~4 句總結整體內容）

## 重點條列
1. （依出現順序條列重點）

## 待確認事項
- （語意不清、需要進一步確認的部分；若無則寫「無」）""",
    "影片重點整理": """# 影片重點整理

## 影片主題
- （這支影片在講什麼，2~3 句說明）

## 章節式重點
1. （依影片播出順序，將內容拆成幾個段落並條列每段重點）

## 關鍵結論 / 論點
- （影片最終想傳達的重點或結論）

## 值得查證 / 延伸閱讀
- （提到但沒展開、或值得進一步查證的地方；若無則寫「無」）""",
}

DEFAULT_TEMPLATE = "會議紀錄"

MINUTES_PROMPT = """你是專業的文件整理助理。請根據以下逐字稿內容，用繁體中文整理成結構化文件，嚴格使用以下格式（Markdown）：

{format_spec}

注意：逐字稿由 AI 語音辨識產生，可能有錯字或同音誤植，請依上下文合理推斷並修正，但不要自行捏造未提及的內容。
{context_block}
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


def sanitize_filename(name: str, max_len: int = 80) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name).strip(" ._")
    return (cleaned or "youtube_audio")[:max_len]


def download_youtube_audio(url: str, dest_dir: Path) -> tuple[Path, str]:
    """下載 YouTube 影片的音軌，回傳 (音檔路徑, 影片標題)。"""
    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_dir = str(Path(resolve_ffmpeg()).parent)
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "ffmpeg_location": ffmpeg_dir,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise RuntimeError(f"YouTube 下載失敗：{e}") from e

    video_id = info["id"]
    title = info.get("title") or video_id
    matches = sorted(dest_dir.glob(f"{video_id}.*"))
    if not matches:
        raise RuntimeError("YouTube 音訊下載失敗，找不到輸出檔案")
    return matches[0], title


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


def transcribe(
    client: OpenAI,
    audio_path: Path,
    language: str | None,
    context: str | None = None,
) -> str:
    print(f"[1/2] 使用 {WHISPER_MODEL} 轉文字中 ...")
    kwargs: dict[str, Any] = {"model": WHISPER_MODEL, "temperature": 0}
    if language:
        kwargs["language"] = language
    if context and context.strip():
        # Whisper 的 prompt 參數可以提示專有名詞/產業用語，改善辨識時的用詞準確度
        kwargs["prompt"] = context.strip()[:800]
    with audio_path.open("rb") as f:
        resp = client.audio.transcriptions.create(file=(audio_path.name, f), **kwargs)
    text = resp.text
    if not text.strip():
        sys.exit("錯誤：轉文字結果為空")
    return text


PARTIAL_PROMPT = """你是專業的會議紀錄助理。以下是一段會議逐字稿的「部分內容」（第 {idx}/{total} 段），
請用繁體中文條列整理這一段出現的重點：討論內容、已達成的結論、待辦事項（含負責人與期限，若有提到）、需要向與會者確認的模糊之處。
只整理這一段實際出現的內容，不要編造，也不要下總結標題。
{context_block}
逐字稿片段：
---
{chunk}
---"""

REDUCE_PROMPT = """你是專業的會議紀錄助理。以下是同一場會議逐字稿依序分段整理出的重點筆記，
請將它們合併、去除重複，用繁體中文條列整理成更精簡的重點筆記（討論內容、結論、待辦事項、待確認事項），
不要下總結標題，只需要條列內容本身。
{context_block}
分段筆記：
---
{notes}
---"""

MERGE_PROMPT = """你是專業的文件整理助理。以下是同一份逐字稿依序分段整理出的重點筆記，請將它們合併、去除重複，
整理成一份完整的文件，嚴格使用以下格式（Markdown）：

{format_spec}
{context_block}
分段筆記：
---
{notes}
---"""


def build_context_block(context: str | None) -> str:
    if not context or not context.strip():
        return ""
    return (
        "\n背景資訊（產業／場景／專有名詞提示，語音辨識可能把這些詞誤植成同音的一般用字，"
        "請優先依此處提示修正用詞，但不要因此捏造逐字稿沒提到的內容）：\n"
        f"{context.strip()}\n"
    )


def split_transcript(transcript: str, limit: int = CHUNK_CHAR_LIMIT) -> list[str]:
    sentences = re.split(r"(?<=[。！？\n])", transcript)
    pieces: list[str] = []
    for sentence in sentences:
        # 語音辨識逐字稿可能標點稀疏，單一「句子」也可能超過 limit，
        # 這種情況直接強制按字數硬切，避免整段仍然過大。
        if len(sentence) > limit:
            for i in range(0, len(sentence), limit):
                pieces.append(sentence[i : i + limit])
        elif sentence:
            pieces.append(sentence)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > limit:
            chunks.append(current)
            current = piece
        else:
            current += piece
    if current.strip():
        chunks.append(current)
    return chunks or [transcript]


def call_llm(
    client: OpenAI,
    system: str,
    user: str,
    max_tokens: int = 1024,
    retries: int = 3,
    reasoning_effort: str | None = None,
) -> str:
    # gpt-oss 是推理模型，推理過程也會吃 max_tokens；需要較長結構化輸出時可調低推理強度
    extra: dict[str, Any] = {}
    if reasoning_effort:
        extra["reasoning_effort"] = reasoning_effort
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                **extra,
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content or ""
        except RateLimitError:
            if attempt == retries - 1:
                raise
            print("  觸發速率限制，等待 20 秒後重試 ...")
            time.sleep(20)
    return ""


def summarize(
    client: OpenAI,
    transcript: str,
    context: str | None = None,
    format_spec: str | None = None,
    chunks: list[str] | None = None,
) -> str:
    system = "你是一位擅長整理文件的助理，輸出簡潔、條理分明的繁體中文。"
    context_block = build_context_block(context)
    format_spec = format_spec or FORMAT_PRESETS[DEFAULT_TEMPLATE]
    # 呼叫端可自行預先切好段落（例如合併分段報告時，讓每段都帶著時間標記）
    chunks = chunks or split_transcript(transcript)

    if len(chunks) == 1:
        print(f"[2/2] 使用 {LLM_MODEL} 整理文件中 ...")
        return call_llm(
            client,
            system,
            MINUTES_PROMPT.format(
                transcript=transcript,
                context_block=context_block,
                format_spec=format_spec,
            ),
            max_tokens=2048,
        )

    print(
        f"[2/2] 逐字稿較長，切成 {len(chunks)} 段分批整理（避免超過 Groq 每分鐘 token 限制）..."
    )
    notes: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        print(f"  整理第 {i}/{len(chunks)} 段 ...")
        note = call_llm(
            client,
            system,
            PARTIAL_PROMPT.format(
                idx=i, total=len(chunks), chunk=chunk, context_block=context_block
            ),
            max_tokens=600,
        )
        notes.append(f"【第 {i} 段】\n{note}")
        time.sleep(8)

    # 分段筆記若數量多，接在一起可能仍超過安全長度，先分批壓縮（reduce）
    while sum(len(n) for n in notes) > CHUNK_CHAR_LIMIT:
        print(f"  分段筆記仍偏長（共 {len(notes)} 段），再次壓縮合併 ...")
        batches = split_transcript("\n\n".join(notes), limit=CHUNK_CHAR_LIMIT)
        reduced: list[str] = []
        for batch in batches:
            reduced.append(
                call_llm(
                    client,
                    system,
                    REDUCE_PROMPT.format(notes=batch, context_block=context_block),
                    max_tokens=800,
                )
            )
            time.sleep(8)
        notes = reduced

    print("  合併分段筆記為最終文件 ...")
    return call_llm(
        client,
        system,
        MERGE_PROMPT.format(
            notes="\n\n".join(notes), context_block=context_block, format_spec=format_spec
        ),
        max_tokens=2048,
    )


def run_segments_cli(
    args: argparse.Namespace, format_spec: str, audio_path: Path | None, stem: str
) -> None:
    from segments import (
        SegmentWorkspace,
        format_suggestions,
        merge_report,
        process_segment,
        run_all_segments,
        suggest_terms,
    )

    if args.segments_dir:
        ws = SegmentWorkspace.load(Path(args.segments_dir))
        if args.context is not None:
            ws.data["context"] = args.context
        if args.language:
            ws.data["language"] = args.language
        ws.save()
    else:
        assert audio_path is not None
        ws = SegmentWorkspace.open_or_create(
            audio_path,
            Path("output") / f"{stem}_segments",
            stem,
            args.segment_minutes,
            args.language,
            args.context,
        )
    print(f"分段專案：{ws.root}（共 {ws.total} 段）")
    client = get_client()

    if args.redo:
        if args.segment_context is not None:
            ws.set_segment_context(args.redo, args.segment_context)
        paths = process_segment(
            client, ws, args.redo, format_spec, retranscribe=not args.keep_transcript
        )
        print(f"  第 {args.redo} 段已更新：{paths['md']}")
    if args.suggest:
        print(format_suggestions(suggest_terms(client, ws, args.suggest)))
    if not args.redo and not args.suggest:
        run_all_segments(client, ws, format_spec)
        print("\n各段結果：")
        for seg in ws.segments:
            print(f"  第 {seg['index']} 段 {ws.label(seg)}：{ws.minutes_path(seg).name}")
    if args.merge:
        paths = merge_report(client, ws, format_spec)
        print("\n完整報告：")
        for path in paths.values():
            print(f"  {path}")


def main() -> None:
    out: Any = sys.stdout
    err: Any = sys.stderr
    out.reconfigure(encoding="utf-8")
    err.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="錄音轉文字 + 會議紀錄整理（Groq 免費模型）"
    )
    parser.add_argument(
        "audio", nargs="?", help="音檔路徑（m4a/mp3/wav/ogg/webm 等），與 --youtube 擇一"
    )
    parser.add_argument("--youtube", help="YouTube 影片網址，與音檔路徑擇一")
    parser.add_argument("--language", help="指定語言代碼，例如 zh（預設自動偵測）")
    parser.add_argument(
        "--context",
        help="補充提示詞，例如產業別、專案代號、常見專有名詞，用來提升辨識與整理的用詞準確度",
    )
    parser.add_argument(
        "--template",
        choices=list(FORMAT_PRESETS.keys()),
        default=DEFAULT_TEMPLATE,
        help=f"輸出文件類型（預設：{DEFAULT_TEMPLATE}）",
    )
    parser.add_argument(
        "--custom-template",
        help="手動指定輸出的 Markdown 結構規格，若有指定則覆蓋 --template",
    )
    parser.add_argument(
        "--reuse-transcript",
        action="store_true",
        help="若 output 已有同名逐字稿則跳過轉文字步驟",
    )
    seg_group = parser.add_argument_group("分段處理（長錄音依時間切段，逐段調整後再合併）")
    seg_group.add_argument(
        "--segment-minutes",
        type=float,
        help="每段幾分鐘，例如 10：65 分鐘錄音會切成 7 段，各自輸出逐字稿與整理文件",
    )
    seg_group.add_argument(
        "--segments-dir", help="開啟既有的分段專案資料夾（<檔名>_segments），不需再給音檔"
    )
    seg_group.add_argument("--redo", type=int, metavar="N", help="重新處理第 N 段")
    seg_group.add_argument(
        "--segment-context",
        help="搭配 --redo：設定第 N 段專用的補充提示詞（會與 --context 合併使用並記錄下來）",
    )
    seg_group.add_argument(
        "--keep-transcript",
        action="store_true",
        help="搭配 --redo：不重新辨識，沿用（已手動修正的）逐字稿只重新整理",
    )
    seg_group.add_argument(
        "--suggest", type=int, metavar="N", help="讓 AI 分析第 N 段逐字稿，建議要補充的提示詞"
    )
    seg_group.add_argument(
        "--merge", action="store_true", help="用各段逐字稿重新整理出一份完整報告"
    )
    args = parser.parse_args()
    format_spec = args.custom_template or FORMAT_PRESETS[args.template]

    out_dir = Path("output")

    if args.segments_dir:
        run_segments_cli(args, format_spec, None, "")
        return

    if not args.youtube and not args.audio:
        sys.exit("錯誤：請提供音檔路徑，或使用 --youtube 指定影片網址")

    out_dir.mkdir(exist_ok=True)

    if args.youtube:
        print("[0/2] 從 YouTube 下載音訊中 ...")
        audio_path, title = download_youtube_audio(args.youtube, out_dir / "youtube_downloads")
        stem = sanitize_filename(title)
        print(f"  已下載：{audio_path}（{title}）")
    else:
        audio_path = Path(args.audio)
        if not audio_path.exists():
            sys.exit(f"錯誤：找不到檔案 {audio_path}")
        stem = audio_path.stem

    if args.segment_minutes:
        run_segments_cli(args, format_spec, audio_path, stem)
        return

    client = get_client()
    transcript_path = out_dir / f"{stem}_transcript.txt"

    if args.reuse_transcript and transcript_path.exists():
        transcript = transcript_path.read_text(encoding="utf-8")
        print("[1/2] 重用既有逐字稿：" + str(transcript_path))
    else:
        audio_for_api = prepare_audio(audio_path)
        transcript = transcribe(client, audio_for_api, args.language, args.context)
        transcript_path.write_text(transcript, encoding="utf-8")
        print(f"  已儲存逐字稿：{transcript_path}")

    minutes = summarize(client, transcript, args.context, format_spec)

    minutes_path = out_dir / f"{stem}_minutes.md"
    minutes_path.write_text(minutes, encoding="utf-8")
    print(f"  已儲存會議紀錄：{minutes_path}")

    docx_path = out_dir / f"{stem}_minutes.docx"
    export_docx(minutes_path, docx_path)
    print(f"  已匯出 Word：{docx_path}")

    html_path = out_dir / f"{stem}_minutes.html"
    export_html(minutes_path, html_path)
    print(f"  已匯出 HTML：{html_path}")

    print("\n========== 會議紀錄 ==========\n")
    print(minutes)


if __name__ == "__main__":
    main()
