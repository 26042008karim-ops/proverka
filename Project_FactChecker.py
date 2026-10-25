import sys
import os
import customtkinter as ctk
import threading
import json
import re
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from pathlib import Path

# Принудительно ставим UTF-8
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr is not None:
    sys.stderr.reconfigure(encoding='utf-8')

# --- 1. НАСТРОЙКИ API ---
OPENAI_API_KEY = "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"
os.environ['HTTP_PROXY'] = 'http://127.0.0.1:10808'
os.environ['HTTPS_PROXY'] = 'http://127.0.0.1:10808'

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENAI_API_KEY,
    default_headers={"Content-Type": "application/json; charset=utf-8"}
)

# Файлы для хранения источников (ОТДЕЛЬНЫЕ для доверенных и недоверенных)
TRUSTED_SOURCES_FILE = Path(__file__).parent / "trusted_sources.json"
UNTRUSTED_SOURCES_FILE = Path(__file__).parent / "untrusted_sources.json"

# --- 2. ДИЗАЙН-СИСТЕМА ---
BG_MAIN = "#0D0D14"
BG_SIDEBAR = "#14141D"
BG_CARD = "#1A1A25"
BG_SOURCES_BLOCK = "#12121A"
BORDER_SUBTLE = "#262633"
BORDER_TRUSTED = "#1F3A2E"
BORDER_UNTRUSTED = "#3A1F2E"

TEXT_PRIMARY = "#E2E2E8"
TEXT_SECONDARY = "#8D8D9B"
TEXT_DIM = "#5A5A6A"

ACCENT_CYAN = "#00E5FF"
ACCENT_PINK = "#FF0055"
ACCENT_PURPLE = "#9D4EDD"
ACCENT_YELLOW = "#FEE440"
ACCENT_MINT = "#00F5D4"


# --- 3. ПРОМПТЫ ---
BASE_SYSTEM_PROMPT = """
Ты — эксперт по факт-чекингу. Проанализируй новость и ответь СТРОГО в формате JSON:
{
"source_trust": "низкое/среднее/высокое",
"source_reason": "почему такой вердикт (1 предложение)",
"clickbait_score": 0-100,
"clickbait_phrases": ["фраза1", "фраза2"],
"truth_risk": "низкий/средний/высокий",
"explanation": "объясни суть новости простыми словами (2-3 предложения)",
"terms": ["термин1: объяснение", "термин2: объяснение"],
"action_advice": "что делать пользователю (1-2 предложения)"
}
"""
PARANOIA_PROMPT = """
ВНИМАНИЕ! ВКЛЮЧЕН РЕЖИМ ПАРАНОЙИ.
Будь максимально подозрительным. Ищи скрытый смысл, скрытые угрозы, манипуляции и подтекст даже в самых безобидных словах.
Если в тексте есть хоть малейший намек на заговор или пропаганду — резко повышай индекс кликбейта и понижай доверие.
"""


class FactCheckApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("AI Fact-Checker Pro")
        self.geometry("1050x780")  # ← уменьшили окно
        self.configure(fg_color=BG_MAIN)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.current_report_text = ""
        self.trusted_sources = []
        self.untrusted_sources = []
        self.trusted_widgets = {}
        self.untrusted_widgets = {}

        # ================= ЛЕВАЯ ПАНЕЛЬ =================
        self.sidebar_frame = ctk.CTkFrame(
            self, width=360, fg_color=BG_SIDEBAR,  # ← было 400
            border_color=BORDER_SUBTLE, border_width=1, corner_radius=0
        )
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(3, weight=1)
        self.sidebar_frame.grid_propagate(False)

        # Логотип
        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame, text="🛡️ Fact-Checker AI",
            font=ctk.CTkFont(size=22, weight="bold"), text_color=ACCENT_CYAN
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))  # ← меньше pady

        # Инструкция
        self.instruction = ctk.CTkLabel(
            self.sidebar_frame, text="Вставьте текст новости или ссылку (http://...):",
            text_color=TEXT_SECONDARY, font=ctk.CTkFont(size=13)
        )
        self.instruction.grid(row=1, column=0, padx=20, pady=(0, 6), sticky="w")

        # Поле ввода (компактнее)
        self.text_input = ctk.CTkTextbox(
            self.sidebar_frame, height=140, fg_color=BG_MAIN,  # ← было 170
            border_color=BORDER_SUBTLE, border_width=1, text_color=TEXT_PRIMARY,
            corner_radius=8, font=ctk.CTkFont(size=14)
        )
        self.text_input.grid(row=2, column=0, padx=20, pady=6, sticky="nsew")

        # ================= БЛОК ИСТОЧНИКОВ =================
        self._build_sources_block()
        self.sources_block.grid(row=3, column=0, padx=20, pady=(0, 8), sticky="ew")

        # Кнопка вставки из буфера
        self.paste_button = ctk.CTkButton(
            self.sidebar_frame, text="📋 Вставить из буфера",
            fg_color=BG_CARD, hover_color="#252532", text_color=TEXT_PRIMARY,
            border_color=BORDER_SUBTLE, border_width=1, corner_radius=6,
            command=self.paste_from_clipboard, font=ctk.CTkFont(size=13),
            height=30  # ← было 34
        )
        self.paste_button.grid(row=4, column=0, padx=20, pady=(0, 8), sticky="ew")

        # РЕЖИМ ПАРАНОИ (с динамическими цветами)
        self.paranoia_var = ctk.StringVar(value="off")
        self.paranoia_switch = ctk.CTkSwitch(
            self.sidebar_frame, text="👁️ Режим параноика", variable=self.paranoia_var,
            onvalue="on", offvalue="off",
            fg_color="#3A3A4A",
            progress_color=ACCENT_PINK,
            button_color="#5A5A6A",
            button_hover_color="#7A7A8A",
            text_color=TEXT_DIM,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_paranoia_toggle
        )
        self.paranoia_switch.grid(row=5, column=0, padx=20, pady=(0, 10), sticky="w")

        # Кнопка анализа (компактнее)
        self.analyze_button = ctk.CTkButton(
            self.sidebar_frame, text="🔍 АНАЛИЗИРОВАТЬ",
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=ACCENT_CYAN, hover_color="#00B8CC", text_color=BG_MAIN,
            corner_radius=8, height=40, command=self.start_analysis  # ← было 45
        )
        self.analyze_button.grid(row=6, column=0, padx=20, pady=(0, 10), sticky="ew")

        # Прогресс-бар (тоньше)
        self.progress = ctk.CTkProgressBar(
            self.sidebar_frame, mode="indeterminate",
            fg_color=BG_CARD, progress_color=ACCENT_PURPLE, corner_radius=4, height=6  # ← было 8
        )
        self.progress.grid(row=7, column=0, padx=20, pady=(0, 18), sticky="ew")
        self.progress.set(0)

        # ================= ПРАВАЯ ПАНЕЛЬ =================
        self.result_frame = ctk.CTkScrollableFrame(
            self, fg_color=BG_MAIN, corner_radius=0,
            scrollbar_button_color=BG_CARD, scrollbar_button_hover_color="#252532"
        )
        self.result_frame.grid(row=0, column=1, padx=0, pady=0, sticky="nsew")
        self.result_frame.grid_columnconfigure(0, weight=1)

        self.welcome_label = ctk.CTkLabel(
            self.result_frame, text="Здесь появится результат анализа...",
            font=ctk.CTkFont(size=16), text_color=TEXT_SECONDARY
        )
        self.welcome_label.pack(pady=80)

        # Загружаем сохранённые источники
        self._load_trusted_sources()
        self._load_untrusted_sources()

    # ============================================================
    #            БЛОК ИСТОЧНИКОВ (КОМПАКТНЫЙ)
    # ============================================================
    def _build_sources_block(self):
        self.sources_block = ctk.CTkFrame(
            self.sidebar_frame, fg_color=BG_SOURCES_BLOCK,
            border_color=BORDER_SUBTLE, border_width=1, corner_radius=8
        )

        # Заголовок
        header = ctk.CTkLabel(
            self.sources_block, text="📚 Управление источниками",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT_PRIMARY, anchor="w"
        )
        header.pack(fill="x", padx=10, pady=(8, 3))

        hint = ctk.CTkLabel(
            self.sources_block,
            text="Добавьте сайт в доверенные (+) или недоверенные (✕)",
            font=ctk.CTkFont(size=11), text_color=TEXT_SECONDARY,
            anchor="w", justify="left"
        )
        hint.pack(fill="x", padx=10, pady=(0, 5))

        # Поле ввода
        self.source_entry = ctk.CTkEntry(
            self.sources_block, placeholder_text="например: bbc.com",
            fg_color=BG_MAIN, border_color=BORDER_SUBTLE, border_width=1,
            text_color=TEXT_PRIMARY, corner_radius=6, height=30,  # ← было 34
            font=ctk.CTkFont(size=12)
        )
        self.source_entry.pack(fill="x", padx=10, pady=(0, 6))

        # ДВЕ КНОПКИ СНИЗУ
        btn_row = ctk.CTkFrame(self.sources_block, fg_color="transparent")
        btn_row.pack(fill="x", padx=10, pady=(0, 6))

        self.add_trusted_btn = ctk.CTkButton(
            btn_row, text="+ Доверенный",
            fg_color=ACCENT_MINT, hover_color="#00D4B8",
            text_color=BG_MAIN, corner_radius=6, height=28,  # ← было 32
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.add_trusted_source
        )
        self.add_trusted_btn.pack(side="left", fill="x", expand=True, padx=(0, 3))

        self.add_untrusted_btn = ctk.CTkButton(
            btn_row, text="✕ Недоверенный",
            fg_color=ACCENT_PINK, hover_color="#CC0044",
            text_color="#FFFFFF", corner_radius=6, height=28,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.add_untrusted_source
        )
        self.add_untrusted_btn.pack(side="left", fill="x", expand=True, padx=(3, 0))

        # === СПИСОК ДОВЕРЕННЫХ ===
        trusted_header = ctk.CTkFrame(self.sources_block, fg_color="transparent")
        trusted_header.pack(fill="x", padx=10, pady=(0, 2))

        self.trusted_title = ctk.CTkLabel(
            trusted_header, text="✅ Доверенные (0)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ACCENT_MINT, anchor="w"
        )
        self.trusted_title.pack(side="left", fill="x", expand=True)

        self.trusted_list_frame = ctk.CTkScrollableFrame(
            self.sources_block, fg_color="transparent", height=42,  # ← было 55
            scrollbar_button_color=BG_CARD, scrollbar_button_hover_color="#252532"
        )
        self.trusted_list_frame.pack(fill="x", padx=6, pady=(0, 4))

        # Разделитель
        sep = ctk.CTkFrame(self.sources_block, fg_color=BORDER_SUBTLE, height=1)
        sep.pack(fill="x", padx=10, pady=2)

        # === СПИСОК НЕДОВЕРЕННЫХ ===
        untrusted_header = ctk.CTkFrame(self.sources_block, fg_color="transparent")
        untrusted_header.pack(fill="x", padx=10, pady=(3, 2))

        self.untrusted_title = ctk.CTkLabel(
            untrusted_header, text="🚫 Недоверенные (0)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ACCENT_PINK, anchor="w"
        )
        self.untrusted_title.pack(side="left", fill="x", expand=True)

        self.untrusted_list_frame = ctk.CTkScrollableFrame(
            self.sources_block, fg_color="transparent", height=42,  # ← было 55
            scrollbar_button_color=BG_CARD, scrollbar_button_hover_color="#252532"
        )
        self.untrusted_list_frame.pack(fill="x", padx=6, pady=(0, 6))

    # --- ДОВЕРЕННЫЕ ИСТОЧНИКИ ---
    def _normalize_source(self, raw):
        src = raw.strip().lower()
        src = re.sub(r'^https?://', '', src)
        src = re.sub(r'^www\.', '', src)
        src = src.rstrip('/')
        return src

    def add_trusted_source(self):
        raw = self.source_entry.get().strip()
        if not raw:
            return

        new_sources = [self._normalize_source(s) for s in re.split(r'[,\n;]+', raw) if s.strip()]
        added = 0
        for src in new_sources:
            if not src or src in self.trusted_sources:
                continue
            if src in self.untrusted_sources:
                self.remove_untrusted_source(src, save=False)
            self.trusted_sources.append(src)
            self._render_trusted_item(src)
            added += 1

        if added:
            self._save_trusted_sources()
            self._update_trusted_counter()
            self.source_entry.delete(0, "end")

    def _render_trusted_item(self, source):
        row = ctk.CTkFrame(
            self.trusted_list_frame, fg_color=BG_CARD, corner_radius=5, height=24
        )
        row.pack(fill="x", pady=1)

        icon = ctk.CTkLabel(
            row, text="✓", width=18,
            font=ctk.CTkFont(size=12, weight="bold"), text_color=ACCENT_MINT
        )
        icon.pack(side="left", padx=(6, 3), pady=2)

        name_label = ctk.CTkLabel(
            row, text=source, font=ctk.CTkFont(size=12),
            text_color=TEXT_PRIMARY, anchor="w"
        )
        name_label.pack(side="left", fill="x", expand=True, padx=(0, 3), pady=2)

        remove_btn = ctk.CTkButton(
            row, text="✕", width=22, height=20,
            fg_color="transparent", hover_color=ACCENT_PINK,
            text_color=TEXT_SECONDARY, corner_radius=3,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=lambda s=source: self.remove_trusted_source(s)
        )
        remove_btn.pack(side="right", padx=(0, 4), pady=2)

        self.trusted_widgets[source] = row

    def remove_trusted_source(self, source):
        if source in self.trusted_sources:
            self.trusted_sources.remove(source)
        if source in self.trusted_widgets:
            self.trusted_widgets[source].destroy()
            del self.trusted_widgets[source]
        self._save_trusted_sources()
        self._update_trusted_counter()

    def _update_trusted_counter(self):
        self.trusted_title.configure(text=f"✅ Доверенные ({len(self.trusted_sources)})")

    def _save_trusted_sources(self):
        try:
            with open(TRUSTED_SOURCES_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.trusted_sources, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Не удалось сохранить доверенные: {e}")

    def _load_trusted_sources(self):
        if not TRUSTED_SOURCES_FILE.exists():
            return
        try:
            with open(TRUSTED_SOURCES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    for src in data:
                        if isinstance(src, str) and src not in self.trusted_sources:
                            self.trusted_sources.append(src)
                            self._render_trusted_item(src)
                    self._update_trusted_counter()
        except Exception as e:
            print(f"Не удалось загрузить доверенные: {e}")

    # --- НЕДОВЕРЕННЫЕ ИСТОЧНИКИ (ОТДЕЛЬНЫЙ СПИСОК) ---
    def add_untrusted_source(self):
        raw = self.source_entry.get().strip()
        if not raw:
            return

        new_sources = [self._normalize_source(s) for s in re.split(r'[,\n;]+', raw) if s.strip()]
        added = 0
        for src in new_sources:
            if not src or src in self.untrusted_sources:
                continue
            if src in self.trusted_sources:
                self.remove_trusted_source(src)
            self.untrusted_sources.append(src)
            self._render_untrusted_item(src)
            added += 1

        if added:
            self._save_untrusted_sources()
            self._update_untrusted_counter()
            self.source_entry.delete(0, "end")

    def _render_untrusted_item(self, source):
        row = ctk.CTkFrame(
            self.untrusted_list_frame, fg_color="#1F1520", corner_radius=5, height=24
        )
        row.pack(fill="x", pady=1)

        icon = ctk.CTkLabel(
            row, text="✕", width=18,
            font=ctk.CTkFont(size=12, weight="bold"), text_color=ACCENT_PINK
        )
        icon.pack(side="left", padx=(6, 3), pady=2)

        name_label = ctk.CTkLabel(
            row, text=source, font=ctk.CTkFont(size=12),
            text_color=TEXT_PRIMARY, anchor="w"
        )
        name_label.pack(side="left", fill="x", expand=True, padx=(0, 3), pady=2)

        remove_btn = ctk.CTkButton(
            row, text="✕", width=22, height=20,
            fg_color="transparent", hover_color=ACCENT_MINT,
            text_color=TEXT_SECONDARY, corner_radius=3,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=lambda s=source: self.remove_untrusted_source(s)
        )
        remove_btn.pack(side="right", padx=(0, 4), pady=2)

        self.untrusted_widgets[source] = row

    def remove_untrusted_source(self, source, save=True):
        if source in self.untrusted_sources:
            self.untrusted_sources.remove(source)
        if source in self.untrusted_widgets:
            self.untrusted_widgets[source].destroy()
            del self.untrusted_widgets[source]
        if save:
            self._save_untrusted_sources()
            self._update_untrusted_counter()

    def _update_untrusted_counter(self):
        self.untrusted_title.configure(text=f"🚫 Недоверенные ({len(self.untrusted_sources)})")

    def _save_untrusted_sources(self):
        """Сохраняет НЕДОВЕРЕННЫЕ источники в ОТДЕЛЬНЫЙ файл."""
        try:
            with open(UNTRUSTED_SOURCES_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.untrusted_sources, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Не удалось сохранить недоверенные: {e}")

    def _load_untrusted_sources(self):
        """Загружает НЕДОВЕРЕННЫЕ источники из ОТДЕЛЬНОГО файла."""
        if not UNTRUSTED_SOURCES_FILE.exists():
            return
        try:
            with open(UNTRUSTED_SOURCES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    for src in data:
                        if isinstance(src, str) and src not in self.untrusted_sources:
                            self.untrusted_sources.append(src)
                            self._render_untrusted_item(src)
                    self._update_untrusted_counter()
        except Exception as e:
            print(f"Не удалось загрузить недоверенные: {e}")

    # --- ПРОМПТЫ ДЛЯ ИИ (недоверенные ИДУТ ПОСЛЕ доверенных) ---
    def _get_trusted_sources_prompt(self):
        if not self.trusted_sources:
            return ""
        sources_list = ", ".join(self.trusted_sources)
        return f"""
ВАЖНО: Пользователь добавил следующие источники в свой список ДОВЕРЕННЫХ:
[{sources_list}]

Если новость ссылается на один из этих источников или совпадает с их позицией — 
повышай source_trust до "высокое" и указывай это в source_reason.
"""

    def _get_untrusted_sources_prompt(self):
        """Этот промпт идёт ПОСЛЕ доверенных — как отдельный чёрный список."""
        if not self.untrusted_sources:
            return ""
        sources_list = ", ".join(self.untrusted_sources)
        return f"""
КРИТИЧНО: Пользователь добавил следующие источники в свой список НЕДОВЕРЕННЫХ (чёрный список):
[{sources_list}]

Если новость ссылается на один из этих источников ИЛИ исходит от них — 
ОБЯЗАТЕЛЬНО понижай source_trust до "низкое", повышай clickbait_score и truth_risk.
В source_reason прямо указывай: "Источник в чёрном списке пользователя".
"""

    # --- ПЕРЕКЛЮЧАТЕЛЬ ПАРАНОИ ---
    def _on_paranoia_toggle(self):
        if self.paranoia_var.get() == "on":
            self.paranoia_switch.configure(
                fg_color="#4A1A2A",
                progress_color=ACCENT_PINK,
                button_color=ACCENT_PINK,
                button_hover_color="#FF3377",
                text_color=ACCENT_PINK
            )
        else:
            self.paranoia_switch.configure(
                fg_color="#3A3A4A",
                progress_color="#5A5A6A",
                button_color="#5A5A6A",
                button_hover_color="#7A7A8A",
                text_color=TEXT_DIM
            )

    # ============================================================
    #            ОСНОВНАЯ ЛОГИКА
    # ============================================================
    def paste_from_clipboard(self):
        try:
            text = self.clipboard_get()
            self.text_input.insert("insert", text)
        except Exception:
            pass

    def copy_report(self):
        if self.current_report_text:
            self.clipboard_clear()
            self.clipboard_append(self.current_report_text)
            self.copy_button.configure(
                text="✅ Скопировано!", fg_color=ACCENT_MINT, text_color=BG_MAIN
            )
            self.after(2000, lambda: self.copy_button.configure(
                text="📋 Скопировать отчет", fg_color=BG_CARD, text_color=TEXT_PRIMARY
            ))

    def start_analysis(self):
        text = self.text_input.get("1.0", "end-1c").strip()
        if not text:
            return

        for widget in self.result_frame.winfo_children():
            widget.destroy()

        self.current_report_text = ""
        self.analyze_button.configure(state="disabled", text="⏳ Анализ...", fg_color=ACCENT_PURPLE)
        self.paste_button.configure(state="disabled")
        self.progress.start()

        is_paranoid = (self.paranoia_var.get() == "on")
        threading.Thread(
            target=self.run_llm_analysis, args=(text, is_paranoid), daemon=True
        ).start()

    def run_llm_analysis(self, text, is_paranoid):
        if text.startswith("http"):
            self.after(0, self.show_info_message, "🌐 Читаю статью по ссылке...")
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                response = requests.get(text, headers=headers, timeout=10)
                soup = BeautifulSoup(response.text, 'html.parser')
                for script in soup(["script", "style", "nav", "footer", "header"]):
                    script.decompose()
                text_content = soup.get_text(separator=' ', strip=True)
                text = text_content[:4000]
                if len(text) < 50:
                    raise Exception("Не удалось извлечь текст со страницы.")
            except Exception as e:
                self.after(0, self.display_error, f"Ошибка чтения ссылки: {e}")
                return

        try:
            # Собираем промпт: сначала доверенные, ПОТОМ недоверенные
            final_prompt = BASE_SYSTEM_PROMPT
            final_prompt += self._get_trusted_sources_prompt()
            final_prompt += self._get_untrusted_sources_prompt()

            if is_paranoid:
                final_prompt += PARANOIA_PROMPT
                self.after(0, self.show_info_message, "👁️ Режим параноика активирован. Ищу скрытые угрозы...")
            else:
                self.after(0, self.show_info_message, "🧠 Нейросеть анализирует текст...")

            response = client.chat.completions.create(
                model="openrouter/free",
                messages=[
                    {"role": "system", "content": final_prompt},
                    {"role": "user", "content": f"Проверь этот текст:\n{text}"}
                ],
                temperature=0.3 if is_paranoid else 0.2,
                timeout=60
            )

            result_text = response.choices[0].message.content
            if not result_text:
                raise Exception("Нейросеть вернула пустой ответ.")

            result_text = re.sub(r'```json|```', '', result_text).strip()
            start_idx = result_text.find('{')
            end_idx = result_text.rfind('}')

            if start_idx != -1 and end_idx != -1:
                result_text = result_text[start_idx:end_idx + 1]

            try:
                data = json.loads(result_text)
            except json.JSONDecodeError:
                raise Exception("Ошибка формата данных. Модель затупила.")

            self.after(0, self.display_results, data)

        except Exception as e:
            self.after(0, self.display_error, f"Ошибка: {str(e)}")

    def show_info_message(self, msg):
        if self.welcome_label.winfo_exists():
            self.welcome_label.configure(text=msg)

    def display_results(self, data):
        self.progress.stop()
        self.analyze_button.configure(state="normal", text="🔍 АНАЛИЗИРОВАТЬ", fg_color=ACCENT_CYAN)
        self.paste_button.configure(state="normal")

        if self.welcome_label.winfo_exists():
            self.welcome_label.destroy()

        # 1. Доверие
        trust = data.get('source_trust', 'неизвестно')
        color = ACCENT_MINT if trust == "высокое" else ACCENT_YELLOW if trust == "среднее" else ACCENT_PINK
        self.add_card(
            "📡 Доверие к источнику",
            f"Вердикт: {trust.upper()}\n{data.get('source_reason', '')}",
            color
        )
        self.current_report_text += f"📡 Доверие: {trust.upper()}\n{data.get('source_reason', '')}\n"

        # 2. Кликбейт
        cb_score = data.get('clickbait_score', 0)
        cb_color = ACCENT_MINT if cb_score < 30 else ACCENT_YELLOW if cb_score < 70 else ACCENT_PINK
        phrases = "\n".join([f"• «{p}»" for p in data.get('clickbait_phrases', [])])
        self.add_card(
            "🎣 Кликбейт и манипуляции",
            f"Индекс кликбейта: {cb_score}/100\nНайдено:\n{phrases}",
            cb_color
        )
        self.current_report_text += f"🎣 Кликбейт: {cb_score}/100\n{phrases}\n"

        # 3. Объяснение
        explanation = data.get('explanation', '')
        terms = "\n".join([f"• {t}" for t in data.get('terms', [])])
        self.add_card(
            "📖 Объяснение происходящего",
            f"{explanation}\n\nТермины:\n{terms}",
            ACCENT_CYAN
        )
        self.current_report_text += f"📖 Объяснение: {explanation}\nТермины: {terms}\n"

        # 4. Совет
        advice = data.get('action_advice', '')
        self.add_card("💡 Что делать?", advice, ACCENT_PURPLE)
        self.current_report_text += f"💡 Что делать: {advice}\n"

        # Кнопка копирования
        self.copy_button = ctk.CTkButton(
            self.result_frame, text="📋 Скопировать отчет",
            fg_color=BG_CARD, hover_color=ACCENT_CYAN, text_color=TEXT_PRIMARY,
            border_color=BORDER_SUBTLE, border_width=1, corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"), command=self.copy_report
        )
        self.copy_button.pack(pady=(15, 30), fill="x", padx=15)

    def add_card(self, title, content, color_hex):
        # Компактные карточки
        card = ctk.CTkFrame(
            self.result_frame, fg_color=BG_CARD,
            border_color=BORDER_SUBTLE, border_width=1, corner_radius=10
        )
        card.pack(fill="x", pady=10, padx=15)

        indicator = ctk.CTkFrame(card, fg_color=color_hex, width=4, corner_radius=2)
        indicator.pack(side="left", fill="y", padx=(0, 0), pady=2)

        content_frame = ctk.CTkFrame(card, fg_color="transparent")
        content_frame.pack(side="left", fill="both", expand=True, padx=14, pady=12)

        lbl_title = ctk.CTkLabel(
            content_frame, text=title,
            font=ctk.CTkFont(size=15, weight="bold"), text_color=color_hex
        )
        lbl_title.pack(anchor="w", pady=(0, 6))

        lbl_content = ctk.CTkLabel(
            content_frame, text=content, wraplength=500, justify="left",  # ← было 550
            font=ctk.CTkFont(size=13), text_color=TEXT_PRIMARY
        )
        lbl_content.pack(anchor="w")

    def display_error(self, error_msg):
        self.progress.stop()
        self.analyze_button.configure(state="normal", text="🔍 АНАЛИЗИРОВАТЬ", fg_color=ACCENT_CYAN)
        self.paste_button.configure(state="normal")

        if self.welcome_label.winfo_exists():
            self.welcome_label.destroy()

        self.add_card("⚠️ Ошибка", error_msg, ACCENT_PINK)

        retry_button = ctk.CTkButton(
            self.result_frame, text="🔄 Попробовать снова",
            fg_color=ACCENT_PINK, hover_color="#CC0044", text_color="#FFFFFF",
            corner_radius=8, font=ctk.CTkFont(size=14, weight="bold"),
            command=self.start_analysis
        )
        retry_button.pack(pady=(10, 30), fill="x", padx=15)


if __name__ == "__main__":
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    app = FactCheckApp()
    app.mainloop()
