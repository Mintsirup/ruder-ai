"""Skills registry module."""
from typing import Dict, Type
from ruder_ai.skills.base import BaseSkill
from ruder_ai.skills.file_ops import (
    ReadFileSkill,
    WriteFileSkill,
    PatchFileSkill,
    AppendFileSkill,
    DeleteFileSkill,
    MoveFileSkill,
    CreateDirectorySkill,
    PreviewPatchSkill,
    BackupFileSkill,
    RestoreBackupSkill,
)
from ruder_ai.skills.code_exec import ExecuteCodeSkill, ExecuteShellSkill
from ruder_ai.skills.web_search import WebSearchSkill
from ruder_ai.skills.web_fetch import WebFetchSkill
from ruder_ai.skills.git_ops import (
    GitSkill,
    GitStatusSkill,
    GitDiffSkill,
    GitCheckoutSkill,
)
from ruder_ai.skills.system_info import ListDirectorySkill, EnvironmentInfoSkill
from ruder_ai.skills.verify import VerifyProjectSkill
from ruder_ai.skills.patch_ops import (
    GenerateDiffSkill,
    ApplyPatchSkill,
    RollbackPatchSkill,
)
from ruder_ai.skills.search_ops import (
    SearchSymbolSkill,
    SearchReferenceSkill,
    SemanticSearchSkill,
)


class SkillRegistry:
    """스킬 동적 등록 및 관리 레지스트리"""
    def __init__(self):
        self._skills: Dict[str, BaseSkill] = {}
        self._register_default_skills()

    def register(self, skill_cls: Type[BaseSkill]):
        instance = skill_cls()
        self._skills[instance.name] = instance

    def get_skill(self, name: str) -> BaseSkill:
        return self._skills.get(name)

    def list_skills(self) -> Dict[str, str]:
        return {name: skill.description for name, skill in self._skills.items()}

    def _register_default_skills(self):
        # 1. 파일 스킬
        self.register(ReadFileSkill)
        self.register(WriteFileSkill)
        self.register(PatchFileSkill)
        self.register(AppendFileSkill)
        self.register(DeleteFileSkill)
        self.register(MoveFileSkill)
        self.register(CreateDirectorySkill)
        self.register(PreviewPatchSkill)
        self.register(BackupFileSkill)
        self.register(RestoreBackupSkill)
        # 2. 실행 & 검색 스킬
        self.register(ExecuteCodeSkill)
        self.register(ExecuteShellSkill)
        self.register(WebSearchSkill)
        self.register(WebFetchSkill)
        # 3. Git & 시스템 스킬
        self.register(GitSkill)
        self.register(GitStatusSkill)
        self.register(GitDiffSkill)
        self.register(GitCheckoutSkill)
        self.register(ListDirectorySkill)
        self.register(EnvironmentInfoSkill)
        # 4. Auto Verify 스킬
        self.register(VerifyProjectSkill)
        # 5. Patch Generator 스킬 (unified diff 생성 + git apply)
        self.register(GenerateDiffSkill)
        self.register(ApplyPatchSkill)
        self.register(RollbackPatchSkill)
        # 6. Search 스킬 (심볼 정의/참조/시맨틱 파일 검색)
        self.register(SearchSymbolSkill)
        self.register(SearchReferenceSkill)
        self.register(SemanticSearchSkill)
