# 會議錄音轉文字工具（Meeting2Text）

用 Groq 免費模型把會議錄音自動轉成逐字稿，再整理成結構化會議紀錄（Markdown + Word）。

## 功能

- 選擇音檔 → 自動轉文字（Whisper large-v3-turbo）→ AI 整理成會議紀錄（`openai/gpt-oss-120b`）
- 一鍵匯出 `.docx` 會議紀錄
- 圖形介面（`app.py`），也可用命令列（`main.py`）
- 已儲存過的逐字稿會自動重用，不用重跑語音辨識

## 自動轉檔壓縮（因應 Groq 25MB 上傳限制）

Groq API 對單一音檔有 **25MB** 的上傳限制。選好檔案後，程式會自動判斷是否需要轉檔，轉檔完成才會上傳：

1. **判斷是否需要轉檔**（`needs_conversion`）：檔案大小 **超過 25MB**，或副檔名不在 Groq 支援清單內，就會觸發轉檔。
2. **第一步：轉成 opus**：用 ffmpeg 轉成單聲道、16kHz、16kbps 的 `.opus`（壓縮率最高、Whisper 原生支援）。
3. **第二步（保底）：若 opus 仍超過 25MB**，再改轉成 `.mp3`（同樣 16kbps、單聲道、16kHz）確保在上傳限制內。
4. 若轉檔後仍超過 25MB（通常代表錄音真的非常長），會直接提示錯誤，建議先分段處理。

轉檔後的檔案會存到輸出資料夾（GUI 版存在 `<音檔所在目錄>/meeting2text_output`，CLI 版存在 `output/`），不會覆蓋原始音檔。

Groq 原生支援的格式（不需轉檔，前提是 ≤25MB）：
`.flac` `.mp3` `.mp4` `.mpeg` `.mpga` `.m4a` `.ogg` `.opus` `.wav` `.webm`

## 使用方式

### 下載直接使用（Windows）

從 [Releases](../../releases) 下載 `Meeting2Text.exe`，雙擊執行即可，不需要安裝 Python。

1. 第一次啟動會要求輸入 Groq API Key（於 [console.groq.com](https://console.groq.com) 免費申請），Key 會存在程式旁的 `.env`。
2. 點選「選擇音檔」→ 選語言（可自動偵測）→「開始轉換」。
3. 完成後可直接點「開啟輸出資料夾」查看逐字稿、會議紀錄（`.md`）與 Word 檔（`.docx`）。

### 從原始碼執行

```bash
pip install -r requirements.txt
python app.py          # 圖形介面
python main.py <音檔路徑> [--language zh] [--reuse-transcript]   # 命令列
```

需要在專案目錄放一個 `.env`：

```
GROQ_API_KEY=你的_groq_api_key
```

也需要安裝 [ffmpeg](https://ffmpeg.org/) 並確保在系統 PATH 中（打包好的 exe 已內建 ffmpeg，不需另外安裝）。

## 為什麼執行檔（exe）這麼大（約 91MB）

`Meeting2Text.exe` 是用 PyInstaller 把 Python 直譯器、所有套件依賴、GUI 函式庫、以及 **完整版 ffmpeg.exe** 全部打包成單一檔案，所以體積會比一般小工具大很多，主要來源：

- **ffmpeg.exe（佔比最大）**：內建的是官方完整功能編譯版（支援 H.264/H.265/AV1、libx264、libvpx、libass 字幕、Vulkan 濾鏡等一大堆編解碼器與濾鏡），本工具其實只用到其中的音訊轉檔（opus/mp3）功能，但因為是單一靜態執行檔沒辦法裁切，只能整個包進去。
- **Python 直譯器 + 標準函式庫**：PyInstaller 會把整個 CPython runtime 打包進來，而不是依賴使用者電腦上是否有安裝 Python。
- **第三方套件**：`openai`（Groq API SDK）、`python-docx`（匯出 Word）、`tkinter`（GUI）等函式庫與其依賴也會一起打包。
- 另外注意：`Meeting2Text.spec` 裡 UPX 壓縮**刻意排除了 ffmpeg.exe**（`upx_exclude=['ffmpeg.exe']`），因為之前發現 UPX 壓縮這顆巨大的靜態執行檔會讓它啟動時直接崩潰（exit code `-2`）。所以 ffmpeg.exe 是以未壓縮的原始大小打包進去，這也是體積偏大的原因之一，但換來的是穩定可執行。

如果之後想縮小體積，可以考慮改用「精簡版 ffmpeg」（只編譯音訊相關 codec，去掉影片/濾鏡功能），通常能省下 60~70MB。

## 打包成 exe

```bash
pyinstaller Meeting2Text.spec
```

輸出在 `dist/Meeting2Text.exe`（內含 ffmpeg，UPX 壓縮已排除 ffmpeg.exe 以避免執行檔損毀）。
