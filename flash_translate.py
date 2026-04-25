"""
Flash Translate for Windows
選取文字後，雙擊 Ctrl（或按 Ctrl+Shift+T）即可翻譯。
"""

import sys
import time
import threading
import tkinter as tk
from typing import Optional, Tuple

# ── 必要套件檢查 ─────────────────────────────────────────────────────────────
def _require(pkg, install_name=None):
    import importlib
    try:
        return importlib.import_module(pkg)
    except ImportError:
        name = install_name or pkg
        print(f"缺少套件，請執行: pip install {name}")
        sys.exit(1)

keyboard     = _require('keyboard')
pyperclip    = _require('pyperclip')
deep_trans   = _require('deep_translator', 'deep-translator')
GoogleTranslator = deep_trans.GoogleTranslator

try:
    from pypinyin import pinyin as to_pinyin, Style
    HAS_PINYIN = True
except ImportError:
    HAS_PINYIN = False
    print("提示: 安裝 pypinyin 可顯示拼音 (pip install pypinyin)")

try:
    import win32api
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

try:
    from gtts import gTTS
    import pygame
    import tempfile, os
    HAS_TTS = True
    pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
    pygame.mixer.init()
except Exception:
    HAS_TTS = False
    print("提示: 安裝 gtts 和 pygame 可使用語音功能 (pip install gtts pygame)")


# ── 設定 ──────────────────────────────────────────────────────────────────────
DOUBLE_TAP_MS    = 400       # 雙擊 Ctrl 最大間隔 (毫秒)
POPUP_TIMEOUT_MS = 8_000     # 浮動視窗自動關閉時間 (毫秒)
POPUP_W, POPUP_H = 380, 280
FALLBACK_TARGET  = 'zh-TW'   # 非中文文字的預設翻譯目標

# ── Catppuccin Mocha 配色 ─────────────────────────────────────────────────────
C_BASE    = '#1e1e2e'
C_CRUST   = '#11111b'
C_SURFACE = '#313244'
C_OVERLAY = '#45475a'
C_TEXT    = '#cdd6f4'
C_SUBTEXT = '#6c7086'
C_BLUE    = '#89b4fa'
C_LAVENDER= '#b4befe'
C_GREEN   = '#a6e3a1'
C_MAUVE   = '#cba6f7'


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def is_chinese(text: str) -> bool:
    return any('一' <= ch <= '鿿' for ch in text)


def get_pinyin(text: str) -> Optional[str]:
    if not HAS_PINYIN:
        return None
    try:
        return ' '.join(item[0] for item in to_pinyin(text, style=Style.TONE))
    except Exception:
        return None


def cursor_pos() -> Tuple[int, int]:
    if HAS_WIN32:
        try:
            return win32api.GetCursorPos()
        except Exception:
            pass
    return (500, 400)


def clamp(x: int, y: int, w: int, h: int, root) -> Tuple[int, int]:
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    return min(x + 18, sw - w - 10), min(y + 18, sh - h - 10)


# ── 浮動翻譯視窗 ──────────────────────────────────────────────────────────────

