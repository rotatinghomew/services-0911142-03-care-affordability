from pathlib import Path
import json

from .domain import explore, validate
from .scenario import affordability, compare
from .storage import Store, fingerprint


class Workbench:
    def __init__(self, root, state):
        self.root = Path(root)
        self.product = json.loads((self.root / "product.json").read_text(encoding="utf-8"))
        self.store = Store(Path(state) / "research.sqlite3")
        initial = json.loads((self.root / "fixtures" / "data.json").read_text(encoding="utf-8"))
        validate(initial)
        self.default_revision = self.store.put_dataset("演示材料", initial)

    def context(self):
        return {"product": self.product, "default_revision": self.default_revision,
                "datasets": self.store.datasets(), "notebooks": self.store.notebooks()}

    def upload(self, body):
        data = body["data"]
        try:
            validate(data)
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("字段结构不完整或类型不符") from error
        return {"revision": self.store.put_dataset(body.get("label", ""), data)}

    def _dataset(self, body):
        revision = body.get("revision", self.default_revision)
        return revision, self.store.dataset(revision)

    def inspect(self, body):
        revision = body.get("revision", self.default_revision)
        selection = body.get("selection", self.product["selection"])
        if not isinstance(selection, dict):
            raise ValueError("选择条件必须是对象")
        data = self.store.dataset(revision)
        result = explore(data, selection)
        return {"revision": revision, "selection": selection, "data_sha256": fingerprint(data), "view": result}

    def trial(self, body):
        revision, data = self._dataset(body)
        scenario = body.get("scenario")
        if not isinstance(scenario, dict):
            raise ValueError("情景参数必须是对象")
        scenario = {**self.product["selection"], **scenario}
        view = affordability(data, scenario)
        return {"revision": revision, "scenario": view["effective_scenario"],
                "data_sha256": fingerprint(data), "view": view}

    def comparison(self, body):
        revision, data = self._dataset(body)
        base = body.get("base")
        alternative = body.get("alternative")
        if not isinstance(base, dict) or not isinstance(alternative, dict):
            raise ValueError("情景对比需要 base 与 alternative 两个情景")
        base = {**self.product["selection"], **base}
        alternative = {**self.product["selection"], **alternative}
        view = compare(data, base, alternative)
        return {"revision": revision, "base": view["base_view"]["effective_scenario"],
                "alternative": view["alternative_view"]["effective_scenario"],
                "data_sha256": fingerprint(data), "view": view}

    def save(self, body):
        kind = body.get("kind", "explore")
        if kind == "explore":
            result = self.inspect(body)
            selection = result["selection"]
            request_body = None
        elif kind == "trial":
            result = self.trial(body)
            selection = result["scenario"]
            request_body = {"kind": "trial", "revision": result["revision"], "scenario": result["scenario"]}
        elif kind == "compare":
            result = self.comparison(body)
            selection = {"base": result["base"], "alternative": result["alternative"]}
            request_body = {"kind": "compare", "revision": result["revision"],
                            "base": result["base"], "alternative": result["alternative"]}
        else:
            raise ValueError("记录类型不受支持")
        saved = self.store.save(body.get("title", ""), result["revision"], selection, result,
                                kind=kind, request_body=request_body)
        return saved
