from __future__ import annotations

import argparse
import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Iterable

from .coding import coding_case
from .models import to_dict
from .pipeline import PlannerPipeline
from .server import serve


def _json(value: Any) -> None:
    print(json.dumps(to_dict(value), ensure_ascii=False, indent=2))


def _root_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=".", help="项目根目录，默认为当前目录")


def _pipeline(args: argparse.Namespace) -> PlannerPipeline:
    return PlannerPipeline(Path(args.root))


def _load_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON 文件必须是对象")
    return payload


def _reference_data(path: Path) -> tuple[bytes, str]:
    data = path.read_bytes()
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return data, mime_type


def _reference_data_uri(path: Path) -> str:
    data, mime_type = _reference_data(path)
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def _add_reference_commands(parent: argparse._SubParsersAction) -> None:
    parser = parent.add_parser("reference", help="管理本地参考资料")
    actions = parser.add_subparsers(dest="reference_action", required=True)

    action = actions.add_parser("list", help="列出参考资料")
    _root_parser(action)
    action = actions.add_parser("upload", help="上传参考文件")
    _root_parser(action)
    action.add_argument("paths", nargs="+", type=Path)
    action = actions.add_parser("get", help="读取参考资料元数据")
    _root_parser(action)
    action.add_argument("reference_id")
    action.add_argument("--output", type=Path, help="同时导出原文件")
    action = actions.add_parser("delete", help="删除参考资料")
    _root_parser(action)
    action.add_argument("reference_id")
    action = actions.add_parser("context", help="提取参考资料上下文")
    _root_parser(action)
    action.add_argument("reference_ids", nargs="+")


def _add_document_commands(parent: argparse._SubParsersAction) -> None:
    parser = parent.add_parser("document", help="管理策划案文档库")
    actions = parser.add_subparsers(dest="document_action", required=True)

    action = actions.add_parser("list", help="列出文档")
    _root_parser(action)
    action = actions.add_parser("search", help="搜索文档")
    _root_parser(action)
    action.add_argument("query")
    action.add_argument("--limit", type=int, default=20)
    action = actions.add_parser("get", help="读取文档")
    _root_parser(action)
    action.add_argument("document_id")
    action.add_argument("--version", type=int)
    action = actions.add_parser("update", help="创建新的文档版本")
    _root_parser(action)
    action.add_argument("document_id")
    action.add_argument("--title")
    action.add_argument("--activity-type")
    content = action.add_mutually_exclusive_group(required=True)
    content.add_argument("--content")
    content.add_argument("--content-file", type=Path)
    action.add_argument("--expected-version", type=int)
    action = actions.add_parser("delete", help="把文档移入回收目录")
    _root_parser(action)
    action.add_argument("document_id")
    action.add_argument("--expected-version", type=int)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI 策划案管线的无网页 Agent 入口")
    sub = parser.add_subparsers(dest="command", required=True)

    command = sub.add_parser("run", help="运行一次本地策划案流程（兼容入口）")
    command.add_argument("request", help="自然语言需求")
    _root_parser(command)

    command = sub.add_parser("intake", help="只做意图路由和澄清判断")
    command.add_argument("request")
    command.add_argument("--existing-document", type=Path)
    _root_parser(command)

    command = sub.add_parser("generate", help="生成完整策划案")
    command.add_argument("request")
    command.add_argument("--answer", dest="answers", action="append", default=[])
    command.add_argument("--reference-id", dest="reference_ids", action="append", default=[])
    command.add_argument("--existing-document", type=Path)
    _root_parser(command)

    command = sub.add_parser("revise", help="局部修改已有策划案")
    command.add_argument("run_id")
    command.add_argument("instruction")
    _root_parser(command)

    command = sub.add_parser("resume", help="继续执行失败或待澄清的运行")
    command.add_argument("run_id")
    _root_parser(command)

    _add_reference_commands(sub)
    _add_document_commands(sub)

    command = sub.add_parser("visual", help="读取运行中的页面任务和跳转关系")
    command.add_argument("run_id")
    _root_parser(command)

    command = sub.add_parser("image", help="为页面任务生成或修改图片")
    command.add_argument("run_id")
    command.add_argument("task_id")
    command.add_argument("--prompt", default="")
    command.add_argument("--size")
    command.add_argument("--supplement-prompt", default="")
    command.add_argument("--reference-image", type=Path)
    command.add_argument("--reference-image-name", default="")
    _root_parser(command)

    command = sub.add_parser("coding", help="从策划案生成 Coding 案")
    command.add_argument("run_id")
    command.add_argument("--output", type=Path)
    _root_parser(command)

    command = sub.add_parser("search", help="搜索联网参考")
    command.add_argument("query")
    command.add_argument("--limit", type=int)
    _root_parser(command)

    command = sub.add_parser("knowledge", help="搜索本地知识库")
    command.add_argument("query", nargs="?", default="")
    command.add_argument("--limit", type=int, default=20)
    _root_parser(command)

    command = sub.add_parser("config", help="读取或更新模型配置")
    action = command.add_subparsers(dest="config_action", required=True)
    show = action.add_parser("show")
    _root_parser(show)
    update = action.add_parser("update")
    _root_parser(update)
    update.add_argument("file", type=Path)

    command = sub.add_parser("serve", help="启动本地工作台")
    _root_parser(command)
    command.add_argument("--host", default="127.0.0.1")
    command.add_argument("--port", default=8765, type=int)

    return parser


