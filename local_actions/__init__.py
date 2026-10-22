from typing import Any, Dict, Optional
from local_actions.base import Action, ActionError
from local_actions.system import TimeNowAction, SystemVersionsAction, SystemMemoryAction, SystemProcessesAction
from local_actions.filesystem import StorageSummaryAction, FilesystemSearchAction, FilesystemReadTextAction
from local_actions.project import ProjectInspectAction, ProjectSearchAction


class ActionEngine:
    """Registry and executor for all registered local deterministic actions."""

    def __init__(self):
        self.actions: Dict[str, Action] = {}
        self.register(TimeNowAction())
        self.register(SystemVersionsAction())
        self.register(SystemMemoryAction())
        self.register(SystemProcessesAction())
        self.register(StorageSummaryAction())
        self.register(FilesystemSearchAction())
        self.register(FilesystemReadTextAction())
        self.register(ProjectInspectAction())
        self.register(ProjectSearchAction())

    def register(self, action_instance: Action) -> None:
        self.actions[action_instance.name] = action_instance

    def execute(self, action_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute action by name with arguments dict."""
        if action_name not in self.actions:
            return {
                "action": action_name,
                "status": "error",
                "error": f"Unknown action: '{action_name}'. Available actions: {list(self.actions.keys())}",
                "data": None,
            }
        
        args = arguments or {}
        action = self.actions[action_name]
        return action.run(args)


_engine_instance: Optional[ActionEngine] = None


def get_action_engine() -> ActionEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = ActionEngine()
    return _engine_instance
