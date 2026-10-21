"""長錄音依時間分段處理：切音檔 → 每段轉文字與整理 → 逐段補提示詞重跑 → 合併成完整報告。

分段專案的所有檔案集中放在一個資料夾（<輸出資料夾>/<檔名>_segments/），
進度與每段的提示詞記錄在 segments.json，之後可以直接重新開啟這個資料夾繼續調整。
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI, RateLimitError

from export_html import export_html
from export_word import export_docx
from main import (
    CHUNK_CHAR_LIMIT,
    WHISPER_MODEL,
    call_llm,
    resolve_ffmpeg,
    split_transcript,
    summarize,
)

MANIFEST_NAME = "segments.json"
# 每段往後多切幾秒，避免剛好切在一句話中間導致邊界的字被吃掉
OVERLAP_SECONDS = 5
# 最後一段若短於這個秒數就併入前一段，避免產生幾乎沒內容的段落
MIN_TAIL_SECONDS = 15
WHISPER_PROMPT_LIMIT = 800
EMPTY_TRANSCRIPT = "（此段沒有可辨識的語音）"

StatusFn = Callable[[str], None]


def _print_status(msg: str) -> None:
    print(msg)


def fmt_time(seconds: float) -> str:
    total = int(round(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def probe_duration(audio_path: Path) -> float:
    """用 ffmpeg 讀取音檔長度（秒）。打包版只內建 ffmpeg 沒有 ffprobe，所以解析 ffmpeg -i 的輸出。"""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.run(
        [resolve_ffmpeg(), "-hide_banner", "-i", str(audio_path)],
        capture_output=True,
        check=False,
        creationflags=flags,
    )
    err = proc.stderr.decode("utf-8", "ignore")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not m:
        raise RuntimeError(f"無法讀取音檔長度：{audio_path}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def plan_segments(duration: float, minutes: float) -> list[tuple[float, float]]:
    step = minutes * 60
    bounds: list[list[float]] = []
    start = 0.0
    while start < duration - 0.5:
        end = min(start + step, duration)
        bounds.append([start, end])
        start = end
    if len(bounds) > 1 and bounds[-1][1] - bounds[-1][0] < MIN_TAIL_SECONDS:
        bounds[-2][1] = bounds[-1][1]
        bounds.pop()
    return [(s, e) for s, e in bounds]


def split_terms(text: str) -> list[str]:
    return [t.strip() for t in re.split(r"[、,，;；\n]+", text or "") if t.strip()]


def join_terms(*texts: str, limit: int = WHISPER_PROMPT_LIMIT) -> str:
    """合併多組提示詞並去除重複，超過長度上限的部分捨棄（Whisper prompt 有長度限制）。"""
    seen: list[str] = []
    for text in texts:
        for term in split_terms(text):
            if term not in seen:
                seen.append(term)
    out = ""
    for term in seen:
        candidate = f"{out}、{term}" if out else term
        if len(candidate) > limit:
            break
        out = candidate
    return out


class SegmentWorkspace:
    def __init__(self, root: Path, data: dict[str, Any]):
        self.root = root
        self.data = data

    # ---- 建立 / 載入 ----
    @classmethod
    def create(
        cls,
        source: Path,
        root: Path,
        stem: str,
        minutes: float,
        language: str | None,
        context: str | None,
    ) -> "SegmentWorkspace":
        root.mkdir(parents=True, exist_ok=True)
        duration = probe_duration(source)
        segments = [
            {"index": i, "start": s, "end": e, "context": ""}
            for i, (s, e) in enumerate(plan_segments(duration, minutes), start=1)
        ]
        ws = cls(
            root,
            {
                "source": str(source.resolve()),
                "stem": stem,
                "duration": duration,
                "segment_minutes": minutes,
                "language": language,
                "context": context or "",
                "segments": segments,
            },
        )
        ws.save()
        return ws

    @classmethod
    def load(cls, path: Path) -> "SegmentWorkspace":
        manifest = path if path.is_file() else path / MANIFEST_NAME
        if not manifest.exists():
            raise RuntimeError(f"找不到分段專案設定檔：{manifest}")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return cls(manifest.parent, data)

    @classmethod
    def open_or_create(
        cls,
        source: Path,
        root: Path,
        stem: str,
        minutes: float,
        language: str | None,
        context: str | None,
    ) -> "SegmentWorkspace":
        """同一個音檔、同樣分段長度的專案已存在就沿用（保留已轉好的段落與每段提示詞）。"""
        if (root / MANIFEST_NAME).exists():
            ws = cls.load(root)
            if ws.data.get("segment_minutes") == minutes:
                ws.data["language"] = language
                ws.data["context"] = context or ""
                ws.save()
                return ws
            raise RuntimeError(
                f"資料夾 {root} 已有分段長度 {ws.data.get('segment_minutes')} 分鐘的分段專案，"
                "如要改用不同分段長度，請先刪除或改名該資料夾。"
            )
        return cls.create(source, root, stem, minutes, language, context)

    def save(self) -> None:
        (self.root / MANIFEST_NAME).write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---- 屬性 / 路徑 ----
    @property
    def stem(self) -> str:
        return self.data["stem"]

    @property
    def segments(self) -> list[dict[str, Any]]:
        return self.data["segments"]

    @property
    def total(self) -> int:
        return len(self.segments)

    def seg(self, index: int) -> dict[str, Any]:
        if not 1 <= index <= self.total:
            raise RuntimeError(f"段落編號需介於 1~{self.total}，收到 {index}")
        return self.segments[index - 1]

    def _name(self, seg: dict[str, Any]) -> str:
        return f"part{seg['index']:02d}"

    def audio_path(self, seg: dict[str, Any]) -> Path:
        return self.root / f"{self._name(seg)}.opus"

    def transcript_path(self, seg: dict[str, Any]) -> Path:
        return self.root / f"{self._name(seg)}_transcript.txt"

    def minutes_path(self, seg: dict[str, Any]) -> Path:
        return self.root / f"{self._name(seg)}_minutes.md"

    def report_path(self) -> Path:
        return self.root / f"{self.stem}_full_report.md"

    def label(self, seg: dict[str, Any]) -> str:
        return f"{fmt_time(seg['start'])}–{fmt_time(seg['end'])}"

    def effective_context(self, seg: dict[str, Any]) -> str:
        return join_terms(self.data.get("context", ""), seg.get("context", ""))

    def set_segment_context(self, index: int, context: str) -> None:
        self.seg(index)["context"] = context.strip()
        self.save()


def split_audio(ws: SegmentWorkspace, status: StatusFn = _print_status) -> None:
    source = Path(ws.data["source"])
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    for seg in ws.segments:
        out = ws.audio_path(seg)
        if out.exists():
            continue
        if not source.exists():
            raise RuntimeError(f"找不到原始音檔，無法切段：{source}")
        status(f"切割音檔 第 {seg['index']}/{ws.total} 段（{ws.label(seg)}）...")
        length = seg["end"] - seg["start"]
        if seg["index"] < ws.total:
            length += OVERLAP_SECONDS
        proc = subprocess.run(
            [
                resolve_ffmpeg(),
                "-y",
                "-ss",
                f"{seg['start']:.2f}",
                "-t",
                f"{length:.2f}",
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "libopus",
                "-b:a",
                "16k",
                str(out),
            ],
            capture_output=True,
            check=False,
            creationflags=flags,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "ignore")
            raise RuntimeError(f"音檔切段失敗（ffmpeg 錯誤）:\n{err[-800:]}")


def transcribe_segment(
    client: OpenAI, ws: SegmentWorkspace, index: int, status: StatusFn = _print_status
) -> str:
    seg = ws.seg(index)
    audio = ws.audio_path(seg)
    if not audio.exists():
        split_audio(ws, status)
    status(f"使用 {WHISPER_MODEL} 轉文字 第 {index}/{ws.total} 段（{ws.label(seg)}）...")
    kwargs: dict[str, Any] = {"model": WHISPER_MODEL, "temperature": 0}
    if ws.data.get("language"):
        kwargs["language"] = ws.data["language"]
    prompt = ws.effective_context(seg)
    if prompt:
        kwargs["prompt"] = prompt
    for attempt in range(3):
        try:
            with audio.open("rb") as f:
                resp = client.audio.transcriptions.create(file=(audio.name, f), **kwargs)
            break
        except RateLimitError:
            if attempt == 2:
                raise
            status("  觸發語音辨識速率限制，等待 30 秒後重試 ...")
            time.sleep(30)
    text = resp.text.strip() or EMPTY_TRANSCRIPT
    ws.transcript_path(seg).write_text(text, encoding="utf-8")
    seg["transcribed_with"] = prompt
    ws.save()
    return text


def _export_all(md_path: Path) -> dict[str, Path]:
    return {
        "md": md_path,
        "docx": export_docx(md_path, md_path.with_suffix(".docx")),
        "html": export_html(md_path, md_path.with_suffix(".html")),
    }


def summarize_segment(
    client: OpenAI,
    ws: SegmentWorkspace,
    index: int,
    format_spec: str,
    status: StatusFn = _print_status,
) -> dict[str, Path]:
    seg = ws.seg(index)
    transcript = ws.transcript_path(seg).read_text(encoding="utf-8")
    status(f"AI 整理 第 {index}/{ws.total} 段（{ws.label(seg)}）...")
    if transcript.strip() == EMPTY_TRANSCRIPT:
        minutes = f"# 第 {index} 段\n\n{EMPTY_TRANSCRIPT}"
    else:
        minutes = summarize(client, transcript, ws.effective_context(seg), format_spec)
    # 在第一個標題下方標註時段，方便對照原音
    note = f"**時段：{ws.label(seg)}（第 {index}／{ws.total} 段）**"
    lines = minutes.splitlines()
    if lines and lines[0].startswith("# "):
        minutes = "\n".join([lines[0], "", note, *lines[1:]])
    else:
        minutes = f"{note}\n\n{minutes}"
    md_path = ws.minutes_path(seg)
    md_path.write_text(minutes, encoding="utf-8")
    return _export_all(md_path)


def process_segment(
    client: OpenAI,
    ws: SegmentWorkspace,
    index: int,
    format_spec: str,
    retranscribe: bool = True,
    status: StatusFn = _print_status,
) -> dict[str, Path]:
    """（重新）處理單一段落：轉文字 + 整理。retranscribe=False 時沿用（可能已手動修正的）逐字稿。"""
    seg = ws.seg(index)
    if retranscribe or not ws.transcript_path(seg).exists():
        transcribe_segment(client, ws, index, status)
    return summarize_segment(client, ws, index, format_spec, status)


def run_all_segments(
    client: OpenAI,
    ws: SegmentWorkspace,
    format_spec: str,
    status: StatusFn = _print_status,
) -> None:
    """切段並處理所有段落；已轉好逐字稿的段落會沿用，不重新辨識。"""
    split_audio(ws, status)
    for seg in ws.segments:
        idx = seg["index"]
        if not ws.transcript_path(seg).exists():
            transcribe_segment(client, ws, idx, status)
        if not ws.minutes_path(seg).exists():
            summarize_segment(client, ws, idx, format_spec, status)


SUGGEST_PROMPT = """你是語音辨識校對專家。以下是一段由 AI 語音辨識產生的逐字稿片段，可能有同音字誤植，
特別是產業術語、專有名詞、產品型號、人名、英文縮寫常被辨識成發音相近的一般生活用字。