def _run_reference(args: argparse.Namespace, pipeline: PlannerPipeline) -> None:
    library = pipeline.reference_library
    if args.reference_action == "list":
        _json(library.list())
    elif args.reference_action == "upload":
        _json([library.upload(path.name, *_reference_data(path)) for path in args.paths])
    elif args.reference_action == "get":
        metadata = library.get(args.reference_id)
        if args.output:
            _, data = library.read_file(args.reference_id)
            args.output.write_bytes(data)
            metadata = dict(metadata)
            metadata["output"] = str(args.output)
        _json(metadata)
    elif args.reference_action == "delete":
        _json(library.delete(args.reference_id))
    elif args.reference_action == "context":
        _json(library.context_segments(args.reference_ids))


def _run_document(args: argparse.Namespace, pipeline: PlannerPipeline) -> None:
    library = pipeline.store.document_library
    if args.document_action == "list":
        _json(library.list())
    elif args.document_action == "search":
        _json(library.search(args.query, limit=args.limit))
    elif args.document_action == "get":
        _json(library.get(args.document_id, version=args.version))
    elif args.document_action == "update":
        current = library.get(args.document_id)
        content = args.content
        if args.content_file:
            content = args.content_file.read_text(encoding="utf-8")
        _json(library.update(
            args.document_id,
            title=args.title if args.title is not None else current["title"],
            activity_type=args.activity_type if args.activity_type is not None else current["activity_type"],
            content=content,
            expected_version=args.expected_version,
        ))
    elif args.document_action == "delete":
        _json(library.delete(args.document_id, expected_version=args.expected_version))


def main(argv: Iterable[str] | None = None) -> None:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "serve":
        serve(Path(args.root), args.host, args.port)
        return

    pipeline = _pipeline(args)
    if args.command in {"run", "generate"}:
        existing_document = _load_json(str(args.existing_document)) if args.existing_document else None
        run = pipeline.run(
            args.request,
            answers=getattr(args, "answers", []),
            existing_document=existing_document,
            reference_ids=getattr(args, "reference_ids", []),
        )
        _json(run)
    elif args.command == "intake":
        existing_document = _load_json(str(args.existing_document)) if args.existing_document else None
        _json(pipeline.create_run(args.request, existing_document=existing_document))
    elif args.command == "revise":
        _json(pipeline.revise(args.run_id, args.instruction))
    elif args.command == "resume":
        _json(pipeline.continue_run(args.run_id))
    elif args.command == "reference":
        _run_reference(args, pipeline)
    elif args.command == "document":
        _run_document(args, pipeline)
    elif args.command == "visual":
        run = pipeline.store.load(args.run_id)
        _json({"run_id": run.id, "visual_tasks": run.visual_tasks, "flow_edges": run.flow_edges})
    elif args.command == "image":
        reference = _reference_data_uri(args.reference_image) if args.reference_image else None
        _json(pipeline.generate_image(
            args.run_id,
            args.task_id,
            prompt=args.prompt,
            size=args.size,
            supplement_prompt=args.supplement_prompt,
            reference_image=reference,
            reference_image_name=args.reference_image_name or (args.reference_image.name if args.reference_image else ""),
        ))
    elif args.command == "coding":
        content = coding_case(pipeline.store.load(args.run_id))
        if args.output:
            args.output.write_text(content, encoding="utf-8")
            _json({"run_id": args.run_id, "output": str(args.output), "characters": len(content)})
        else:
            print(content)
    elif args.command == "search":
        _json(pipeline.search_references(args.query, limit=args.limit))
    elif args.command == "knowledge":
        items = pipeline.knowledge.items
        if args.query:
            needle = args.query.lower()
            items = [item for item in items if needle in f"{item.title} {item.content} {' '.join(item.tags)}".lower()]
        _json([item.__dict__ for item in items[:args.limit]])
    elif args.command == "config":
        if args.config_action == "show":
            _json(pipeline.get_model_config())
        else:
            _json(pipeline.update_model_config(_load_json(str(args.file))))


if __name__ == "__main__":
    main()
