import os
import sys
import threading
import traceback
from pathlib import Path

from dotenv import load_dotenv

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).parent

ENV_PATH = APP_DIR / ".env"
load_dotenv(ENV_PATH)

from openai import AuthenticationError

from main import (
    GROQ_BASE_URL,
    MAX_BYTES,
    WHISPER_MODEL,
    get_client,
    needs_conversion,
    prepare_audio,
    summarize,
)
from export_word import export_docx


def save_api_key(key: str) -> None:
    ENV_PATH.write_text(f"GROQ_API_KEY={key}\n", encoding="utf-8")
    os.environ["GROQ_API_KEY"] = key


def has_valid_key_format() -> bool:
    key = os.getenv("GROQ_API_KEY", "")
    return bool(key) and "貼上" not in key


class PipelineTask:
    def __init__(
        self, audio_path: Path, language: str | None, on_status, on_done, on_error
    ):
        self.audio_path = audio_path
        self.language = language
        self.on_status = on_status
        self.on_done = on_done
        self.on_error = on_error

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            result = self._pipeline()
            self.on_done(result)
        except Exception as e:
            traceback.print_exc()
            self.on_error(e)

    def _pipeline(self):
        audio_path = self.audio_path
        out_dir = audio_path.parent / "meeting2text_output"
        out_dir.mkdir(exist_ok=True)
        stem = audio_path.stem
        transcript_path = out_dir / f"{stem}_transcript.txt"

        client = get_client()

        if transcript_path.exists():
            self.on_status("發現既有逐字稿，直接重用")
            transcript = transcript_path.read_text(encoding="utf-8")
        else:
            if needs_conversion(audio_path):
                self.on_status("音檔格式不支援或超過 25MB，轉檔中（約需 1~2 分鐘）...")
            audio_for_api = prepare_audio(audio_path, out_dir)
            self.on_status(
                f"使用 {WHISPER_MODEL} 轉文字中（長錄音可能需要數分鐘，請耐心等候）..."
            )
            with audio_for_api.open("rb") as f:
                if self.language:
                    resp = client.audio.transcriptions.create(
                        model=WHISPER_MODEL,
                        file=(audio_for_api.name, f),
                        temperature=0,
                        language=self.language,
                    )
                else:
                    resp = client.audio.transcriptions.create(
                        model=WHISPER_MODEL,
                        file=(audio_for_api.name, f),
                        temperature=0,
                    )
            transcript = resp.text
            transcript_path.write_text(transcript, encoding="utf-8")

        self.on_status("AI 整理會議紀錄中...")
        minutes = summarize(client, transcript)

        minutes_path = out_dir / f"{stem}_minutes.md"
        minutes_path.write_text(minutes, encoding="utf-8")
        self.on_status("匯出 Word 中...")
        docx_path = export_docx(minutes_path, out_dir / f"{stem}_minutes.docx")

        return {
            "out_dir": out_dir,
            "transcript": transcript_path,
            "minutes_md": minutes_path,
            "minutes_docx": docx_path,
        }


