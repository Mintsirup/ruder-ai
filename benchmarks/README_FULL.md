# RuderBench Full Suite 2.0

64개 케이스의 장시간 통합 benchmark. 실제 `AIAgent.process_task()`를 사용한다.

## 범주
- G-01~08: 프로젝트 생성
- E-09~18: 파일/기능 수정
- D-19~28: 디버깅/오류 복구
- R-29~38: 리팩터링
- V-39~46: 검증/실패 정직성
- T-47~56: Tool routing / venv / shell / recovery
- S-57~64: scope / state / multi-file consistency

## 실행
`python benchmarks\run_benchmark.py --bench-file benchmarks\ruderbench_full.json --model ruder_ai-agent:latest`

한 케이스만: `--case D-26`
여러 케이스: `--case D-26 --case T-56`
workspace 유지: `--keep-workspaces`

벤치 fixture 중 `python_buggy`는 의도적으로 실패 상태에서 시작하므로 프로젝트 전체 테스트 수집 대상이 아니다. 루트 pytest는 `pytest.ini`의 `testpaths=tests`로 실제 RUDER-AI 테스트만 수집하도록 설정되어 있다.
