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
    DEFAULT_TEMPLATE,
    FORMAT_PRESETS,
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
        self,
        audio_path: Path,
        language: str | None,
        context: str | None,
        format_spec: str,
        on_status,
        on_done,
        on_error,
    ):
        self.audio_path = audio_path
        self.language = language
        self.context = context
        self.format_spec = format_spec
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
            kwargs: dict = {"model": WHISPER_MODEL, "temperature": 0}
            if self.language:
                kwargs["language"] = self.language
            if self.context and self.context.strip():
                kwargs["prompt"] = self.context.strip()[:800]
            with audio_for_api.open("rb") as f:
                resp = client.audio.transcriptions.create(
                    file=(audio_for_api.name, f), **kwargs
                )
            transcript = resp.text
            transcript_path.write_text(transcript, encoding="utf-8")

        self.on_status("AI 整理文件中...")
        minutes = summarize(client, transcript, self.context, self.format_spec)

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

    BG = "#f4f5f9"
    CARD = "#ffffff"
    BORDER = "#e2e4ea"
    TEXT = "#1f2430"
    MUTED = "#8a8fa3"
    ACCENT = "#4f46e5"
    ACCENT_ACTIVE = "#4338ca"
    ACCENT_DISABLED = "#c7c9e8"
    SUCCESS = "#0a7d32"
    ERROR = "#c62828"

    root = tk.Tk()
    root.title("會議錄音轉文字工具")
    root.geometry("640x600")
    root.minsize(640, 480)
    root.configure(bg=BG)

    icon_base = Path(getattr(sys, "_MEIPASS", APP_DIR))
    icon_path = icon_base / "icon.ico"
    if icon_path.exists():
        try:
            root.iconbitmap(str(icon_path))
        except tk.TclError:
            pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT, font=("微軟正黑體", 10))
    style.configure("Card.TLabel", background=CARD, foreground=TEXT, font=("微軟正黑體", 10))
    style.configure(
        "Title.TLabel", background=BG, foreground=TEXT, font=("微軟正黑體", 15, "bold")
    )
    style.configure(
        "Subtitle.TLabel", background=BG, foreground=MUTED, font=("微軟正黑體", 9)
    )
    style.configure(
        "Step.TLabel", background=CARD, foreground=ACCENT, font=("微軟正黑體", 9, "bold")
    )
    style.configure(
        "Hint.TLabel", background=CARD, foreground=MUTED, font=("微軟正黑體", 8)
    )
    style.configure(
        "Filename.TLabel", background=CARD, foreground=MUTED, font=("微軟正黑體", 9)
    )

    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground="white",
        font=("微軟正黑體", 11, "bold"),
        padding=(12, 10),
        borderwidth=0,
        focuscolor=ACCENT,
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", ACCENT_DISABLED), ("active", ACCENT_ACTIVE)],
        foreground=[("disabled", "#ffffff")],
    )
    style.configure(
        "Secondary.TButton",
        background="#eef0f6",
        foreground=TEXT,
        font=("微軟正黑體", 9),
        padding=(10, 6),
        borderwidth=0,
    )
    style.map("Secondary.TButton", background=[("active", "#e1e4ee")])
    style.configure(
        "Link.TButton",
        background=BG,
        foreground=MUTED,
        font=("微軟正黑體", 8, "underline"),
        padding=(0, 0),
        borderwidth=0,
        relief="flat",
    )
    style.map("Link.TButton", foreground=[("active", ACCENT)])

    style.configure(
        "Accent.Horizontal.TProgressbar", background=ACCENT, troughcolor="#eceefb"
    )
    style.configure(
        "TCombobox",
        fieldbackground="white",
        background="white",
        foreground=TEXT,
        padding=6,
    )
    style.configure("TEntry", fieldbackground="white", padding=6)

    def card(parent) -> ttk.Frame:
        outer = tk.Frame(parent, bg=BORDER)
        outer.pack(fill="x", pady=(0, 12))
        inner = ttk.Frame(outer, style="Card.TFrame", padding=16)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return inner

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
    frm = ttk.Frame(root, padding=20)
    frm.pack(fill="both", expand=True)

    header = ttk.Frame(frm)
    header.pack(fill="x", pady=(0, 16))
    ttk.Label(header, text="會議錄音轉文字工具", style="Title.TLabel").pack(anchor="w")
    ttk.Label(
        header,
        text="選擇錄音檔 → 自動轉文字 → AI 整理成會議紀錄與 Word 檔",
        style="Subtitle.TLabel",
    ).pack(anchor="w", pady=(2, 0))

    # ---- STEP 1：選擇音檔 ----
    c1 = card(frm)
    ttk.Label(c1, text="STEP 1　選擇會議錄音檔", style="Step.TLabel").pack(anchor="w")

    file_row = ttk.Frame(c1, style="Card.TFrame")
    file_row.pack(fill="x", pady=(10, 0))
    lbl_file = ttk.Label(
        file_row,
        text="尚未選擇檔案",
        style="Filename.TLabel",
        wraplength=420,
        justify="left",
    )
    lbl_file.pack(side="left", fill="x", expand=True, padx=(0, 12))
    btn_pick = ttk.Button(file_row, text="📂 選擇音檔", style="Secondary.TButton")
    btn_pick.pack(side="right")

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
            lbl_file.config(text=path, foreground=TEXT)
            refresh_start_state()

    btn_pick.config(command=pick_file)

    # ---- STEP 2：選項 ----
    c2 = card(frm)
    ttk.Label(c2, text="STEP 2　設定選項（選填）", style="Step.TLabel").pack(anchor="w")

    opt_row = ttk.Frame(c2, style="Card.TFrame")
    opt_row.pack(fill="x", pady=(10, 0))
    ttk.Label(opt_row, text="語言", style="Card.TLabel").pack(side="left")
    lang_var = tk.StringVar(value="自動偵測")
    lang_box = ttk.Combobox(
        opt_row,
        textvariable=lang_var,
        state="readonly",
        width=12,
        values=["自動偵測", "中文 (zh)", "英文 (en)"],
    )
    lang_box.pack(side="left", padx=(10, 0))

    ttk.Label(c2, text="輸出文件類型", style="Card.TLabel").pack(anchor="w", pady=(14, 0))
    TEMPLATE_CUSTOM = "自訂範本..."
    template_names = list(FORMAT_PRESETS.keys()) + [TEMPLATE_CUSTOM]
    template_var = tk.StringVar(value=DEFAULT_TEMPLATE)
    template_box = ttk.Combobox(
        c2,
        textvariable=template_var,
        state="readonly",
        width=20,
        values=template_names,
    )
    template_box.pack(anchor="w", pady=(6, 0))

    custom_template_frame = ttk.Frame(c2, style="Card.TFrame")
    custom_template_text = tk.Text(
        custom_template_frame,
        height=6,
        wrap="word",
        font=("Consolas", 9),
        bg="white",
        fg=TEXT,
        relief="solid",
        bd=1,
        highlightthickness=0,
    )
    custom_template_text.pack(fill="x", pady=(6, 0))
    ttk.Label(
        custom_template_frame,
        text="輸入你想要的 Markdown 結構規格（例如自訂標題與條列項目），會取代預設格式",
        style="Hint.TLabel",
    ).pack(anchor="w", pady=(4, 0))

    def on_template_change(*_args) -> None:
        if template_var.get() == TEMPLATE_CUSTOM:
            custom_template_frame.pack(fill="x", pady=(8, 0))
        else:
            custom_template_frame.pack_forget()

    template_box.bind("<<ComboboxSelected>>", on_template_change)

    ttk.Label(c2, text="補充提示詞（產業別／專有名詞）", style="Card.TLabel").pack(
        anchor="w", pady=(14, 0)
    )
    context_var = tk.StringVar()
    context_entry = ttk.Entry(c2, textvariable=context_var)
    context_entry.pack(fill="x", pady=(6, 0))
    ttk.Label(
        c2,
        text="例如：半導體產業、蝕刻機台、良率、公司內部代號等，可提升辨識與整理的用詞準確度",
        style="Hint.TLabel",
    ).pack(anchor="w", pady=(4, 0))

    # ---- STEP 3：開始 ----
    c3 = card(frm)
    ttk.Label(c3, text="STEP 3　開始轉換", style="Step.TLabel").pack(anchor="w")

    btn_start = ttk.Button(c3, text="▶  開始轉換", style="Accent.TButton", state="disabled")
    btn_start.pack(fill="x", pady=(10, 10))

    progress = ttk.Progressbar(c3, mode="indeterminate", style="Accent.Horizontal.TProgressbar")

    status_row = ttk.Frame(c3, style="Card.TFrame")
    status_row.pack(fill="x", pady=(4, 0))
    lbl_status_dot = tk.Label(status_row, text="●", fg=MUTED, bg=CARD, font=("Arial", 10))
    lbl_status_dot.pack(side="left")
    lbl_status = ttk.Label(status_row, text="就緒，請先選擇音檔", style="Card.TLabel")
    lbl_status.pack(side="left", padx=(6, 0))

    result_row = ttk.Frame(c3, style="Card.TFrame")
    btn_open = ttk.Button(result_row, text="📁 開啟輸出資料夾", style="Secondary.TButton")

    def refresh_start_state() -> None:
        btn_start.config(state="normal" if state["audio"] else "disabled")

    # ---- API Key 狀態列（頁尾，弱化處理） ----
    row_key = ttk.Frame(frm)
    row_key.pack(fill="x", pady=(4, 0))
    lbl_key = ttk.Label(row_key, style="Subtitle.TLabel")
    btn_reset_key = ttk.Button(row_key, text="重新設定 API Key", style="Link.TButton")

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

    def set_status(text: str, color: str = MUTED) -> None:
        lbl_status.config(text=text)
        lbl_status_dot.config(fg=color)

    def set_busy(busy: bool) -> None:
        if busy:
            progress.pack(fill="x", pady=(0, 10), before=status_row)
            progress.start(12)
        else:
            progress.stop()
            progress.pack_forget()
        btn_start.config(state="disabled" if busy else ("normal" if state["audio"] else "disabled"))
        btn_pick.config(state="disabled" if busy else "normal")
        context_entry.config(state="disabled" if busy else "normal")
        lang_box.config(state="disabled" if busy else "readonly")
        template_box.config(state="disabled" if busy else "readonly")
        custom_template_text.config(state="disabled" if busy else "normal")

    def on_done(result: dict) -> None:
        def ui():
            set_busy(False)
            set_status("完成！", SUCCESS)
            btn_open.config(command=lambda: os.startfile(str(result["out_dir"])))
            result_row.pack(fill="x", pady=(10, 0))
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
            set_status("失敗，請查看錯誤訊息", ERROR)
            messagebox.showerror("發生錯誤", msg[:1500])

        root.after(0, ui)

    def start() -> None:
        audio = state["audio"]
        if audio is None:
            messagebox.showwarning("提醒", "請先選擇音檔")
            return
        if template_var.get() == TEMPLATE_CUSTOM:
            format_spec = custom_template_text.get("1.0", "end").strip()
            if not format_spec:
                messagebox.showwarning("提醒", "請輸入自訂範本內容，或改選預設文件類型")
                return
        else:
            format_spec = FORMAT_PRESETS[template_var.get()]
        result_row.pack_forget()
        lang_map = {"自動偵測": None, "中文 (zh)": "zh", "英文 (en)": "en"}
        task = PipelineTask(
            audio,
            lang_map.get(lang_var.get()),
            context_var.get(),
            format_spec,
            on_status=lambda s: root.after(0, lambda: set_status(s, ACCENT)),
            on_done=on_done,
            on_error=on_error,
        )
        set_busy(True)
        set_status("處理中...", ACCENT)
        task.start()

    btn_start.config(command=start)

    def on_close() -> None:
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    # ---- 依實際內容自動調整預設高度，避免底部按鈕被遮住 ----
    root.update_idletasks()
    content_w = frm.winfo_reqwidth() + 40
    content_h = frm.winfo_reqheight() + 40
    screen_h = root.winfo_screenheight()
    win_w = max(640, content_w)
    win_h = min(max(600, content_h), screen_h - 80)
    root.geometry(f"{win_w}x{win_h}")
    root.minsize(win_w, 480)

    root.mainloop()


if __name__ == "__main__":
    main()
