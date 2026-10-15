"""Headless recipe inspection, Camera RAW settings and full-resolution exports."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
import threading

from app_common.cli_encoding import configure_utf8_output
from app_common.image_output_paths import ImageOutputOptions, allocate_outputs
from app_common.image_processing_types import ImageProcessingCancelled, check_cancelled
from .model import Recipe
from .storage import RecipeStore


def read_bounded(path, limit=2*1024*1024):
    with Path(path).open("rb") as stream:
        data = stream.read(limit+1)
    if len(data)>limit:
        raise ValueError("参数文件过大")
    return data


def main(argv=None):
    configure_utf8_output()
    parser = argparse.ArgumentParser(prog="python -m app_common.photo_edit",
        description="Camera RAW / 调色；SDR sRGB 非破坏编辑，原照片不写回")
    commands = parser.add_subparsers(dest="command",required=True)
    commands.add_parser("info",help="原生引擎版本和支持的参数")
    show = commands.add_parser("show",help="读取照片 XMP 中的编辑状态")
    show.add_argument("source")
    auto = commands.add_parser("auto-adjust", help="自动调节当前底图，将可微调的色阶保存到照片 XMP")
    auto.add_argument("source")
    auto.add_argument("--mode", choices=("tone", "contrast", "color"), default="tone")
    render = commands.add_parser("render",help="导出完整分辨率成片（不会修改原照片参数）")
    render.add_argument("sources",nargs="+")
    render.add_argument("--recipe",help="显式配方 JSON 文件，默认使用源照片 XMP")
    render.add_argument("--input",choices=("saved","original"),default="saved",help="已保存底图或原始文件")
    render.add_argument("--denoised",help="指定经来源验证的降噪成片；仅允许单张输入")
    render.add_argument("--format",choices=("tiff","jpeg","png"),default="tiff")
    render.add_argument("--output-mode",choices=("source_subdir","source_dir","sibling_dir","fixed"))
    render.add_argument("-o","--output-dir",default="")
    render.add_argument("--subdir",default="edited")
    render.add_argument("--json",action="store_true",help="每张照片一行 UTF-8 JSON")
    settings_in = commands.add_parser("import-settings",help="从 Camera RAW XMP 导入支持的设置，保存到照片 XMP")
    settings_in.add_argument("source")
    settings_in.add_argument("settings")
    settings_out = commands.add_parser("export-settings",help="另存 Camera RAW 设置到新 XMP 文件")
    settings_out.add_argument("source")
    settings_out.add_argument("destination")
    args = parser.parse_args(argv)
    stop = threading.Event()
    previous = None
    if threading.current_thread() is threading.main_thread():
        previous = signal.signal(signal.SIGINT,lambda *_:stop.set())
    try:
        store = RecipeStore()
        if args.command=="info":
            from .native import backend
            print(backend().info_json())
            return 0
        if args.command=="render":
            return _render(args,store,stop)
        state = store.load(args.source)
        if args.command=="show":
            print(json.dumps(state.to_dict(),ensure_ascii=False,indent=2))
        elif args.command=="auto-adjust":
            from .auto_adjust import prepare_auto_adjustment
            result = prepare_auto_adjustment(state, args.mode, cancelled=stop.is_set)
            check_cancelled(stop.is_set)
            if result.changed:
                state.current.recipe = result.recipe
                state.revision = store.save(state)
            print(json.dumps(dict(mode=result.mode, changed=result.changed), ensure_ascii=False))
        elif args.command=="import-settings":
            from .native import import_settings
            imported = import_settings(state.current.recipe.camera,read_bounded(args.settings))
            state.current.recipe.camera = imported["cameraRaw"]
            state.current.recipe.validated()
            check_cancelled(stop.is_set)
            state.revision = store.save(state)
            print(json.dumps(imported,ensure_ascii=False))
        elif args.command=="export-settings":
            from .native import export_settings
            data = export_settings(state.current.recipe.camera)
            check_cancelled(stop.is_set)
            # Exclusive create includes the source sidecar: no existing file is overwritten.
            with Path(args.destination).open("xb") as stream:
                stream.write(data)
            print(str(Path(args.destination).absolute()))
        return 0
    except (ImageProcessingCancelled, KeyboardInterrupt):
        return 130
    except Exception as exc:
        print(f"照片编辑失败：{exc}",file=sys.stderr)
        return 2
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT,previous)


def _render(args,store,stop):
    from .export import export_recipe
    if args.denoised and len(args.sources)!=1:
        raise ValueError("指定降噪版本时只允许一张原照片")
    options = ImageOutputOptions(output_mode=args.output_mode or ("fixed" if args.output_dir else "source_subdir"),
        subdir=args.subdir,output_directory=args.output_dir,format=args.format)
    recipe = Recipe.from_dict(json.loads(read_bounded(args.recipe))) if args.recipe else None
    destinations = allocate_outputs(args.sources,options)
    failed = False
    for source,destination in zip(args.sources,destinations):
        check_cancelled(stop.is_set)
        result = dict(source=str(Path(source).absolute()),destination=destination,status="success",error="")
        try:
            state = store.load(source)
            if args.input=="original":
                state.active = "original"
            if args.denoised:
                state.use_denoised(args.denoised)
            if recipe:
                state.current.recipe = recipe.validated()
            export_recipe(state,options,destination=destination,cancelled=stop.is_set)
        except ImageProcessingCancelled:
            raise
        except Exception as exc:
            failed = True
            result.update(status="failed",error=str(exc))
        print(json.dumps(result,ensure_ascii=False) if args.json else
              f"{result['status']}: {source} → {destination}"+(f"：{result['error']}" if result['error'] else ""),flush=True)
    return 1 if failed else 0


if __name__=="__main__":
    raise SystemExit(main())
