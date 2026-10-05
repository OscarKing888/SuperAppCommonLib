# -*- coding: utf-8 -*-
"""无 Qt 的照片/边车文件事务；剪贴板与鸟名归档共用失败恢复路径。"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile


def same_file_path(path_a: str, path_b: str) -> bool:
    try:
        return os.path.samefile(path_a, path_b)
    except OSError:
        return os.path.normcase(os.path.normpath(path_a)) == os.path.normcase(os.path.normpath(path_b))


def publish_without_overwrite(source: str, destination: str) -> None:
    """独占发布；不支持硬链接的归档卷回退到独占创建再复制。"""
    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError:
        with open(destination, "xb") as output:
            try:
                with open(source, "rb") as incoming:
                    shutil.copyfileobj(incoming, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            except BaseException:
                output.close()
                os.unlink(destination)
                raise
        # 内容已经完整发布；不让可选文件属性失败触发错误回滚。
        try:
            shutil.copystat(source, destination)
        except OSError:
            pass
    try:
        os.unlink(source)
    except OSError:
        # 发布完成但暂存清理失败时，保留已发布文件并回收暂存副本。
        os.unlink(destination)
        raise


def transfer_file_pairs(
    path_pairs: list[tuple[str, str]],
    *,
    action: str,
    copy_sources: tuple[str, ...] = (),
    no_replace: bool = False,
) -> list[str]:
    """Atomically stage/commit every pair in one clipboard paste."""
    if action not in {"copy", "cut"}:
        raise ValueError(f"Unsupported clipboard action: {action!r}")
    pairs = [
        (os.path.abspath(source), os.path.abspath(dest))
        for source, dest in path_pairs
    ]
    pairs = [
        (source, dest)
        for source, dest in pairs
        if not same_file_path(source, dest)
    ]
    if not pairs:
        return []

    copy_keys = {os.path.abspath(p) for p in copy_sources}

    def is_cut(source):
        return action == "cut" and source not in copy_keys

    staged: list[tuple[str, str, str]] = []
    committed: list[tuple[str, str, str]] = []
    rollback_errors: list[str] = []
    try:
        for source, dest in pairs:
            if not os.path.isfile(source):
                raise FileNotFoundError(source)
            if os.path.exists(dest):
                raise FileExistsError(dest)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            fd, temp_path = tempfile.mkstemp(
                prefix=f".{Path(dest).name}.sbt-paste-",
                suffix=".tmp",
                dir=os.path.dirname(dest),
            )
            os.close(fd)
            # mkstemp reserves a collision-free name.  Remove its empty
            # placeholder so same-volume cut uses a fast rename instead
            # of a copy-over-existing fallback on Windows.
            os.remove(temp_path)
            try:
                if is_cut(source):
                    shutil.move(source, temp_path)
                else:
                    shutil.copy2(source, temp_path)
            except Exception as stage_exc:
                stage_rollback_error = ""
                if is_cut(source) and os.path.exists(temp_path) and not os.path.exists(source):
                    try:
                        # A filesystem move may complete and then raise
                        # (for example while copying metadata).  Restore
                        # the only surviving copy before unwinding.
                        shutil.move(temp_path, source)
                    except Exception as rollback_exc:
                        stage_rollback_error = (
                            f"{temp_path!r} -> {source!r}: {rollback_exc}"
                        )
                elif is_cut(source) and os.path.exists(temp_path):
                    # 失败的跨卷移动可能同时留下不完整源文件和完整暂存副本。
                    # 无法确认哪份完整时保留两份，绝不按“源存在”删除恢复副本。
                    stage_rollback_error = f"recoverable file retained at {temp_path!r}; source also exists: {source!r}"
                elif os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception as rollback_exc:
                        stage_rollback_error = f"remove {temp_path!r}: {rollback_exc}"
                elif is_cut(source) and not os.path.exists(source):
                    stage_rollback_error = (
                        f"both source and staging path are missing for {source!r}"
                    )
                if stage_rollback_error:
                    raise RuntimeError(
                        f"Clipboard staging failed ({stage_exc}); rollback was incomplete: "
                        f"{stage_rollback_error}"
                    ) from stage_exc
                raise
            staged.append((source, temp_path, dest))

        for source, temp_path, dest in staged:
            # Do not silently overwrite a destination created after the
            # initial collision check.
            if os.path.exists(dest):
                raise FileExistsError(dest)
            if no_replace:
                publish_without_overwrite(temp_path, dest)
            else:
                os.replace(temp_path, dest)
            committed.append((source, temp_path, dest))

        touched: list[str] = []
        for source, _temp_path, dest in committed:
            if is_cut(source):
                touched.append(source)
            touched.append(dest)
        return touched
    except Exception as exc:
        retained_paths: list[str] = []
        committed_by_source = {source: dest for source, _tmp, dest in committed}
        for source, temp_path, _dest in reversed(staged):
            current = committed_by_source.get(source) or temp_path
            if not os.path.exists(current):
                continue
            try:
                if is_cut(source):
                    if os.path.exists(source):
                        raise FileExistsError(source)
                    shutil.move(current, source)
                else:
                    os.remove(current)
            except Exception as rollback_exc:
                rollback_errors.append(f"{current!r} -> {source!r}: {rollback_exc}")
                if is_cut(source) and os.path.exists(current):
                    retained_paths.append(current)
        for _source, temp_path, _dest in reversed(staged):
            try:
                if os.path.exists(temp_path):
                    if is_cut(_source):
                        # A failed restore can leave the only surviving
                        # original in staging. Keep it for recovery even
                        # when the source path now contains a partial copy.
                        if temp_path not in retained_paths:
                            retained_paths.append(temp_path)
                        continue
                    os.remove(temp_path)
            except Exception as rollback_exc:
                rollback_errors.append(f"remove {temp_path!r}: {rollback_exc}")
        if rollback_errors or retained_paths:
            recovery_detail = (
                "; recoverable files retained at: " + ", ".join(repr(path) for path in retained_paths)
                if retained_paths else ""
            )
            raise RuntimeError(
                f"Clipboard bundle failed ({exc}); rollback was incomplete: "
                + "; ".join(rollback_errors) + recovery_detail
            ) from exc
        raise