def main() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
    from openai import OpenAI

    root = tk.Tk()
    root.title("會議錄音轉文字工具")
    root.geometry("640x360")
    root.resizable(False, False)

    state: dict[str, Path | None] = {"audio": None}

    # ---- 首次啟動：要求輸入 API Key ----
    while not has_valid_key_format():
        key = simpledialog.askstring(
            "首次設定",
            "請貼上你的 Groq API Key\n（可於 console.groq.com 免費申請）",
            parent=root,
            show="*",
        )
        if not key:
            sys.exit(0)
        try:
            probe = OpenAI(api_key=key.strip(), base_url=GROQ_BASE_URL)
            probe.models.list()
        except AuthenticationError:
            messagebox.showerror(
                "API Key 錯誤", "這組 API Key 無效，請重新輸入。", parent=root
            )
            continue
        except Exception as e:
            messagebox.showerror("連線失敗", f"無法驗證 API Key：\n{e}", parent=root)
            continue
        save_api_key(key.strip())

    # ---- 版面 ----
    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)

    ttk.Label(
        frm, text="會議錄音轉文字 + 自動整理會議紀錄", font=("微軟正黑體", 14, "bold")
    ).pack(anchor="w", pady=(0, 12))

    row1 = ttk.Frame(frm)
    row1.pack(fill="x", pady=4)
    lbl_file = ttk.Label(row1, text="尚未選擇音檔", foreground="#666")
    btn_pick = ttk.Button(row1, text="選擇音檔")

    def pick_file() -> None:
        path = filedialog.askopenfilename(
            title="選擇會議錄音",
            filetypes=[
                (
                    "音訊檔案",
                    "*.m4a *.mp3 *.wav *.ogg *.opus *.webm *.flac *.aac *.wma *.amr *.3gp *.mp4",
                ),
                ("所有檔案", "*.*"),
            ],
        )
        if path:
            state["audio"] = Path(path)
            lbl_file.config(text=path, foreground="#000")

    btn_pick.config(command=pick_file)

    row2 = ttk.Frame(frm)
    row2.pack(fill="x", pady=4)
    ttk.Label(row2, text="語言：").pack(side="left")
    lang_var = tk.StringVar(value="自動偵測")
    lang_box = ttk.Combobox(
        row2,
        textvariable=lang_var,
        state="readonly",
        width=12,
        values=["自動偵測", "中文 (zh)", "英文 (en)"],
    )

    progress = ttk.Progressbar(frm, mode="indeterminate")
    lbl_status = ttk.Label(frm, text="就緒", foreground="#333", wraplength=580)
    btn_open = ttk.Button(frm, text="開啟輸出資料夾", state="disabled")
    btn_start = ttk.Button(frm, text="開始轉換")

    row1.pack_configure(pady=8)
    lbl_file.pack(side="left", fill="x", expand=True, padx=(0, 8))
    btn_pick.pack(side="right")

    lang_box.pack(side="left")
    row2.pack(pady=4)

    progress.pack(fill="x", pady=(18, 6))
    lbl_status.pack(anchor="w")
    btn_start.pack(pady=(14))
    btn_open.pack()

    # ---- API Key 狀態列 ----
    row_key = ttk.Frame(frm)
    row_key.pack(fill="x", pady=(16, 0))
    lbl_key = ttk.Label(row_key, foreground="#666")
    btn_reset_key = ttk.Button(row_key, text="重新設定 API Key")

    def masked_key() -> str:
        key = os.getenv("GROQ_API_KEY", "")
        if len(key) > 10:
            return f"{key[:6]}...{key[-4:]}"
        return key

    def refresh_key_label() -> None:
        lbl_key.config(text=f"API Key：{masked_key()}（儲存於 {ENV_PATH.name}）")

    def reset_key() -> None:
        key = simpledialog.askstring(
            "重新設定 API Key",
            "請貼上新的 Groq API Key",
            parent=root,
            show="*",
        )
        if not key:
            return
        try:
            probe = OpenAI(api_key=key.strip(), base_url=GROQ_BASE_URL)
            probe.models.list()
        except AuthenticationError:
            messagebox.showerror(
                "API Key 錯誤", "這組 API Key 無效，請重新輸入。", parent=root
            )
            return
        except Exception as e:
            messagebox.showerror("連線失敗", f"無法驗證 API Key：\n{e}", parent=root)
            return
        save_api_key(key.strip())
        refresh_key_label()
        messagebox.showinfo("完成", "API Key 已更新。", parent=root)

    btn_reset_key.config(command=reset_key)
    lbl_key.pack(side="left")
    btn_reset_key.pack(side="right")
    refresh_key_label()

    def set_busy(busy: bool) -> None:
        if busy:
            progress.start(12)
        else:
            progress.stop()
        btn_start.config(state="disabled" if busy else "normal")
        btn_pick.config(state="disabled" if busy else "normal")

    def on_done(result: dict) -> None:
        def ui():
            set_busy(False)
            lbl_status.config(text="完成！", foreground="#0a7d32")
            btn_open.config(
                state="normal", command=lambda: os.startfile(str(result["out_dir"]))
            )
            messagebox.showinfo(
                "完成",
                "會議紀錄已產生：\n\n"
                f"{result['minutes_docx'].name}\n\n"
                f"輸出資料夾：\n{result['out_dir']}",
            )

        root.after(0, ui)

    def on_error(e: Exception) -> None:
        def ui():
            set_busy(False)
            msg = str(e)
            if isinstance(e, AuthenticationError):
                msg = "API Key 無效或已過期，請刪除程式旁的 .env 後重新設定。"
            lbl_status.config(text="失敗", foreground="#b3261e")
            messagebox.showerror("發生錯誤", msg[:1500])

        root.after(0, ui)

    def start() -> None:
        audio = state["audio"]
        if audio is None:
            messagebox.showwarning("提醒", "請先選擇音檔")
            return
        lang_map = {"自動偵測": None, "中文 (zh)": "zh", "英文 (en)": "en"}
        task = PipelineTask(
            audio,
            lang_map.get(lang_var.get()),
            on_status=lambda s: root.after(0, lambda: lbl_status.config(text=s)),
            on_done=on_done,
            on_error=on_error,
        )
        set_busy(True)
        lbl_status.config(text="處理中...", foreground="#333")
        task.start()

    btn_start.config(command=start)

    def on_close() -> None:
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
