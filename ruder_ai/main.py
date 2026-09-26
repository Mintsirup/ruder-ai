"""RUDER-AI CLI Main Entrypoint."""
import asyncio
import os
import sys
from pathlib import Path
import typer
from rich.console import Console
from ruder_ai.core.agent import AIAgent
from ruder_ai.core.config import ConfigManager, settings_from_config
from ruder_ai.core.telemetry import END_OF_TOKEN

#: Rendered dim so it reads as a terminator rather than as agent output.
END_OF_TOKEN_RICH = f"[dim]{END_OF_TOKEN}[/dim]"


def _force_utf8_console() -> None:
    """Make stdout/stderr able to render the Korean UI text on Windows.

    A Korean Windows console defaults to cp949, which cannot encode glyphs the
    help text legitimately uses (em dash, box drawing, emoji).  Without this,
    ``ruder-ai --help`` dies with UnicodeEncodeError before printing anything.
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:  # pragma: no cover - no console attached
            pass

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - exotic stream replacement
            pass

    # Our own streams are fixed above, but every *child* process still picks
    # the ANSI code page for its piped stdout, which is how Korean output from
    # execute_code came back as mojibake. Exporting the same setting makes the
    # whole tree UTF-8, including children we do not spawn ourselves (the
    # model running `mvn test`, for instance).
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")


_force_utf8_console()

app = typer.Typer(help="RUDER-AI - Autonomous AI Software Engineering Engine")
console = Console()

async def run_agent_loop(override_model: str = None, override_dir: str = None):
    config_mgr = ConfigManager()

    active_model = override_model or config_mgr.get("model_name")
    target_dir = str(Path(override_dir or config_mgr.get("workspace_dir", ".")).resolve())

    if not Path(target_dir).exists():
        console.print(f"[bold red]❌ 디렉토리가 존재하지 않습니다: {target_dir}[/bold red]")
        return

    settings = settings_from_config(config_mgr)
    # --model on the command line overrides the persisted value.
    if override_model:
        settings.model = active_model
    agent = AIAgent(workspace_path=target_dir, settings=settings)

    console.print(f"[bold green]🚀 RUDER-AI Agent Running[/bold green]")
    console.print(f"[cyan]📁 Workspace:[/cyan] [bold white]{target_dir}[/bold white]")
    console.print(f"[cyan]🤖 Model:[/cyan] [bold white]{active_model}[/bold white]")
    console.print("[dim]종료하려면 'exit' 또는 'quit'을 입력하세요.[/dim]\n")

    while True:
        try:
            user_input = console.input("[bold blue]User > [/bold blue]")
        except (EOFError, KeyboardInterrupt):
            # Ctrl+C / Ctrl+Z used to tear down the whole REPL with a traceback.
            console.print("\n[dim]종료합니다.[/dim]")
            break

        if user_input.strip().lower() in {"exit", "quit", ":q"}:
            break

        if not user_input.strip():
            continue

        try:
            response = await agent.process_task(user_input)
        except KeyboardInterrupt:
            console.print("\n[yellow]⚠️  작업이 중단되었습니다.[/yellow]")
            console.print(END_OF_TOKEN_RICH)
            continue
        except Exception as e:
            # Keep the session alive: one failed task must not drop the
            # conversation context the agent has already built up.
            console.print(f"\n[bold red]❌ 처리 중 오류가 발생했습니다: {e}[/bold red]")
            console.print(END_OF_TOKEN_RICH)
            continue

        console.print(f"\n[bold green]RUDER-AI >[/bold green]\n{response}\n")
        console.print(END_OF_TOKEN_RICH)

@app.command()
def start(
    model: str = typer.Option(None, "--model", "-m", help="Ollama 모델명 지정"),
    dir: str = typer.Option(None, "--dir", "-d", help="작업을 수행할 프로젝트 경로 지정")
):
    """터미널 인터랙티브 모드로 RUDER-AI Agent를 시작합니다."""
    _report_provenance()
    asyncio.run(run_agent_loop(override_model=model, override_dir=dir))


def _report_provenance() -> None:
    """Print where this process actually runs, and warn on a split checkout."""
    try:
        from ruder_ai.core.provenance import detect, detect_divergence, write_origin
    except Exception:  # pragma: no cover - provenance must never block startup
        return
    try:
        info = detect()
        console.print(f"[dim]{info.describe()}[/dim]")
        for warning in detect_divergence():
            console.print(f"[bold yellow]⚠️  {warning}[/bold yellow]")
        write_origin(info.workspace or None)
    except Exception:  # pragma: no cover
        pass


@app.command()
def bench(
    dir: str = typer.Option(None, "--dir", "-d", help="측정할 프로젝트 경로 (기본: 현재 디렉터리)"),
    reps: int = typer.Option(3, "--reps", "-r", min=1, max=20, help="각 구간 반복 횟수"),
):
    """인덱싱/계획/컨텍스트/편집 반영 속도를 측정합니다."""
    from ruder_ai.core.bench import format_report, run

    target = Path(dir).resolve() if dir else Path.cwd()
    console.print(f"[dim]측정 중: {target}[/dim]")
    try:
        result = run(target, reps=reps)
    except Exception as exc:
        console.print(f"[bold red]측정 실패: {exc}[/bold red]")
        raise typer.Exit(code=1)
    console.print(format_report(result))


@app.command()
def where():
    """지금 실행 중인 RUDER-AI가 어느 체크아웃인지, 그리고 복사본 불일치를 확인합니다."""
    from ruder_ai.core.provenance import detect, detect_divergence

    info = detect()
    console.print(info.describe())
    warnings = detect_divergence()
    if not warnings:
        console.print("[green]복사본 불일치 없음[/green]")
    for warning in warnings:
        console.print(f"[bold yellow]⚠️  {warning}[/bold yellow]")


@app.command()
def survey(
    dir: str = typer.Option(None, "--dir", "-d", help="분석할 프로젝트 경로 (기본: 현재 디렉터리)"),
):
    """프로젝트의 모든 파일을 역할별로 분류해 한 줄씩 설명합니다 (LLM 호출 없음)."""
    from ruder_ai.core.survey import build_survey, format_survey
    from ruder_ai.indexer.detector import ProjectDetector
    from ruder_ai.indexer.scanner import ProjectScanner
    from ruder_ai.indexer.symbol_indexer import SymbolIndexer

    root = Path(dir).resolve() if dir else Path.cwd()
    scanner = ProjectScanner(root)
    files = scanner.scan()
    index = SymbolIndexer().build(
        root, files, ProjectDetector().detect(root, files), dict(scanner.inverted_index)
    )
    result = build_survey(index)
    console.print(format_survey(result))
    console.print(
        f"[dim]설명된 파일 {result.covered}/{result.total}개"
        + (f" (판독 불가 {len(result.unreadable)}개)" if result.unreadable else "")
        + "[/dim]"
    )

@app.command()
def gui():
    """Code-OSS 스타일의 Desktop GUI 에디터 및 AI 콘솔 스튜디오를 띄웁니다."""
    _report_provenance()
    try:
        from ruder_ai.tui.app_gui import launch_vscode_gui
    except ImportError as e:
        console.print(f"[bold red]❌ GUI를 실행할 수 없습니다: {e}[/bold red]")
        console.print("[cyan]안내:[/cyan] tkinter가 필요합니다. (Termux 등에서는 사용 불가)")
        raise typer.Exit(code=1)
    try:
        launch_vscode_gui()
    except Exception as e:
        console.print(f"[bold red]❌ GUI를 실행할 수 없습니다: {e}[/bold red]")
        raise typer.Exit(code=1)

@app.command()
def config():
    """GUI 설정 창을 띄워 작업 디렉토리, 모델, 시스템 설정을 변경합니다.
    (tkinter가 필요 — Termux 등 헤드리스 환경에서는 사용 불가.
    ruder_ai start --model ... --dir ... 로 직접 지정하세요.)
    """
    try:
        from ruder_ai.tui.config_gui import open_config_gui
    except ImportError as e:
        console.print(f"[bold red]❌ tkinter를 사용할 수 없습니다: {e}[/bold red]")
        console.print("[cyan]안내:[/cyan] ruder_ai start --model ... --dir ... 로 직접 지정하세요.")
        raise typer.Exit(code=1)
    try:
        open_config_gui()
    except Exception as e:
        console.print(f"[bold red]❌ 설정 창을 열 수 없습니다: {e}[/bold red]")
        raise typer.Exit(code=1)

if __name__ == "__main__":
    app()
