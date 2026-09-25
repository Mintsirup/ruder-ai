"""RUDER-AI CLI Main Entrypoint."""
import asyncio
from pathlib import Path
import typer
from rich.console import Console
from ruder_ai.core.agent import AIAgent
from ruder_ai.core.config import ConfigManager
from ruder_ai.core.settings import RuderAISettings

app = typer.Typer(help="RUDER-AI - Autonomous AI Software Engineering Engine")
console = Console()

async def run_agent_loop(override_model: str = None, override_dir: str = None):
    config_mgr = ConfigManager()

    active_model = override_model or config_mgr.get("model_name")
    target_dir = str(Path(override_dir or config_mgr.get("workspace_dir", ".")).resolve())

    if not Path(target_dir).exists():
        console.print(f"[bold red]❌ 디렉토리가 존재하지 않습니다: {target_dir}[/bold red]")
        return

    settings = RuderAISettings(
        model=active_model,
        ollama_host=str(config_mgr.get("ollama_base_url", "http://127.0.0.1:11434")),
        temperature=float(config_mgr.get("temperature", 0.1)),
        num_ctx=int(config_mgr.get("num_ctx", 16384)),
        max_tokens=int(config_mgr.get("max_tokens", 3072)),
        timeout=float(config_mgr.get("timeout", 300.0)),
        context_token_budget=int(config_mgr.get("context_token_budget", 4352)),
        max_steps=int(config_mgr.get("max_steps", 5)),
        max_replans=int(config_mgr.get("max_replans", 2)),
        max_reflections=int(config_mgr.get("max_reflections", 2)),
        enable_reflection=bool(config_mgr.get("enable_reflection", True)),
    )
    agent = AIAgent(workspace_path=target_dir, settings=settings)

    console.print(f"[bold green]🚀 RUDER-AI Agent Running[/bold green]")
    console.print(f"[cyan]📁 Workspace:[/cyan] [bold white]{target_dir}[/bold white]")
    console.print(f"[cyan]🤖 Model:[/cyan] [bold white]{active_model}[/bold white]")
    console.print("[dim]종료하려면 'exit' 또는 'quit'을 입력하세요.[/dim]\n")

    while True:
        user_input = console.input("[bold blue]User > [/bold blue]")
        if user_input.lower() in ["exit", "quit"]:
            break

        response = await agent.process_task(user_input)
        console.print(f"\n[bold green]RUDER-AI >[/bold green]\n{response}\n")

@app.command()
def start(
    model: str = typer.Option(None, "--model", "-m", help="Ollama 모델명 지정"),
    dir: str = typer.Option(None, "--dir", "-d", help="작업을 수행할 프로젝트 경로 지정")
):
    """터미널 인터랙티브 모드로 RUDER-AI Agent를 시작합니다."""
    asyncio.run(run_agent_loop(override_model=model, override_dir=dir))

@app.command()
def gui():
    """Code-OSS 스타일의 Desktop GUI 에디터 및 AI 콘솔 스튜디오를 띄웁니다."""
    from ruder_ai.tui.app_gui import launch_vscode_gui
    launch_vscode_gui()

@app.command()
def config():
    """GUI 설정 창을 띄워 작업 디렉토리, 모델, 시스템 설정을 변경합니다.
    (PyQt6/tkinter가 필요 — Termux 등 헤드리스 환경에서는 사용 불가.
    ruder_ai start --model ... --dir ... 로 직접 지정하세요.)
    """
    from ruder_ai.tui.config_gui import open_config_gui
    open_config_gui()

if __name__ == "__main__":
    app()