請找出：
1. suspects：疑似辨識錯誤的詞（逐字稿中的原文）與你推測的正確用詞，並簡短說明理由。
2. terms：建議加入「補充提示詞」的關鍵詞彙（正確寫法的專有名詞／術語），讓下次語音辨識能直接辨識正確。
3. industry：推測這段內容所屬的產業或場景（一句話）。

只列出有相當把握的項目，不要捏造逐字稿中沒有跡象的詞。
{context_block}
請只輸出 JSON，格式如下，不要有其他文字：
{{"industry": "...", "suspects": [{{"heard": "原文", "likely": "推測正確用詞", "reason": "理由"}}], "terms": ["詞1", "詞2"]}}

逐字稿片段：
---
{chunk}
---"""


def _parse_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def suggest_terms(
    client: OpenAI, ws: SegmentWorkspace, index: int, status: StatusFn = _print_status
) -> dict[str, Any]:
    """讓 AI 讀該段逐字稿，找出疑似誤植的詞並建議要補充的提示詞。"""
    seg = ws.seg(index)
    path = ws.transcript_path(seg)
    if not path.exists():
        raise RuntimeError(f"第 {index} 段尚未轉文字，請先處理該段")
    transcript = path.read_text(encoding="utf-8")
    current = ws.effective_context(seg)
    context_block = f"\n目前已提供的提示詞（不用重複建議）：{current}\n" if current else ""
    system = "你是嚴謹的語音辨識校對助理，只輸出 JSON。"
    chunks = split_transcript(transcript, limit=CHUNK_CHAR_LIMIT)

    industries: list[str] = []
    suspects: list[dict[str, str]] = []
    terms: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        status(f"AI 分析第 {index} 段用詞（{i}/{len(chunks)}）...")
        raw = call_llm(
            client,
            system,
            SUGGEST_PROMPT.format(chunk=chunk, context_block=context_block),
            max_tokens=3000,
            reasoning_effort="low",
        )
        data = _parse_json(raw)
        if data.get("industry"):
            industries.append(str(data["industry"]))
        for s in data.get("suspects") or []:
            if isinstance(s, dict) and s.get("heard") and s.get("likely"):
                if all(s["heard"] != x["heard"] for x in suspects):
                    suspects.append(
                        {
                            "heard": str(s["heard"]),
                            "likely": str(s["likely"]),
                            "reason": str(s.get("reason", "")),
                        }
                    )
        for t in data.get("terms") or []:
            t = str(t).strip()
            if t and t not in terms and t not in split_terms(current):
                terms.append(t)
        if i < len(chunks):
            time.sleep(8)

    # 疑似誤植的「正確用詞」本身也是很好的提示詞
    for s in suspects:
        if s["likely"] not in terms and s["likely"] not in split_terms(current):
            terms.append(s["likely"])
    return {
        "industry": industries[0] if industries else "",
        "suspects": suspects,
        "terms": terms,
    }


def format_suggestions(result: dict[str, Any]) -> str:
    lines: list[str] = []
    if result.get("industry"):
        lines.append(f"推測產業／場景：{result['industry']}")
        lines.append("")
    if result.get("suspects"):
        lines.append("疑似辨識錯誤：")
        for s in result["suspects"]:
            reason = f"（{s['reason']}）" if s.get("reason") else ""
            lines.append(f"  「{s['heard']}」→「{s['likely']}」{reason}")
        lines.append("")
    if result.get("terms"):
        lines.append("建議補充的提示詞：" + "、".join(result["terms"]))
    return "\n".join(lines) or "AI 沒有找到明顯需要補充的詞彙。"


MERGE_HINT = (
    "逐字稿中的【時段 mm:ss–mm:ss】是時間標記；相鄰段落交界處有幾秒重疊，內容重複請合併。"
    "整理條列重點時，請在句末用括號標註出處時段，例如（10:00–20:00）。"
    "逐字稿沒有明確提到的負責人或期限請寫「未提及」，不要自行推測日期。"
)


def merge_report(
    client: OpenAI,
    ws: SegmentWorkspace,
    format_spec: str,
    status: StatusFn = _print_status,
) -> dict[str, Path]:
    """用各段（修正過的）逐字稿依時間順序重新整理成一份完整報告。"""
    missing = [s["index"] for s in ws.segments if not ws.transcript_path(s).exists()]
    if missing:
        raise RuntimeError(f"以下段落尚未轉文字：{missing}，請先處理後再合併")
    parts: list[str] = []
    chunks: list[str] = []
    for seg in ws.segments:
        text = ws.transcript_path(seg).read_text(encoding="utf-8").strip()
        if text == EMPTY_TRANSCRIPT:
            continue
        marker = f"【時段 {ws.label(seg)}】"
        parts.append(f"{marker}\n{text}")
        # 每個送給 AI 的片段開頭都帶時間標記，分段筆記才能標註出處時段
        for piece in split_transcript(text, limit=CHUNK_CHAR_LIMIT - len(marker) - 1):
            chunks.append(f"{marker}\n{piece}")
    combined = "\n\n".join(parts)
    (ws.root / f"{ws.stem}_full_transcript.txt").write_text(combined, encoding="utf-8")

    terms = join_terms(ws.data.get("context", ""), *(s.get("context", "") for s in ws.segments))
    context = f"{terms}\n{MERGE_HINT}" if terms else MERGE_HINT
    status(f"合併 {ws.total} 段逐字稿，AI 整理完整報告中（長錄音需要數分鐘）...")
    report = summarize(client, combined, context, format_spec, chunks=chunks)
    md_path = ws.report_path()
    md_path.write_text(report, encoding="utf-8")
    status("匯出完整報告 Word / HTML 中...")
    return _export_all(md_path)
