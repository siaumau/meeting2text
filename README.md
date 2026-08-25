# 會議錄音轉文字工具（Meeting2Text）

用 Groq 免費模型把會議錄音自動轉成逐字稿，再整理成結構化會議紀錄（Markdown + Word）。

## 功能

- 選擇音檔 → 自動轉文字（Whisper large-v3-turbo）→ AI 整理成會議紀錄（`openai/gpt-oss-120b`）
- 一鍵匯出 `.docx` 會議紀錄
- 圖形介面（`app.py`），也可用命令列（`main.py`）
- 已儲存過的逐字稿會自動重用，不用重跑語音辨識

## 輸出格式：Markdown / Word / 美化 HTML

每次轉換完成後，輸出資料夾會同時產生三種格式：

- `*_minutes.md`：原始 Markdown 逐字整理內容
- `*_minutes.docx`：Word 文件
- `*_minutes.html`：美化過的網頁版，卡片式排版、粉色系配色（跟 App 風格一致），標題、條列、表格都有樣式，支援深色模式（跟隨系統），也可以直接用瀏覽器開啟列印或另存 PDF

GUI 完成後除了「📁 開啟輸出資料夾」，也有「🌐 開啟美化 HTML」按鈕可以直接用瀏覽器打開看排版好的結果。

## YouTube 影片摘要

STEP 1 可以切換「📂 本機音檔」或「🔗 YouTube 連結」。選 YouTube 連結貼上網址後，程式會用 `yt-dlp` 只下載該影片的音軌（不下載影片畫面），下載完直接進入跟本機音檔一樣的轉文字＋整理流程。

- 下載到的音檔與輸出結果會存在程式旁的 `meeting2text_output/<影片標題>/`。
- 建議搭配「輸出文件類型」選「影片重點整理」（章節式重點、關鍵結論、值得查證事項），會比會議紀錄格式更適合影片內容。
- CLI 版對應 `--youtube <網址>`，與音檔路徑參數擇一使用。
- 僅供個人筆記整理使用；請勿用來下載受著作權保護、非公開或會員限定的內容。

## 輸出文件類型（可選預設範本或自訂）

STEP 2 新增「輸出文件類型」下拉選單，決定整理出來的文件結構長什麼樣子：

- **會議紀錄**（預設）：重點摘要／討論事項／決議事項／待辦事項／待確認事項
- **訪談紀錄**：受訪者背景／重點問答整理／關鍵引述／待確認事項
- **課程筆記**：主題摘要／重點內容／重要名詞定義／延伸問題
- **通用摘要**：適合不屬於以上情境的內容，簡單摘要＋重點條列
- **自訂範本**：選這項會出現一個文字框，可以自己輸入想要的 Markdown 結構（標題、條列項目），送出後會直接取代預設格式

CLI 版對應 `--template`（從上述四種預設選一個）或 `--custom-template "你的規格文字"`（優先權高於 `--template`）。

## 補充提示詞（改善專有名詞辨識準確度）

語音辨識對「沒特別說明就會泛化成一般生活用字」的產業/專有名詞常常會辨識錯（例如同音字誤植成常見詞）。GUI 上新增一個**選填**的「補充提示詞」欄位（CLI 版對應 `--context` 參數），可以填入產業別、專案代號、常出現的專有名詞或人名等，程式會在兩個階段都用上：

1. **語音辨識階段**：提示詞會當成 Whisper 的 `prompt` 參數送出，直接引導模型辨識時採用你提供的詞彙，而不是事後校正——這是準確度提升最主要的來源。
2. **AI 整理階段**：提示詞也會附加在整理逐字稿的 prompt 中，讓 LLM 依產業背景修正明顯的同音誤植用詞，並在條列會議紀錄時用字更貼近實際場景。

**使用建議：**
- 內容以「名詞」為主，例如：`半導體產業，常提到蝕刻機台、良率、Fab 廠、EUV`，不需要寫成完整句子。
- 越具體效果越好，例如專案代號、產品型號、內部術語、與會者職稱等都可以列進去。
- Whisper prompt 有長度限制，程式會自動截到 800 字，建議只列真正關鍵、常被念到的詞彙，不要塞整篇背景說明。
- 不同會議如果領域不同，建議每次依實際主題調整這欄內容，而不是固定用同一組詞。

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

從 [Releases](../../releases) 下載 `Meeting2Text-vX.X.X-win.zip`，解壓縮後執行資料夾內的 `Meeting2Text.exe` 即可，不需要安裝 Python。

> 從 v1.0.3 起改為「資料夾」形式發布（而非單一 exe），因為單一自解壓 exe + UPX 壓縮很容易被 Windows Defender / SmartScreen 誤判為病毒（false positive）。解壓後請保留整個資料夾（`.exe` 需要跟旁邊的 `_internal` 資料夾一起才能執行），不要只複製 `Meeting2Text.exe` 單一檔案。

1. 第一次啟動會要求輸入 Groq API Key（於 [console.groq.com](https://console.groq.com) 免費申請），Key 會存在程式旁的 `.env`。
2. 點選「選擇音檔」→ 選語言（可自動偵測）→「開始轉換」。
3. 完成後可直接點「開啟輸出資料夾」查看逐字稿、會議紀錄（`.md`）與 Word 檔（`.docx`）。

### 從原始碼執行

```bash
pip install -r requirements.txt
python app.py          # 圖形介面
python main.py <音檔路徑> [--language zh] [--context "半導體產業，常提到蝕刻機台、良率"] [--reuse-transcript]   # 命令列
```

需要在專案目錄放一個 `.env`：

```
GROQ_API_KEY=你的_groq_api_key
```

也需要安裝 [ffmpeg](https://ffmpeg.org/) 並確保在系統 PATH 中（打包好的 exe 已內建 ffmpeg，不需另外安裝）。

## 執行檔被 Windows 判定為病毒怎麼辦

PyInstaller 打包出來的「單一自解壓 exe」+ UPX 壓縮，是 Windows Defender / SmartScreen 最容易誤報（false positive）的組合：exe 執行時會把內容解壓到暫存目錄再啟動，這個行為模式跟部分真的惡意程式類似，容易被啟發式掃描誤抓。ffmpeg.exe 本身沒有數位簽章，也常被單獨誤判。

因此 v1.0.3 起改成「資料夾」發布方式（`--onedir`）並關閉 UPX 壓縮，不再是單一 exe，可大幅降低誤報機率。若你打包後仍被誤判：

- 用 [VirusTotal](https://www.virustotal.com/) 上傳確認，若只有零星一兩家小廠商引擎報、其餘大廠（Defender、Kaspersky、BitDefender 等）都乾淨，基本可判定是誤報。
- 可到 [Microsoft Defender 誤判提交](https://www.microsoft.com/en-us/wdsi/filesubmission) 回報，審核通過後該版本較不會再被擋。
- 最徹底的做法是購買程式碼簽章憑證（Code Signing Certificate）幫 exe 簽章，但需要費用。

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
