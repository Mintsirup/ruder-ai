"""Symbol / Reference / Semantic Search skills for RuderAI Agent.

인덱스(`ProjectIndex.symbols`, `ReferenceIndex`, `SemanticFileIndex`)는
전부 이미 `core/agent.py::AIAgent`가 만들어 두고 있었지만, Planner가
직접 호출할 수 있는 Tool로는 하나도 노출돼 있지 않아 "어디에 정의돼
있는지", "어디서 쓰이는지"를 물어보는 요청에도 매번 list_directory +
read_file로 파일을 하나씩 훑는 식으로만 대응할 수 있었다. 이 세 Skill은
그 인덱스를 그대로 읽기 전용으로 노출한다.

인덱스는 `ToolExecutor._execute_tool`이 `execute()` 시그니처에 있는
`project_index`/`reference_index`/`semantic_file_index` 파라미터를 보고
`self.agent`에서 자동으로 주입해준다 (`workspace_path`/`project` 자동
주입과 동일한 패턴 — executor.py 참고). 그래서 여기 Skill들은 Plan/Task
kwargs로 검색어만 받으면 되고, 인덱스 객체 자체를 어떻게 구하는지는
신경 쓸 필요가 없다.
"""

from __future__ import annotations

from typing import Any, Optional

from ruder_ai.skills.base import BaseSkill


class SearchSymbolSkill(BaseSkill):
    name = "search_symbol"
    description = (
        "이름으로 클래스/함수/메서드 등 심볼이 어느 파일 몇 번째 줄에 "
        "정의돼 있는지 찾습니다. name(필수, 부분 일치)과 kind(선택, "
        "예: 'class', 'method', 'function')를 받습니다."
    )

    async def execute(
        self,
        name: Optional[str] = None,
        symbol: Optional[str] = None,
        kind: Optional[str] = None,
        limit: int = 20,
        project_index: Any = None,
        **kwargs,
    ) -> dict[str, Any]:

        query = name or symbol

        if not query:
            return {"status": "error", "message": "name이 필요합니다."}

        if project_index is None:
            return {
                "status": "error",
                "message": "프로젝트 인덱스가 아직 없습니다 (프로젝트가 "
                "스캔되지 않았을 수 있습니다).",
            }

        query_lower = query.lower()

        matches = []

        symbols = getattr(project_index, "symbols", [])
        if isinstance(symbols, int) or symbols is None:
            symbols = []
        for sym in symbols:

            if query_lower not in sym.name.lower():
                continue

            if kind and sym.kind != kind:
                continue

            matches.append(
                {
                    "name": sym.name,
                    "kind": sym.kind,
                    "file": sym.file,
                    "line": sym.line,
                    "package": sym.package,
                }
            )

        # 정확히 일치하는 이름을 먼저, 그다음 이름 길이가 짧은(더
        # 구체적인) 순으로 정렬해 상위 결과의 관련성을 높인다.
        matches.sort(
            key=lambda m: (m["name"].lower() != query_lower, len(m["name"]))
        )

        matches = matches[:limit]

        return {
            "status": "success",
            "count": len(matches),
            "matches": matches,
        }


class SearchReferenceSkill(BaseSkill):
    name = "search_reference"
    description = (
        "심볼(클래스/함수/메서드) 이름이 프로젝트 어디에서 사용(참조)되는지 "
        "찾습니다. symbol(필수, 정확한 이름)을 받습니다."
    )

    async def execute(
        self,
        symbol: Optional[str] = None,
        name: Optional[str] = None,
        limit: int = 30,
        reference_index: Any = None,
        **kwargs,
    ) -> dict[str, Any]:

        target = symbol or name

        if not target:
            return {"status": "error", "message": "symbol이 필요합니다."}

        if reference_index is None:
            return {
                "status": "error",
                "message": "참조 인덱스가 아직 없습니다 (프로젝트가 "
                "스캔되지 않았을 수 있습니다).",
            }

        refs = reference_index.find_references(target, limit=limit)

        if not refs:
            return {
                "status": "success",
                "count": 0,
                "references": [],
                "message": f"'{target}'에 대한 참조를 찾지 못했습니다.",
            }

        return {
            "status": "success",
            "count": len(refs),
            "references": [
                {"file": file, "line": line} for file, line in refs
            ],
        }


class SemanticSearchSkill(BaseSkill):
    name = "semantic_search"
    description = (
        "정확한 파일명/심볼명을 몰라도 자연어/키워드 설명으로 관련 "
        "파일을 찾습니다 (예: 'nether star 폭발 데미지 처리'). "
        "query(필수)를 받습니다."
    )

    async def execute(
        self,
        query: Optional[str] = None,
        limit: int = 10,
        semantic_file_index: Any = None,
        **kwargs,
    ) -> dict[str, Any]:

        if not query:
            return {"status": "error", "message": "query가 필요합니다."}

        if semantic_file_index is None:
            return {
                "status": "error",
                "message": "시맨틱 인덱스가 아직 없습니다 (프로젝트가 "
                "스캔되지 않았을 수 있습니다).",
            }

        # SemanticFileIndex.search()는 이미 만들어진 query token 집합을
        # 받는다. 토큰화 자체는 SemanticEngine이 하는데, 개별 심볼
        # 이름뿐 아니라 여러 단어로 된 문장도 동일하게 토큰화한다
        # (SemanticCache.build -> SemanticEngine.expand ->
        # SemanticTokenizer.tokenize, tests/... 참고 불필요, engine.py가
        # 문자열 하나를 토큰 여러 개로 나눠준다).
        from ruder_ai.semantic.cache import SemanticCache

        tokens = SemanticCache().build(query)

        results = semantic_file_index.search(tokens)

        top = list(results.items())[:limit]

        if not top:
            return {
                "status": "success",
                "count": 0,
                "results": [],
                "message": f"'{query}'와 관련된 파일을 찾지 못했습니다.",
            }

        return {
            "status": "success",
            "count": len(top),
            "results": [
                {"file": file, "score": score} for file, score in top
            ],
        }
