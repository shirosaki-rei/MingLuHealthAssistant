"""架构 §2.2 的依赖规则。

这几条本来是靠 import-linter 在 CI 里静态检查的。没有引 import-linter，
就用测试跑等价的断言——规则本身必须可执行，写在注释里等于没有。

新增模块时如果这里红了，去看 docs/明路_架构设计_v1.0.md §2.2。
"""
from __future__ import annotations

import ast
import pathlib

MODULES_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "modules"


def _imports_of(path: path.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
    return found


def test_safety_never_depends_on_llm_gateway():
    """红旗必须能在模型全挂时生效。safety 不得依赖 platform.llm。"""
    for module in MODULES_DIR.glob("*.py"):
        if module.name == "safety.py":
            for target in _imports_of(module):
                assert "llm" not in target, f"{module.name} 依赖了 {target}，红旗不能依赖模型"


def test_platform_does_not_know_modules():
    """基础设施保持无业务。platform 不得 import modules。"""
    platform_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "platform"
    for module in platform_dir.glob("*.py"):
        for target in _imports_of(module):
            assert "modules" not in target, f"{module.name} 依赖了 {target}"


def test_entitlement_does_not_depend_on_business_modules():
    """entitlement 被所有人调用，不能反向依赖业务模块，否则循环。"""
    for target in _imports_of(MODULES_DIR / "entitlement.py"):
        assert "modules." not in target or target.endswith("safety"), (
            f"entitlement 依赖了 {target}"
        )


def test_every_module_route_declares_an_action():
    """路由必须显式声明 action，靠启动自检兜不住 FastAPI 版本差异，
    这里再断言一次：任何 require_action / require_public_action 之外的路由函数都要被 main 的自检覆盖。"""
    from app.main import _walk_routes
    from app.main import app as fastapi_app

    actions: set[str] = set()
    for route in _walk_routes(fastapi_app.routes):
        if not route.path.startswith("/v1/"):
            continue
        found = {
            getattr(dep.call, "__minglu_action__", None)
            for dep in route.dependant.dependencies
            if hasattr(dep, "call")
        } - {None}
        assert found, f"{route.path} 没有声明 action"
        actions |= found

    from app.modules.entitlement import MEDICAL_ACTIONS
    from app.deps import PUBLIC_ACTIONS, _ACTION_CHECKS

    # 三类合法归属，缺一类都会让某条路径的付费/限额判定落空：
    #   就医路径（永远放行）/ 公开端点（无 token）/ 注册了额度或频控检查
    unclassified = actions - MEDICAL_ACTIONS - PUBLIC_ACTIONS - set(_ACTION_CHECKS)
    assert not unclassified, (
        f"这些 action 既不在就医路径白名单、也不是公开端点、还没注册检查：{unclassified}"
    )


def test_contract_action_list_matches_code():
    """契约里返回的白名单必须与代码里的 MEDICAL_ACTIONS 完全一致。

    前端按这个列表做本地预判，两边漂移会出现「本地以为免费、服务端拦了」。
    """
    import yaml

    contract = pathlib.Path(__file__).resolve().parents[3] / "contracts" / "openapi.yaml"
    spec = yaml.safe_load(contract.read_text(encoding="utf-8"))
    example = spec["paths"]["/v1/config/medical-actions"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["example"]

    from app.modules.entitlement import ACTIONS_VERSION, MEDICAL_ACTIONS

    assert set(example["actions"]) == set(MEDICAL_ACTIONS), (
        f"契约与代码不一致，只在一边：{set(example['actions']) ^ set(MEDICAL_ACTIONS)}"
    )
    assert example["version"] == ACTIONS_VERSION, (
        f"契约 version {example['version']} != 代码 ACTIONS_VERSION {ACTIONS_VERSION}"
    )
