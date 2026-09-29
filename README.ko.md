# RUDER-AI

**언어:** [English](README.md) · **[한국어](README.ko.md)** · [日本語](README.ja.md) · [简体中文](README.zh.md)

**RUDER-AI**는 자연어로 주어진 작업을 실제 검증 가능한 코드 변경으로 만들어내는, 역할 분리형 자율 코딩 에이전트입니다.

RUDER-AI는 LLM의 추론과 결정론적 Tool 실행, 역할 단위 권한 부여, 독립적인 검증, 다국어 모델 설정을 결합합니다.

## 요구 사항

* Python 3.10 이상
* 로컬에서 실행 중인 [Ollama](https://ollama.com) (기본값 `http://127.0.0.1:11434`)
* `tkinter` — Windows/macOS의 CPython에는 기본 포함. Linux에서 GUI가 필요하면
  `python3-tk`를 설치하세요. 터미널 에이전트와 테스트 스위트는 사용하지 않습니다.

## 설치

```bash
git clone https://github.com/Mintsirup/ruder-ai.git
cd ruder-ai
python -m pip install -e .
```

이 과정으로 `ruder-ai` CLI가 설치됩니다. 대화형 에이전트는 다음과 같이 실행합니다.

```bash
ruder-ai start --dir /path/to/your/project --model ruder-ai-ko
```

### 명령어

| 명령어 | 동작 |
|---|---|
| `ruder-ai start` | 대상 프로젝트에서 대화형 에이전트 실행 |
| `ruder-ai gui` | 데스크톱 에디터 + AI 콘솔 실행 |
| `ruder-ai where` | 실제로 실행 중인 체크아웃을 출력하고, 다른 복사본의 코드가 다르면 경고 |
| `ruder-ai survey` | 프로젝트의 모든 파일을 역할별로 분류해 설명 (LLM 호출 없음) |
| `ruder-ai bench` | 주요 구간 측정: scan, index, plan, context, 편집 반영 |
| `ruder-ai config` | 설정 창 열기 |

## 데스크톱 GUI

RuderAI Studio는 Code-OSS 스타일의 데스크톱 앱입니다 — 파일 탐색기, 멀티 탭 에디터,
AI 콘솔, 내장 터미널을 모두 Python 표준 라이브러리의 `tkinter`만으로 구현했습니다.
설치할 GUI 바이너리가 없으므로 `pip install -e .` 외에 필요한 것은 없습니다.

```bash
ruder-ai gui      # RuderAI Studio 실행
ruder-ai config   # 설정 창: 작업공간, Ollama URL, 모델, temperature
```

주의 사항:

* 모든 실행 — CLI, GUI 채팅, `.ruder_ai_logs/`의 JSONL 실행 로그 — 은 명시적인
  `End Of Token` 마커로 종료되므로, 대화 기록에서 "에이전트가 끝났다"와 "스트림이
  도중에 끊겼다"를 항상 구분할 수 있습니다. 로그에서는 `end_of_token` 이벤트입니다.
* 자식 프로세스에서 캡처한 출력(`execute_code`, `execute_shell`, `git`, 검증 러너)은
  자식 측에서 UTF-8로 강제됩니다. Windows에서 파이프된 프로세스는 stdout을 ANSI 코드
  페이지로 인코딩하므로, 이것이 없으면 에이전트가 방금 쓴 한국어 출력을 mojibake로
  읽고 자신이 작성한 코드가 깨졌다고 결론 내립니다.
* `tkinter`는 Windows와 macOS의 CPython에 포함됩니다. 일부 슬림 Linux 이미지에는
  `python3-tk`가 필요합니다. 헤드리스 환경(서버, Termux)에는 디스플레이가 없어
  GUI를 실행할 수 없습니다 — 그런 환경에서는 `ruder-ai start`를 사용하세요.
* 내장 터미널은 **실제 TTY**에 연결됩니다: Linux/macOS에서는 PTY, Windows에서는
  Win32 **ConPTY** 의사 콘솔을 사용하므로 `vim`, `htop`, 언어 REPL이 정상 동작합니다.
  호스트가 ConPTY API를 노출하지만 콘솔 호스트 할당을 거부하는 경우(컨테이너,
  비대화형 세션, 일부 CI 러너)에는 Studio가 파이프 셸로 강등하고 그 사실을
  터미널에 알려줍니다 — 일반 명령은 여전히 동작하고, 전체 화면 프로그램은 동작하지 않습니다.
* `gui`와 `config`는 터미널 에이전트와 동일한 `.ruder_ai_config.json`을 읽고
  쓰므로, 모델이나 호스트 변경이 모든 곳에 적용됩니다.
* 에디터는 Python, C 계열(JS/TS, C#, Java, C/C++, Go, Rust, Kotlin, Swift, PHP, Lua),
  JSON, YAML, 셸, CSS, 마크다운, XML/HTML, Ruby에 대해 구문 강조를 지원합니다.
  하이라이팅은 디바운스되며, 버퍼가 매우 크거나 한 줄이 지나치게 길면 지연 없이
  강조 없이 렌더링합니다.
* 찾기/바꾸기: `Ctrl+F` / `Ctrl+H`, 반복은 `F3`와 `Shift+F3`, 패널을 닫거나 선택을
  해제하려면 `Esc`. 옵션은 대소문자 구분, 전체 단어, 정규식, 그리고 정확히 일치하는
  항목이 없을 때 유사 후보를 제안하는 "오타 허용" 폴백입니다. 잘못된 패턴은 예외를
  던지지 않고 보고하며, 상태 표시줄에 `Ln/Col`이 나타납니다.
* 전체 단어 일치는 정규식 `\b`가 아니라 유니코드 인식 방식으로 동작하므로 한국어/CJK와
  `//` 같은 문장부호로 이루어진 검색어에서도 정확합니다. 정규식 검색은 여러 줄에
  걸쳐 적용되므로 `^import`는 `import`로 시작하는 모든 줄과 일치합니다.

## 개발

테스트 스위트는 저장소 루트에서 실행합니다:

```bash
python -m pip install pytest pytest-asyncio
python -m pytest
```

`tests/conftest.py`가 기본적으로 `RUDER_AI_BENCHMARK_FIXTURE_MODE`를 켜는데, 이는
실제 모델을 호출하는 대신 오케스트레이터의 결정론적 벤치마크 답을 선택하는 모드입니다.
변수를 직접 설정해 덮어쓸 수도 있습니다(예: `RUDER_AI_BENCHMARK_FIXTURE_MODE=0`으로
라이브 LLM 코드 경로를 실행).

## 번역

이 README는 영어, 일본어, 중국어(간체)으로도 제공됩니다. 각 번역본은 이 문서를
섹션 단위로 대응시키며, 하나가 오래되면 영어 문서가 기준입니다.

| 언어 | 파일 |
|---|---|
| English | [README.md](README.md) |
| 한국어 | [README.ko.md](README.ko.md) |
| 日本語 | [README.ja.md](README.ja.md) |
| 简体中文 | [README.zh.md](README.zh.md) |

## 기능

* 자율 코딩 워크플로우
* 역할 분리 에이전트 파이프라인
* 결정론적 Tool 권한 강제
* 턴 인텐트 게이팅 — 인사말이 파일 시스템까지 도달하지 않음
* 커버리지가 보고되는 완전한 프로젝트 서베이와 개요
* 프로젝트에 대한 독립적 검증
* 실제 파일과 diff 대조에 의한 주장 검증
* 프로젝트 인지 코드 생성
* 재계획 및 복구
* 웹 검색과 정보 검색
* 최종 작업 결과에 대한 메모리
* 다국어 모델 설정
* 한국어, 중국어, 영어, 일본어 지원
* 모든 작업이 지불하는 구간을 재는 내장 벤치마크(`ruder-ai bench`)

---

## v7.5.9 에이전트 파이프라인

```text
Explorer → Planner → Coder → Tester → Reviewer → Memory
                 \_______________________________/
                           Orchestrator
```

RUDER-AI는 코딩 워크플로우를 전문화된 단계로 분리합니다.

### Explorer

읽기 전용 프로젝트 탐색.

* 프로젝트 구조 점검
* 파일 검색
* 심볼과 참조 확인
* 프로젝트 언어와 프레임워크 식별
* 파일을 변경하지 않음

### Planner

구현 계획을 수립합니다.

* 요청된 작업 분석
* 필요한 변경 사항 결정
* 관련 파일과 컴포넌트 식별
* 구현 전에 구조화된 계획 산출

### Coder

계획된 변경을 적용합니다.

* 파일 생성 및 수정
* 패치 적용
* 허용된 개발 Tool 실행
* 계획된 솔루션 구현

검증은 의도적으로 Tester 단계로 미뤄집니다.

### Tester

구현을 독립적으로 검증합니다.

* `verify_project` 실행
* 프로젝트 상태 점검
* 구현이 실제로 동작하는지 확인
* 파일을 변경하지 않음

따라서 Coder는 자신이 한 작업의 성공 여부에 대한 최종 권한자가 아닙니다.

### Reviewer

현실에 비추어 결과를 검토합니다.

* 변경된 파일 점검
* diff 점검
* 구현 주장 확인
* 보고된 변경과 실제 변경의 불일치 식별
* 읽기 전용

### Memory

최종 결과를 기록합니다.

* 최종 단계 결과 저장
* 중요한 결정 기록
* 향후 맥락을 위해 작업 결과 보존
* Tool 접근 없음

### Orchestrator

전체 파이프라인을 조율합니다.

Orchestrator는 단계 흐름을 제어하지만 **Tool 권한을 부여하지 않습니다**.

Tool 인가는 `ruder_ai/agents/permissions.py`의 결정론적 역할 정책
(`ROLE_TOOL_POLICIES`)을 사용해 `ToolExecutor`가 강제합니다.
Executor가 모든 Tool 호출에 대해 권위 있는 런타임 검사를 수행합니다.

---

## Tool 권한 강제

RUDER-AI는 프롬프트 지시를 인가 경계로 취급하지 않습니다.

모든 Tool 호출은 `ToolExecutor`를 거치며, 실행 전에 활성 역할을 검사합니다.

```text
Role Agent
    │
    ▼
Tool Call
    │
    ▼
ToolExecutor
    │
    ├── Allowed → Execute Tool
    └── Denied  → permission error
```

역할 에이전트는 자신의 명시적 역할을 Executor에 전달합니다.

역할이 허용되지 않은 Tool을 사용하려 하면 `ToolExecutor`는 결정론적인 `permission`
오류를 반환합니다.

### 이중 레이어 보호

Coder는 시스템 프롬프트를 통해 허용된 Tool만 전달받습니다.

그러나 프롬프트는 최종 보안 경계로 간주되지 않습니다.

Executor가 런타임에서 권위 있는 권한 검사를 수행합니다.

```text
LLM Tool visibility
        +
Runtime Tool authorization
        =
Role-enforced Tool access
```

하위 호환성을 위해 `role=None`을 쓰는 기존 `ToolExecutor` 직접 호출자는 제한 없이
그대로 동작합니다.

---

## 검증 모델

RUDER-AI는 구현과 검증을 의도적으로 분리합니다.

다음 방식 대신:

```text
LLM → Modify → "Done"
```

의도된 워크플로우는:

```text
Plan
  ↓
Modify
  ↓
Verify
  ↓
Review
  ↓
Record
```

따라서 시스템은 Coder의 "작업이 성공했다"는 주장만 신뢰하는 대신, 독립적으로
관찰된 프로젝트 상태를 기준으로 완료를 판단하려 합니다.

---

## 언어 모델

RUDER-AI는 다음 기반의 언어별 모델 설정을 제공합니다:

```text
qwen2.5-coder:7b-instruct
```

현재 설정은 다음과 같습니다:

| 모델          | 언어      | 용도              |
| ------------- | --------- | ----------------- |
| `ruder-ai-ko` | 한국어      | Korean RUDER-AI   |
| `ruder-ai-cn` | 简体中文     | Chinese RUDER-AI  |
| `ruder-ai-en` | English  | English RUDER-AI  |
| `ruder-ai-jp` | 日本語      | Japanese RUDER-AI |

각 모델은 동일한 RUDER-AI Code Mode 아키텍처를 사용하면서 설정된 응답 언어를 강제합니다.

기술 코드, 식별자, 클래스 이름, 메서드 이름, 원본 언어의 소스 코멘트는 해당 언어 그대로 유지됩니다.

자연어 설명, 추론, 지시는 설정된 언어로 생성됩니다.

### 모델 설정

`Modelfiles/`의 Ollama Modelfile이 모델 수준에서 샘플링을 설정합니다:

```text
Temperature: 0.7
Context window: 16384
Base model: qwen2.5-coder:7b-instruct
```

Python 런타임(`RuderAISettings`)은 에이전트 실행을 위해 자체 결정론적 기본값을
사용하고 요청 수준 샘플링 옵션을 덮어씁니다:

```text
Temperature: 0.1
Context window: 16384
Max tokens: 3072
```

따라서 실제 런타임 샘플링은 `RuderAISettings`를 따릅니다(`RUDER_AI_TEMPERATURE` 등의
환경 변수나 `ruder-ai config`로 설정 가능). Modelfile 값은 명시적 요청 옵션 없이
모델이 호출되는 경우에만 적용됩니다.

이 모델들의 용도:

* RUDER-AI CODE MODE
* `READ_ONLY_INSPECT`

일상적인 대화는 별도로 처리되며 이 Code Mode 설정을 거치지 않습니다.

---

## 언어별 모델 파일

모델 파일 구조 예시:

```text
Modelfiles/
├── ruder-ai-ko
├── ruder-ai-cn
├── ruder-ai-en
└── ruder-ai-jp
```

Ollama로 모델을 생성합니다 (`-f` 경로는 이 저장소 루트 기준 상대 경로입니다):

```bash
ollama create ruder-ai-ko -f Modelfiles/ruder-ai-ko
ollama create ruder-ai-cn -f Modelfiles/ruder-ai-cn
ollama create ruder-ai-en -f Modelfiles/ruder-ai-en
ollama create ruder-ai-jp -f Modelfiles/ruder-ai-jp
```

언어별 모델 실행:

```bash
ollama run ruder-ai-ko
```

```bash
ollama run ruder-ai-en
```

```bash
ollama run ruder-ai-jp
```

```bash
ollama run ruder-ai-cn
```

---

## 프로젝트 인지 코딩

RUDER-AI는 특정 프로그래밍 생태계를 가정하지 않습니다.

변경하기 전에 실제 프로젝트를 조사하고 다음을 파악할 것으로 기대합니다:

* 프로그래밍 언어
* 빌드 시스템
* 프레임워크
* 프로젝트 구조
* 기존 규칙
* 설정 및 매니페스트 형식
* 관련 API와 의존성

에이전트는 무관한 아키텍처를 새로 만들어내는 대신 프로젝트에 이미 확립된 규칙을 따라야 합니다.

다음과 같은 설정 및 매니페스트 파일에 대해:

```text
package.json
pom.xml
plugin.yml
Cargo.toml
```

RUDER-AI는 대상 프로젝트에 이미 확립된 규칙을 따릅니다.

---

## 인덱스 성능

인덱싱은 모든 작업 전에 일어나는 일이라 가장 빠르게 만들어야 하는 부분이었습니다.
이 저장소에서 측정(192개 파일, 1,300개 심볼, Windows, CPython 3.13):

| 작업 | 이전 | 이후 |
|---|---|---|
| 파일 1개 편집(`refresh_file`) | 302 ms | 3.7 ms |
| 전체 인덱스 재구축, warm | 294 ms | 39 ms |
| `ProjectScanner.scan` | 115 ms | 10 ms |
| `FileResolver.resolve_many` | 114 ms | 5 ms |
| 컨텍스트 조립 | 17 ms | 10 ms |
| 파일시스템 시그니처(요청당) | 9.6 ms | 4 ms |

무엇을 바꾸었고, 각각 왜 안전한가:

* **증분 인덱싱이 실제로 동작합니다.** `AIAgent.refresh_file`이
  `SymbolIndexer.update_file`을 호출했는데 이 메서드가 존재하지 않았습니다. 모든
  "증분" 갱신이 `AttributeError`를 일으켰고, 호출자는 이를 삼킨 뒤
  `project_index = None`으로 설정했습니다 — 즉 파일 1개 편집이 조용히 전체
  워크스페이스 재스캔과 재파싱을 강제했습니다. `SymbolIndexer.update_file`과
  `ReferenceIndex.update_file`이 이제 존재하며 전체 재구축과 바이트 단위로 동일한
  상태에 도달합니다(편집, 삭제, 문법 오류, Java 파일을 포함해 검증).
* **참조 인덱스는 심볼 이름이 이동할 때만 재구축됩니다.** 전역 이름 집합을 기준으로
  하므로 함수 본문만 바꾸는 편집은 파일 하나에 적용할 수 있고, 정의를 추가하거나
  제거하면 전체를 다시 도출합니다.
* **무시 대상 디렉터리는 순회 중에 가지치기됩니다.** `node_modules`, `.venv`,
  `build/` 등은 건너뛰고, 규칙은 워크스페이스 *내부* 경로에 매칭됩니다 — `out` 같은
  디렉터리 밑에 체크아웃이 있는 경우 더 이상 빈 인덱스가 나오지 않습니다.
* **파생 데이터는 파일별로 캐시됩니다.** 키는 `(path, size, mtime_ns)` — 워크스페이스
  시그니처가 재구축 여부를 판단할 때 쓰던 것과 동일한 삼중항이라, 캐시가 인덱스보다
  오래될 수 없습니다. 전체 빌드는 파일을 세 번이 아니라 한 번 읽고, 변경 없는
  파일은 토큰화도 재파싱도 하지 않습니다.
* **토큰화와 점수화는 Python 레벨 문자 단위 작업을 피합니다.** 토큰 추정기의 한글
  카운팅은 C 레벨 정규식 한 번의 통과이며 ASCII는 빠른 경로를 탑니다; CamelCase
  분할은 쪼갤 수 없는 토큰은 건너뛰고, 대소문자 무시 타입 조회는 미리 계산한
  소문자 이름을 사용합니다.

동치성 증명은 `tests/test_v755_performance.py`에 있습니다 — AST 순회는 이 저장소의
모든 모듈에 대해 `ast.walk`와 비교되고, 토크나이저는 수천 개의 무작위 입력에 대해
이전 구현과 비교되며, 증분 인덱서는 전체 재구축과 비교됩니다.

같은 수치로 언제든 다시 측정할 수 있습니다:

```bash
ruder-ai bench --dir . --reps 3
```

각 단계는 측정 전에 워밍업되고 best-of-N으로 보고되며, 편집 반영 측정은 버림용
복사본에서 실행되어 작업 트리는 건드리지 않습니다.

---

## 턴 인텐트

인사말은 작업이 아닙니다. `안녕` 한마디가 planner에 도달해 `hello_handler` 모듈의
`write_file`을 만들어냈습니다 — 누군가 안녕을 한 것만으로 에이전트가 저장소를
수정했습니다. planner는 이미 검색용 `intent`를 계산했지만, 변경 전에 그것을
확인하는 곳이 없었으므로 아무것도 요청하지 않은 턴에 대해 모델이 원하는 계획을
세울 수 있었습니다.

`ruder_ai/core/turn_intent.py`는 모든 턴을 세 가지 종류 중 하나로 분류합니다:

| 종류 | 예시 | 파일 변경 가능 |
|---|---|---|
| `CONVERSATIONAL` | `안녕`, `hi`, `thanks!` | 아니오 |
| `QUESTION` | `이게 왜 이렇게 짜여 있어?`, `which module handles login?` | 아니오 |
| `ACTION` | `auth.py 고쳐줘`, `Add a test for login` | 예 |

이 결정은 planner가 아니라 **executor에서 강제**됩니다. 같은 모델이 계획을 쓰기
때문입니다 — 계획을 지어낸 모델이라면 자기 변명도 지어낼 수 있습니다.
`ToolExecutor._check_turn_intent`는 `write_file`, `append_file`, `patch_file`,
`delete_file`, `move_file`, `apply_patch` 중 하나를 실행하기 직전에 동작하고,
변경을 허용하는 표현을 알려주며 `MUTATION_REFUSED_MESSAGE`으로 거부합니다. 읽기
전용 Tool은 계속 사용 가능합니다 — 프로젝트에 대한 질문은 파일을 열어 볼 정당한
이유지만, 그를 바꿀 이유는 아닙니다.

분류기는 의도적으로 거부 쪽으로 기울어 있습니다. 대화용으로 오독된 턴은 더 명확한
표현으로 재시도 한 번의 비용이고, 작업용으로 오독된 턴은 조용히 코드를 편집합니다.
라틴어 인사말은 단어 경계로 매칭하므로 `this`/`which`/`him`이 `hi`로 읽히지 않습니다.

`AIAgent.process_task`는 `CONVERSATIONAL`을 Tool도 인덱스도 없는 단일 LLM 호출로
단락 처리하므로, 인사말은 "변경된 파일 없음"을 만들어내는 전체
Explorer→Planner→Coder→Tester→Reviewer 통과 대신 왕복 한 번의 비용만 냅니다.

---

## 프로젝트 서베이

"이 프로젝트 일일히 분석해서 파일마다 기능 일일히 말해줘"라는 요청이 **190개 파일 중
2개**에 대한 답을 냈고, 출력 어디에도 나머지 188개가 건너뛰어졌다는 표시가 없었습니다.
문제는 둘이었습니다: 190개 파일을 프롬프트에 담을 방법이 없었고, 커버리지가
보고되지 않아 부분적인 답이 완전한 답처럼 보였습니다.

서베이는 프로젝트 인덱스로부터 만들어지므로 구조적으로 완전하고 모델 호출이 없습니다:

```bash
ruder-ai survey --dir .
```

```
- 파일: **199/199**개 (1480 KiB) (100%)

## CLI 엔트리포인트 (1개)

### `ruder_ai/main.py`
- 226줄 / 8,975 bytes / .py
- 기능: RUDER-AI CLI Main Entrypoint.
- 주요 심볼: `run_agent_loop`, `start`, `bench`, `where`, `survey`, `gui`, `config`
```

각 파일은 자신의 docstring과 AST로부터 설명됩니다(LLM이 추측하는 내용이 아니라
무엇을 *위해* 존재하는지), 디렉터리와 파일명과 패키지 구조에서 유도한 역할별로
묶입니다. `covered/total` 줄은 장식이 아닙니다: 인덱스에는 있지만 읽을 수 없는
파일은 분모에 포함되고 출력에 이름이 표시되므로, 이 숫자가 조용히 "우리가 확인한
분량"을 가리킬 수 없습니다.

### 하나의 답이 아니라 두 개의 등급

프로젝트를 *분석*해달라는 요청과 *모든 파일*을 설명해달라는 요청은 서로 다른
요청이며, 둘 다 48,000자짜리 덤프로 답하는 것은 답하지 않는 것만큼이나 도움이
되지 않습니다. 둘은 별개 경로로 나뉩니다:

| 요청 | 답 |
|---|---|
| "모든 프로젝트를 분석해", "파일마다 기능 일일히 말해줘", "explain every file" | 전체 서베이, 파일마다 한 섹션 |
| "이 프로젝트가 뭐 하는 곳이야?", "analyze this codebase" | 한 화면 개요: 규모, 최상위 구조, 역할 히스토그램, 진입점, 가장 큰 모듈 — 그리고 전체 서베이로 가는 요청을 알려줌 |

둘 다 결정론적이고 LLM 호출이 없습니다. 어느 쪽이든 분석 결과는 얻지만, 어느 것도
파일 *변경*에 대한 질문에는 답하지 않으며 — 그것이 읽기 전용 요청이 예전에는
넘어떨어져 대신 받는 것이었습니다.

### 벽을 다시 읽지 않고 이어가기

전체 서베이는 약 48,000자입니다. 채팅 창에서 연속 두 번 제공되는 것은 답이 아니라
스크롤이므로 서베이는 **한 번만** 전달됩니다: 같은 요청이 오면 개요와 더 좁아가는
방법을 안내하고, 전체를 명시적으로 요청해야 다시 제공합니다. 이름을 명시한 요청은
해당 대상만 받습니다:

| 이렇게 물어보면 | 이렇게 답합니다 |
|---|---|
| `ruder_ai/core/executor.py 는 어떤 일을 해?` | 그 파일 전체 상세 |
| `executor.py 자세히 설명해줘` | basename으로 같은 파일 |
| `테스트 파일만 설명해줘` | 테스트 파일 63개 목록 |
| `core 디렉터리 자세히` | `core/` 아래 25개 파일 |
| `없는파일.py 설명해줘` | 개요와 정직한 "찾지 못함" |

이름이 명시된 파일, 역할, 디렉터리는 개요보다 우선하고, 편집 요청은 둘 다보다
우선합니다 — `auth.py 고쳐줘`는 질문이 아니라 작업이고, 이를 변경이 되지 않도록
막는 것이 executor의 턴 인텐트 게이트입니다.

한국어는 조사 정렬 후 매칭합니다. 자연어에는 조사가 명사와 동사 사이에 붙습니다 —
`프로젝트` + `를` + ` ` + `분석해` — 그래서 조사 없는 표식과 매칭하면 한 글자
어긋나고 "모든 프로젝트를 분석해"는 라우팅할 수 없었습니다. 철자 변형을 나열하는
대신 매칭 전에 조사를 제거하며, 이 정렬기는 라우팅에만 쓰이고 인사말 분류기와는
의도적으로 공유하지 않습니다 — 거기서는 `하이`가 정말 `하이`로 남아야 합니다.

---

## 출처와 복사본 불일치

한 번의 작업 세션 전체가 여기서 사라졌습니다. 편집은 한 RUDER-AI 체크아웃에
들어갔는데 editable 설치로 인해 에이전트가 *다른* 사본을 import했고, 그래서 모든
변경이 효과가 없는 것처럼 보였습니다 — 그리고 아무 것도 경고하지 않았습니다.
에이전트는 잘 돌아갔습니다. 그저 잘못된 코드를 실행한 것이었습니다.

```bash
ruder-ai where
```

```
RUDER-AI 실행 위치 : E:\etc\ruder-ai\Ruder-AI
import 경로       : E:\etc\ruder-ai\Ruder-AI\ruder_ai
현재 작업 디렉터리 : E:\etc\ruder-ai\workspace
버전              : 7.5.9-survey-followups
⚠️  경고: 실행 중인 코드와 작업 디렉터리가 서로 다른 복사본입니다.
```

`start`와 `gui`도 시작할 때 같은 헤더를 출력합니다. 현재 프로세스를 넘어서
`~/.ruder_ai/provenance.json`이 이 머신에서 본 모든 체크아웃과 신원 파일 여섯 개
(`VERSION`, `main.py`, `executor.py`, `agent.py`, `scanner.py`, `app_gui.py`)의
해시를 기억합니다. 두 번째 복사본에서 실행하면 어떤 파일이 다른지 알려주고,
사라진 복사본은 조용히 제거합니다 — 오래되었지만 건드리지 않은 사본은 소음을
만들지 않습니다.

보고되는 버전은 패키징 버전(`0.1.0`)이나 `AIAgent.__version__`이 아니라 체크아웃의
`VERSION` 파일 내용입니다 — README와 Modelfile이 참조하는 문자열이므로 사용자가
인용할 값이기 때문입니다.

성능 작업용으로 두 번째 체크아웃을 유지한다면, `sync_perf_clone.py`가 절대 삭제하지
않으면서 소스 파일을 복사합니다:

```bash
python sync_perf_clone.py . ..\workspace --dry   # 미리보기
python sync_perf_clone.py . ..\workspace
```

---

## Code Mode

RUDER-AI Code Mode는 다양한 언어와 프레임워크의 소프트웨어 엔지니어링 작업을
위해 설계되었습니다.

대표적인 작업:

* 기존 코드베이스 이해
* 관련 파일 찾기
* 구현 변경 계획
* 새 코드 작성
* 기존 코드 패치
* 프로젝트 명령 실행
* 오류 진단
* 빌드와 테스트 검증
* 변경 검토
* 실패한 구현 시도의 복구

시스템은 특정 생태계로 제한되지 않습니다.

---

## 읽기 전용 검사

`READ_ONLY_INSPECT`는 비파괴 모드입니다.

사용자 요청에 코딩 작업이 포함되어 있어도 이 단계에서 파일을 변경해서는 안 됩니다.

대신 다음을 수행해야 합니다:

1. 프로젝트 검사
2. 요청된 변경 분석
3. 관련 파일과 코드 식별
4. 조사 결과 반환

파일 변경은 적절한 Tool 권한을 가진 단계에 reserves됩니다.

---

## 웹 검색 기반 확인

`web_search`와 같은 정보 검색 Tool이 사용되면, 그 결과가 응답의 권위 있는 출처가 됩니다.

모델은:

* 최종 응답을 검색 결과에 근거해야 하며
* 충돌하는 기억 정보를 도입해서는 안 되며
* 결과에 없는 사실을 만들어내서는 안 되며
* 검색 결과에 요청한 답이 없을 때 그 사실을 명시해야 합니다

이는 문서, 버전, API, 가격, 날짜처럼 변동하는 정보에 대한 근거 없는 주장을 줄이기
위함입니다.

---

## 아키텍처

높은 수준에서:

```text
                    ┌──────────────┐
                    │     User     │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │ Orchestrator │
                    └──────┬───────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
    Explorer            Planner             Coder
        │                  │                  │
        │                  │                  ▼
        │                  │             ToolExecutor
        │                  │                  │
        │                  │                  ▼
        │                  │              Project
        │                  │                  │
        └──────────────────┼──────────────────┘
                           ▼
                        Tester
                           │
                           ▼
                       Reviewer
                           │
                           ▼
                        Memory
```

LLM이 추론과 의사 결정을 담당하고, Tool 계층이 실제 워크스페이스 작업을 수행합니다.

---

## 설계 원칙

### 결정론적 실행

LLM 출력은 작업이 일어났다는 증거로 취급되지 않습니다.

Tool 작업은 실제로 성공적으로 실행되어야 에이전트가 완료했다고 주장할 수 있습니다.

### 책임 분리

탐색, 계획, 구현, 테스트, 검토, 메모리가 서로 다른 단계로 분리됩니다.

### 독립적 검증

구현을 수행한 구성요소가 구현의 성공 여부를 결정하는 유일한 구성요소일 수 없습니다.

### 명시적 인가

Tool 권한은 자연어 지시에만 의존하지 않고 executor가 강제합니다.

### 프로젝트 인지

에이전트는 대상 프로젝트의 생태계나 규칙을 추측하기 전에 실제로 조사합니다.

### 다국어 지원

동일한 RUDER-AI 코딩 아키텍처를 한국어, 중국어, 영어, 일본어 전용 모델 설정으로
노출할 수 있습니다.

---

## 상태

**현재 파이프라인:** `v7.5.9`

**지원 모델 설정:**

```text
ruder-ai-ko
ruder-ai-cn
ruder-ai-en
ruder-ai-jp
```

RUDER-AI는 LLM 추론과 결정론적 Tool 실행, 명시적 권한, 독립적 검증, 다국어 지원을
결합한 실용적인 자율 코딩 시스템을 목표로 하는 진행 중인 프로젝트입니다.
