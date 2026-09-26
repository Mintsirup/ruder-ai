"""GUI Settings Window for RuderAI using Tkinter & Directory Selector."""
import json
import sys
import urllib.request
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
from pathlib import Path
from ruder_ai.core.config import ConfigManager, DEFAULT_CONFIG


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

    root = tk.Tk()
    root.title("RuderAI - Model & System Config")
    root.geometry("520x380")
    root.resizable(False, False)

    padding = {'padx': 10, 'pady': 8}

    title_label = ttk.Label(root, text="⚙️ RuderAI Settings", font=("Arial", 14, "bold"))
    title_label.pack(**padding)

    form_frame = ttk.Frame(root)
    form_frame.pack(fill="x", **padding)

    # 1. Workspace Directory (작업할 프로젝트 경로)
    ttk.Label(form_frame, text="Workspace:").grid(row=0, column=0, sticky="w", pady=5)
    workspace_var = tk.StringVar(value=config_mgr.get("workspace_dir", str(Path.cwd())))
    workspace_entry = ttk.Entry(form_frame, textvariable=workspace_var, width=28)
    workspace_entry.grid(row=0, column=1, pady=5)

    def browse_workspace():
        selected = filedialog.askdirectory(initialdir=workspace_var.get())
        if selected:
            workspace_var.set(selected)

    browse_btn = ttk.Button(form_frame, text="📁 폴더 선택", command=browse_workspace)
    browse_btn.grid(row=0, column=2, padx=5, pady=5)

    # 2. Ollama Base URL
    ttk.Label(form_frame, text="Ollama URL:").grid(row=1, column=0, sticky="w", pady=5)
    url_var = tk.StringVar(value=config_mgr.get("ollama_base_url"))
    url_entry = ttk.Entry(form_frame, textvariable=url_var, width=28)
    url_entry.grid(row=1, column=1, pady=5)

    # 3. Model Name
    ttk.Label(form_frame, text="Model Name:").grid(row=2, column=0, sticky="w", pady=5)
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
            status_var.set("Ollama에 연결할 수 없습니다. URL을 확인하세요.")

    # Deliberately not probed at window-open time: get_installed_ollama_models
    # blocks for up to 3s on a socket timeout, which froze the whole window on
    # every launch while Ollama was down. Fetch on demand instead.
    status_var = tk.StringVar(value="")
    model_combo['values'] = [current_model]
    url_var.trace_add("write", lambda *_: status_var.set(""))

    refresh_btn = ttk.Button(form_frame, text="🔄 새로고침", command=refresh_models)
    refresh_btn.grid(row=2, column=2, padx=5, pady=5)

    # 4. Temperature
    ttk.Label(form_frame, text="Temperature:").grid(row=3, column=0, sticky="w", pady=5)
    # Default must match DEFAULT_CONFIG, otherwise the window shows 0.3 while
    # the CLI/agent run at 0.1 and saving here silently changes model behaviour.
    temp_var = tk.DoubleVar(value=float(config_mgr.get("temperature", DEFAULT_CONFIG["temperature"])))
    temp_entry = ttk.Entry(form_frame, textvariable=temp_var, width=28)
    temp_entry.grid(row=3, column=1, pady=5)

    ttk.Label(root, textvariable=status_var, foreground="#b58900").pack(**padding)

    # Save Action
    def on_save():
        try:
            ws_path = Path(workspace_var.get().strip()).resolve()
            if not ws_path.exists():
                messagebox.showerror("Error", f"존재하지 않는 디렉토리입니다:\n{ws_path}")
                return

            model = model_var.get().strip()
            if not model:
                messagebox.showerror("Error", "모델 이름을 입력하세요.")
                return

            temperature = float(temp_entry.get())
            if not 0.0 <= temperature <= 2.0:
                messagebox.showerror("Error", "Temperature 값은 0.0 ~ 2.0 사이여야 합니다.")
                return

            # update() merges; save_config() would replace the document and
            # destroy env_vars plus every other tuning key.
            config_mgr.update({
                "workspace_dir": str(ws_path),
                "ollama_base_url": url_var.get().strip(),
                "model_name": model,
                "temperature": temperature,
            })
            messagebox.showinfo("Success", f"설정이 저장되었습니다!\n작업 경로: {ws_path}")
            root.destroy()
        except ValueError:
            messagebox.showerror("Error", "Temperature 값은 숫자여야 합니다.")

    save_btn = ttk.Button(root, text="Save Configuration", command=on_save)
    save_btn.pack(pady=15)

    root.mainloop()
    return 0
