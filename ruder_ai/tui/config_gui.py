"""GUI Settings Window for RuderAI using Tkinter & Directory Selector."""
import json
import sys
import urllib.request
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
from pathlib import Path
from ruder_ai.core.config import ConfigManager, DEFAULT_CONFIG
from ruder_ai.tui import i18n
from ruder_ai.tui.i18n import LANGUAGE_NAMES, t


def get_installed_ollama_models(base_url: str) -> list[str]:
    try:
        url = f"{base_url.rstrip('/')}/api/tags"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                return [model.get('name') for model in data.get('models', []) if model.get('name')]
    except Exception:
        pass
    return []


def open_config_gui() -> int:
    """Open the settings window. Returns a process exit code.

    Tkinter is unavailable on some headless installs (Termux, slim server
    images). Fail with a readable message and a non-zero code instead of an
    unhandled ``TclError`` traceback.
    """
    config_mgr = ConfigManager()
    i18n.set_language(config_mgr.get("ui_language", DEFAULT_CONFIG["ui_language"]))

    root = tk.Tk()
    root.title(t("settings.window_title"))
    root.geometry("520x420")
    root.resizable(False, False)

    padding = {'padx': 10, 'pady': 8}

    title_label = ttk.Label(root, text=t("settings.heading"), font=("Arial", 14, "bold"))
    title_label.pack(**padding)

    form_frame = ttk.Frame(root)
    form_frame.pack(fill="x", **padding)

    # 1. Workspace Directory (작업할 프로젝트 경로)
    workspace_label = ttk.Label(form_frame, text=t("settings.workspace"))
    workspace_label.grid(row=0, column=0, sticky="w", pady=5)
    workspace_var = tk.StringVar(value=config_mgr.get("workspace_dir", str(Path.cwd())))
    workspace_entry = ttk.Entry(form_frame, textvariable=workspace_var, width=28)
    workspace_entry.grid(row=0, column=1, pady=5)

    def browse_workspace():
        selected = filedialog.askdirectory(initialdir=workspace_var.get())
        if selected:
            workspace_var.set(selected)

    browse_btn = ttk.Button(form_frame, text=t("settings.browse"), command=browse_workspace)
    browse_btn.grid(row=0, column=2, padx=5, pady=5)

    # 2. Ollama Base URL
    ttk.Label(form_frame, text=t("settings.url")).grid(row=1, column=0, sticky="w", pady=5)
    url_var = tk.StringVar(value=config_mgr.get("ollama_base_url"))
    url_entry = ttk.Entry(form_frame, textvariable=url_var, width=28)
    url_entry.grid(row=1, column=1, pady=5)

    # 3. Model Name
    ttk.Label(form_frame, text=t("settings.model")).grid(row=2, column=0, sticky="w", pady=5)
    current_model = config_mgr.get("model_name")
    model_var = tk.StringVar(value=current_model)
    model_combo = ttk.Combobox(form_frame, textvariable=model_var, width=26)
    model_combo.grid(row=2, column=1, pady=5)

    def refresh_models():
        base_url = url_var.get().strip()
        if not base_url:
            return
        models = get_installed_ollama_models(base_url)
        if models:
            model_combo['values'] = models
            if model_var.get() not in models:
                model_combo.current(0)
        else:
            model_combo['values'] = [current_model]
            status_var.set(t("settings.ollama_unreachable"))

    # Deliberately not probed at window-open time: get_installed_ollama_models
    # blocks for up to 3s on a socket timeout, which froze the whole window on
    # every launch while Ollama was down. Fetch on demand instead.
    status_var = tk.StringVar(value="")
    model_combo['values'] = [current_model]
    url_var.trace_add("write", lambda *_: status_var.set(""))

    refresh_btn = ttk.Button(form_frame, text=t("settings.refresh"), command=refresh_models)
    refresh_btn.grid(row=2, column=2, padx=5, pady=5)

    # 4. Temperature
    ttk.Label(form_frame, text=t("settings.temperature")).grid(row=3, column=0, sticky="w", pady=5)
    # Default must match DEFAULT_CONFIG, otherwise the window shows 0.3 while
    # the CLI/agent run at 0.1 and saving here silently changes model behaviour.
    temp_var = tk.DoubleVar(value=float(config_mgr.get("temperature", DEFAULT_CONFIG["temperature"])))
    temp_entry = ttk.Entry(form_frame, textvariable=temp_var, width=28)
    temp_entry.grid(row=3, column=1, pady=5)

    # 5. UI Language — switching re-labels the window in place rather than
    # making the user save and reopen, which is the only way to see the effect.
    language_label = ttk.Label(form_frame, text=t("settings.language"))
    language_label.grid(row=4, column=0, sticky="w", pady=5)
    language_codes = [code for code, _ in i18n.language_choices()]
    language_names = [name for _, name in i18n.language_choices()]
    language_var = tk.StringVar(
        value=LANGUAGE_NAMES[i18n.get_language()],
    )
    language_combo = ttk.Combobox(
        form_frame, textvariable=language_var, width=26, state="readonly",
        values=language_names,
    )
    language_combo.grid(row=4, column=1, pady=5)

    def on_language_change(event=None):
        chosen = language_names.index(language_var.get())
        i18n.set_language(language_codes[chosen])
        apply_language()

    def apply_language():
        root.title(t("settings.window_title"))
        title_label.configure(text=t("settings.heading"))
        workspace_label.configure(text=t("settings.workspace"))
        browse_btn.configure(text=t("settings.browse"))
        language_label.configure(text=t("settings.language"))
        refresh_btn.configure(text=t("settings.refresh"))
        save_btn.configure(text=t("settings.save"))

    language_combo.bind("<<ComboboxSelected>>", on_language_change)

    ttk.Label(root, textvariable=status_var, foreground="#b58900").pack(**padding)

    # Save Action
    def on_save():
        try:
            ws_path = Path(workspace_var.get().strip()).resolve()
            if not ws_path.exists():
                messagebox.showerror(
                    t("settings.error"),
                    f"{t('settings.err_missing_dir')}\n{ws_path}",
                )
                return

            model = model_var.get().strip()
            if not model:
                messagebox.showerror(t("settings.error"), t("settings.err_no_model"))
                return

            temperature = float(temp_entry.get())
            if not 0.0 <= temperature <= 2.0:
                messagebox.showerror(t("settings.error"), t("settings.err_temp_range"))
                return

            # update() merges; save_config() would replace the document and
            # destroy env_vars plus every other tuning key.
            config_mgr.update({
                "workspace_dir": str(ws_path),
                "ollama_base_url": url_var.get().strip(),
                "model_name": model,
                "temperature": temperature,
                "ui_language": i18n.get_language(),
            })
            messagebox.showinfo(
                t("settings.success"),
                f"{t('settings.saved')}\n{t('settings.saved_workspace')} {ws_path}",
            )
            root.destroy()
        except ValueError:
            messagebox.showerror(t("settings.error"), t("settings.err_temp_number"))

    save_btn = ttk.Button(root, text=t("settings.save"), command=on_save)
    save_btn.pack(pady=15)

    root.mainloop()
    return 0
