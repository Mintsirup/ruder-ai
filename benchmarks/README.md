# RuderBench

이 벤치마크는 별도 가짜 Bridge를 실행하지 않습니다. **현재 RUDER-AI 소스의 실제 `AIAgent.process_task()` 경로**를 직접 호출합니다.

## 실행

```powershell
cd C:\path\to\RUDER-AI
python benchmarks\run_benchmark.py --model ruder_ai-agent:latest
```

특정 테스트만:

```powershell
python benchmarks\run_benchmark.py --model ruder_ai-agent:latest --case MP-01 --case MP-08
```

작업 디렉터리는 `benchmarks/results/workspace-<timestamp>/` 아래에 케이스별로 따로 생성됩니다. 기본적으로 벤치마크 종료 후 이 임시 작업공간은 삭제되고, 결과 JSON만 `benchmarks/results/benchmark-<timestamp>.json`에 남습니다.

`--keep-workspaces`를 쓰면 실제로 RUDER-AI가 만든 결과 파일과 `.ruder_ai_logs/executions-*.jsonl`까지 남겨서 사후 분석할 수 있습니다.

## 측정 대상

- 실제 모델 호출: `OllamaClient.chat()`
- 실제 Agent 경로: `AIAgent.process_task()`
- 실제 역할 파이프라인 및 ToolExecutor
- 실제 JSONL 실행 로그
- 실제 변경 파일 SHA-256 비교
- `AIAgent.executor.last_execution_result`의 task/verification/metrics

## 케이스

| ID | 목적 |
|---|---|
| MP-01 | 빈 fixture에서 프로젝트 생성 |
| MP-02 | Python 작업 후 Java 전환 및 활성 프로젝트 범위 유지 |
| MP-03 | MP3/OGG 요구사항 반영 |
| MP-04 | seek/timeline 요구사항 반영 |
| MP-05 | 실제 검증 결과 기록 여부 |
| MP-06 | 존재하지 않는 심볼을 임의로 만들지 않음 |
| MP-07 | 명시된 Java 파일만 실제 수정 |
| MP-08 | `execute_code`와 `execute_shell`을 혼동하지 않음 |


### 1.1 변경점
- 빈 프로젝트와 기존 음악 재생기 프로젝트 fixture를 분리했습니다.
- README/TODO 같은 참조 문서를 생성하도록 모델이 유도되지 않게 했습니다.
- 비-Git workspace에서 git_status를 무조건 호출하지 않도록 했습니다.
- 이미 적용된 patch 뒤의 stale preview_patch는 false failure가 되지 않도록 처리합니다.
- 명시적인 `python --version`/`pip --version` 셸 요청은 execute_shell 경로로 강제합니다.
