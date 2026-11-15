import importlib
import importlib.util
import pkgutil

from app import apps


def discover_apps() -> list:
    """apps配下のroutesを読み込み、登録と一覧に同じ情報を使う。"""
    modules = []
    for entry in sorted(pkgutil.iter_modules(apps.__path__), key=lambda item: item.name):
        if entry.ispkg and importlib.util.find_spec(f"app.apps.{entry.name}.routes") is not None:
            modules.append(importlib.import_module(f"app.apps.{entry.name}.routes"))
    return modules


def app_entries(modules: list) -> list[dict]:
    return [
        {"title": getattr(module, "APP_TITLE", module.__package__.rsplit(".", 1)[-1]),
         "description": getattr(module, "APP_DESCRIPTION", ""),
         "url": module.router.prefix + "/"}
        for module in modules
    ]