class TranslationPopup:
    def __init__(self, parent, x: int, y: int,
                 original: str, translated: str,
                 pronunciation: Optional[str] = None):
        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.attributes('-alpha', 0.96)
        self.win.configure(bg=C_BASE)

        px, py = clamp(x, y, POPUP_W, POPUP_H, parent)
        self.win.geometry(f'{POPUP_W}x{POPUP_H}+{px}+{py}')

        self._drag_ox = self._drag_oy = 0
        self._timer_id = None
        self._build(original, translated, pronunciation)
        self._start_timer()
        self.win.bind('<Escape>', lambda _e: self.close())

    def _build(self, original: str, translated: str, pronunciation: Optional[str]):
        # ── 標題列 (可拖移) ────────────────────────────────────────────────
        bar = tk.Frame(self.win, bg=C_CRUST, height=28)
        bar.pack(fill='x')
        bar.pack_propagate(False)

        tk.Label(bar, text=' ⚡ Flash Translate',
                 fg=C_SUBTEXT, bg=C_CRUST, font=('Segoe UI', 8)).pack(side='left', pady=5)

        close = tk.Label(bar, text='✕ ', fg=C_SUBTEXT, bg=C_CRUST,
                         font=('Segoe UI', 10), cursor='hand2')
        close.pack(side='right')
        close.bind('<Button-1>', lambda _e: self.close())
        _hover(close, C_TEXT, C_SUBTEXT, C_CRUST)

        # 只在標題列綁拖移，不包含 X 按鈕（否則會覆蓋關閉事件）
        bar.bind('<Button-1>', self._drag_start)
        bar.bind('<B1-Motion>', self._drag_move)

        # ── 按鈕列固定在底部 ────────────────────────────────────────────────
        btn_bar = tk.Frame(self.win, bg=C_CRUST, padx=14, pady=6)
        btn_bar.pack(side='bottom', fill='x')

        # 語音按鈕需要保留參考以切換圖示
        b_orig = _speak_btn(btn_bar, '🔊 原文')
        b_orig.bind('<Button-1>', lambda e: self._speak_toggle(original, b_orig, '🔊 原文'))
        b_orig.pack(side='left')

        b_trans = _speak_btn(btn_bar, '🔊 譯文')
        b_trans.bind('<Button-1>', lambda e: self._speak_toggle(translated, b_trans, '🔊 譯文'))
        b_trans.pack(side='left', padx=(6, 0))

        _btn(btn_bar, '📋 複製', lambda: self._copy(translated)).pack(side='left', padx=(6, 0))

        # ── 原文 (灰色、截斷) ────────────────────────────────────────────────
        top = tk.Frame(self.win, bg=C_BASE, padx=14)
        top.pack(fill='x', pady=(8, 4))

        short = original if len(original) <= 70 else original[:70] + '…'
        tk.Label(top, text=short, fg=C_SUBTEXT, bg=C_BASE,
                 font=('Segoe UI', 9), anchor='w',
                 wraplength=350, justify='left').pack(fill='x')

        tk.Frame(self.win, bg=C_SURFACE, height=1).pack(fill='x', padx=14)

        # ── 可捲動翻譯區 ────────────────────────────────────────────────────
        scroll_frame = tk.Frame(self.win, bg=C_BASE, padx=14, pady=8)
        scroll_frame.pack(fill='both', expand=True)

        sb = tk.Scrollbar(scroll_frame, orient='vertical')
        sb.pack(side='right', fill='y')

        txt = tk.Text(scroll_frame, bg=C_BASE, fg=C_TEXT,
                      font=('Segoe UI', 12, 'bold'),
                      wrap='word', relief='flat', bd=0,
                      cursor='arrow', padx=0, pady=0,
                      selectbackground=C_SURFACE,
                      yscrollcommand=sb.set)
        txt.pack(side='left', fill='both', expand=True)
        sb.configure(command=txt.yview)

        # 必須先設定 tag 再 insert，disabled 狀態下 tag 顏色才能覆蓋系統灰色
        txt.tag_configure('body', foreground=C_TEXT, font=('Segoe UI', 12, 'bold'))
        txt.tag_configure('pinyin', foreground=C_BLUE, font=('Segoe UI', 9))

        txt.insert('end', translated, 'body')
        if pronunciation:
            txt.insert('end', f'\n{pronunciation}', 'pinyin')
        txt.configure(state='disabled')

        # 滑鼠滾輪捲動
        txt.bind('<MouseWheel>',
                 lambda e: txt.yview_scroll(-1 * (e.delta // 120), 'units'))

    def _speak_toggle(self, text: str, btn: tk.Label, play_label: str):
        if not HAS_TTS:
            print("語音套件未安裝，請執行: pip install gtts pygame")
            return

        if pygame.mixer.music.get_busy():
            # 正在播放 → 立即停止
            pygame.mixer.music.stop()
            btn.config(text=play_label)
            return

        # 尚未播放 → 開始播放
        lang = 'zh-tw' if is_chinese(text) else 'en'
        btn.config(text='⏹ 停止')

        def _run():
            try:
                tts = gTTS(text=text, lang=lang, slow=False)
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
                tmp_path = tmp.name
                tmp.close()
                tts.save(tmp_path)
                pygame.mixer.music.load(tmp_path)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    time.sleep(0.05)
                pygame.mixer.music.unload()
                os.unlink(tmp_path)
            except Exception as e:
                print(f'TTS 錯誤: {e}')
            finally:
                # 播放結束後在主執行緒還原按鈕文字（視窗可能已關閉）
                def _reset():
                    try:
                        if btn.winfo_exists():
                            btn.config(text=play_label)
                    except Exception:
                        pass
                try:
                    btn.after(0, _reset)
                except Exception:
                    pass

        threading.Thread(target=_run, daemon=True).start()

    def _copy(self, text: str):
        try:
            pyperclip.copy(text)
        except Exception:
            pass

    def _drag_start(self, e):
        self._drag_ox, self._drag_oy = e.x, e.y

    def _drag_move(self, e):
        nx = self.win.winfo_x() + e.x - self._drag_ox
        ny = self.win.winfo_y() + e.y - self._drag_oy
        self.win.geometry(f'+{nx}+{ny}')

    def _start_timer(self):
        if self._timer_id:
            self.win.after_cancel(self._timer_id)
        self._timer_id = self.win.after(15_000, self.close)

    def close(self):
        try:
            if self._timer_id:
                self.win.after_cancel(self._timer_id)
            self.win.destroy()
        except Exception:
            pass


# ── 小工具：按鈕、hover 效果 ──────────────────────────────────────────────────

def _btn(parent, text: str, cmd) -> tk.Label:
    b = tk.Label(parent, text=text, fg=C_BLUE, bg=C_SURFACE,
                 font=('Segoe UI', 9), padx=9, pady=3, cursor='hand2')
    b.bind('<Button-1>', lambda _e: cmd())
    _hover(b, C_LAVENDER, C_BLUE, C_SURFACE, C_OVERLAY)
    return b


def _speak_btn(parent, text: str) -> tk.Label:
    """語音按鈕（不預綁指令，由呼叫端設定 toggle 行為）"""
    b = tk.Label(parent, text=text, fg=C_BLUE, bg=C_SURFACE,
                 font=('Segoe UI', 9), padx=9, pady=3, cursor='hand2')
    _hover(b, C_LAVENDER, C_BLUE, C_SURFACE, C_OVERLAY)
    return b


def _hover(widget, fg_in, fg_out, bg_out, bg_in=None):
    if bg_in is None:
        bg_in = widget.cget('bg')
    widget.bind('<Enter>', lambda _e: widget.config(fg=fg_in, bg=bg_in))
    widget.bind('<Leave>', lambda _e: widget.config(fg=fg_out, bg=bg_out))


# ── 主程式 ────────────────────────────────────────────────────────────────────

class FlashTranslate:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title('Flash Translate')

        self._popup: Optional[TranslationPopup] = None
        self._last_ctrl_time = 0.0
        self._ctrl_is_clean = True
        self._busy = False        # 正在送出內部 Ctrl+C 期間
        self._translating = False # 翻譯流程執行中，防止重複觸發
        self._ignore_until = 0.0  # 送出 Ctrl+C 後的冷卻，避免合成事件干擾偵測

        keyboard.hook(self._key_event)
        keyboard.add_hotkey('ctrl+shift+t',
                             lambda: None if self._busy or self._translating else self._trigger())

        print('=' * 40)
        print('  Flash Translate 已啟動')
        print('  • 選取文字後，快速雙擊 Ctrl 翻譯')
        print('  • 或使用快捷鍵 Ctrl+Shift+T')
        print('  • 按 Esc 或右上角 X 關閉翻譯視窗')
        print('  • 在此視窗按 Ctrl+C 可退出程式')
        print('=' * 40)

    # ── 鍵盤事件處理 ──────────────────────────────────────────────────────────

    def _key_event(self, event):
        if self._busy or time.time() < self._ignore_until:
            return

        name = event.name or ''
        is_ctrl = name in ('ctrl', 'left ctrl', 'right ctrl')

        if event.event_type == keyboard.KEY_DOWN:
            if is_ctrl:
                self._ctrl_is_clean = True
            elif self._last_ctrl_time > 0:
                self._ctrl_is_clean = False  # 有其他鍵被按下

        elif event.event_type == keyboard.KEY_UP:
            if is_ctrl and self._ctrl_is_clean:
                now = time.time()
                elapsed_ms = (now - self._last_ctrl_time) * 1000
                if 50 < elapsed_ms < DOUBLE_TAP_MS:
                    # 雙擊 Ctrl 成立
                    self._last_ctrl_time = 0.0
                    self._trigger()
                else:
                    self._last_ctrl_time = now
                self._ctrl_is_clean = False

    def _trigger(self):
        if self._translating:
            return
        threading.Thread(target=self._do_translate, daemon=True).start()

    # ── 翻譯流程 ──────────────────────────────────────────────────────────────

    def _do_translate(self):
        self._translating = True
        try:
            self._run_translate()
        finally:
            self._translating = False

    def _run_translate(self):
        # 儲存原始剪貼簿，並放入哨兵值以偵測是否有選取文字
        _SENTINEL = '\x01FLASH_TRANSLATE\x01'
        try:
            prev = pyperclip.paste()
            pyperclip.copy(_SENTINEL)
        except Exception:
            prev = ''

        # 模擬 Ctrl+C 複製選取文字
        self._busy = True
        keyboard.send('ctrl+c')
        time.sleep(0.3)
        self._busy = False
        # 送出後冷卻 300ms，讓合成事件消化完再恢復 Ctrl 偵測
        self._ignore_until = time.time() + 0.3

        try:
            text = pyperclip.paste()
        except Exception:
            text = ''
        finally:
            # 立即還原剪貼簿（不需要延遲）
            try:
                pyperclip.copy(prev)
            except Exception:
                pass

        text = text.strip()

        # 剪貼簿仍是哨兵 → 沒有選取文字
        if not text or text == _SENTINEL or len(text) > 1000:
            return

        # 智慧判斷翻譯方向
        target = 'en' if is_chinese(text) else FALLBACK_TARGET

        # 呼叫 Google 翻譯
        try:
            translated = GoogleTranslator(source='auto', target=target).translate(text)
        except Exception as e:
            print(f'翻譯錯誤: {e}')
            return

        if not translated or translated.strip() == text.strip():
            return

        # 取得拼音（中文原文 → 顯示原文拼音；翻譯結果是中文 → 顯示譯文拼音）
        pronunciation: Optional[str] = None
        if is_chinese(text):
            pronunciation = get_pinyin(text)
        elif is_chinese(translated):
            pronunciation = get_pinyin(translated)

        x, y = cursor_pos()
        self.root.after(0, self._show_popup, x, y, text, translated, pronunciation)

    def _show_popup(self, x, y, original, translated, pronunciation):
        try:
            if self._popup:
                self._popup.close()
            self._popup = TranslationPopup(
                self.root, x, y, original, translated, pronunciation
            )
        except Exception as e:
            print(f'視窗錯誤: {e}')

    def run(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            keyboard.unhook_all()
            print('Flash Translate 已關閉。')


if __name__ == '__main__':
    FlashTranslate().run()
