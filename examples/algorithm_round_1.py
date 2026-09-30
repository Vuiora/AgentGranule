"""Offline execution demo using explicitly supplied example facts, not model inference.

Run from the repository after installation: python examples/algorithm_round_1.py
"""

import json

from agentgranule import AlgorithmRunner, Project, Task


FACTS = {
    "advantages": ["示例：降低重复工作", "示例：明确处理约束", "示例：保留可追溯记录"],
    "disadvantages": ["示例：需要人工设置", "示例：存在接入成本", "示例：需要维护处理函数"],
}


def main():
    calls = {"advantages": 0, "disadvantages": 0, "explanation": 0}

    def enumerate_facts(request):
        direction = request["task"]["direction"]
        calls[direction] += 1
        count = request["constraints"]["item_count"]
        facts = request["context"]["facts"][direction]
        if count > len(facts):
            raise ValueError("Not enough supplied facts; ask the human for more input")
        return {"items": facts[:count]}

    def explain(request):
        calls["explanation"] += 1
        summaries = [f"{name}：{len(output['items'])} 项" for name, output in request["inputs"].items()]
        output = {"text": "；".join(summaries)}
        if request["constraints"]["detail_level"] == "detailed":
            depth = request["constraints"]["max_depth"]
            output["details"] = [{"text": summary} for summary in summaries]
            if depth is None or depth >= 2:
                for node, data in zip(output["details"], request["inputs"].values()):
                    node["children"] = [{"text": item} for item in data["items"]]
        return output

    with Project(":memory:") as project:
        session = project.create_session("第一轮算法离线演示")
        project.record_message(session, "user", "演示输入：优点两项，缺点默认，汇总详细说明，最多两层。")
        modules = {direction: project.add_module(session, direction) for direction in calls}
        project.set_granularity(modules["advantages"], actor="demo-human", direction="advantages", parameters={"count": 2})
        project.set_granularity(modules["explanation"], actor="demo-human", direction="explanation",
                                parameters={"detail_level": "detailed", "max_depth": 2})
        runner = AlgorithmRunner(project)
        runner.register("advantages", enumerate_facts, version="demo-v1")
        runner.register("disadvantages", enumerate_facts, version="demo-v1")
        runner.register("explanation", explain, version="demo-v1")
        tasks = [Task("advantages", modules["advantages"], "advantages"),
                 Task("disadvantages", modules["disadvantages"], "disadvantages"),
                 Task("summary", modules["explanation"], "explanation", ["advantages", "disadvantages"])]
        context = {"facts": FACTS}
        first = runner.run(session, tasks, context=context)
        second = runner.run(session, tasks, context=context)
        project.record_message(session, "user", "演示调整：优点改为三项。")
        project.set_granularity(modules["advantages"], actor="demo-human", direction="advantages", parameters={"count": 3})
        third = runner.run(session, tasks, context=context)
        print(json.dumps({
            "initial": {name: task["status"] for name, task in first["tasks"].items()},
            "repeat": {name: task["status"] for name, task in second["tasks"].items()},
            "changed": {name: task["status"] for name, task in third["tasks"].items()},
            "handler_calls": calls, "output": third["outputs"],
            "all_complete": all(run["complete"] for run in [first, second, third]),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
