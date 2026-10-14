"""High-depth image encoding and image/XMP pair publication shared by processing tools."""
from __future__ import annotations
import errno
import os
from pathlib import Path
import shutil
import tempfile
import unicodedata
import numpy as np
from PIL import Image
from app_common.exif_io import get_exiftool_executable_path
from app_common.exif_io.exiftool_runner import run_exiftool_utf8_args
from app_common.image_pixels import srgb_profile
from app_common.image_processing_types import check_cancelled


def write_pixels(path, rgb, alpha, format, profile):
    if format == "jpeg" and alpha is not None and np.any(alpha < 1):
        raise ValueError("JPEG 不支持透明通道，请选择 TIFF 或 PNG")
    if format in ("tiff","png"):
        pixels = np.rint(np.clip(rgb,0,1)*65535).astype(np.uint16)
        if alpha is not None:
            a = np.rint(np.clip(alpha,0,1)*65535).astype(np.uint16)
            pixels = np.concatenate((pixels,a[...,None]),axis=-1)
        if format == "tiff":
            import tifffile
            tifffile.imwrite(path,pixels,photometric="rgb",planarconfig="contig",compression="deflate",
                             compressionargs={"level":6},predictor=2,metadata=None,iccprofile=profile,
                             maxworkers=1,extrasamples="unassalpha" if alpha is not None else None)
        else:
            import imagecodecs
            Path(path).write_bytes(imagecodecs.png_encode(pixels))
    elif format == "jpeg":
        with Image.fromarray(np.rint(np.clip(rgb,0,1)*255).astype(np.uint8)) as image:
            image.save(path,format="JPEG",quality=95,subsampling=0,icc_profile=profile)
    else:
        raise ValueError(f"不支持的输出格式：{format}")


def export_pixels(source, destination, rgb, alpha, format, *, prepare_sidecar, cancelled=None,
                  validate_source=None, metadata_writer=None, publisher=None):
    """Publish a new image and sidecar without replacing existing user files.

    Metadata preparation receives (staged_sidecar, width, height). The caller
    owns provenance and baked/active recipe semantics. No source metadata writes.
    """
    source,destination = Path(source).resolve(),Path(destination).absolute()
    metadata_writer = metadata_writer or copy_output_metadata
    publisher = publisher or _publish_new
    initial = source.stat()
    def check_source():
        current = source.stat()
        if (current.st_size,current.st_mtime_ns,current.st_ino)!=(initial.st_size,initial.st_mtime_ns,initial.st_ino):
            raise RuntimeError("原图在导出期间已变化，未发布过期成片")
        if validate_source:
            validate_source()
    check_cancelled(cancelled)
    check_source()
    destination.parent.mkdir(parents=True,exist_ok=True)
    _check_destination(source,destination)
    if format == "jpeg" and alpha is not None and np.any(alpha<1):
        raise ValueError("JPEG 不支持透明通道，请选择 TIFF 或 PNG")
    published = []
    try:
        with tempfile.TemporaryDirectory(prefix=".superbird-export-",dir=destination.parent) as folder:
            temporary = Path(folder)
            staged = temporary/destination.name
            sidecar = staged.with_suffix(".xmp")
            profile = srgb_profile()
            icc = temporary/"output.icc"
            icc.write_bytes(profile)
            height,width = rgb.shape[:2]
            prepare_sidecar(sidecar,width,height)
            check_cancelled(cancelled)
            write_pixels(staged,rgb,alpha,format,profile)
            check_cancelled(cancelled)
            metadata_writer(source,staged,sidecar,rgb,icc,cancelled=cancelled)
            _check_destination(source,destination)
            check_cancelled(cancelled)
            check_source()
            for staged_path,target in ((sidecar,destination.with_suffix(".xmp")),(staged,destination)):
                published.append((target,publisher(staged_path,target)))
    except BaseException as exc:
        retained = []
        for path,identity in reversed(published):
            try:
                st = path.stat()
                if (st.st_dev,st.st_ino)==identity:
                    path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                retained.append(str(path))
        if retained:
            raise RuntimeError(f"{exc}；未能清理本次成片，请检查：{', '.join(retained)}") from exc
        raise

def _exiftool(args: list[str], *, allow_empty=False) -> None:
    executable = get_exiftool_executable_path()
    if not executable:
        raise RuntimeError("图像导出需要完整的 ExifTool 组件，未发布当前照片")
    # 写出使用有界生命周期的独立进程，不争用界面元数据的全局 stay-open 锁。
    result = run_exiftool_utf8_args(executable, ["-charset", "filename=UTF8", "-overwrite_original", *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if result.returncode or "error:" in result.stderr.lower() or "due to errors" in result.stdout.lower():
        warnings = [s.strip() for s in result.stderr.splitlines() if s.strip()]
        if allow_empty and warnings and all(s.startswith("Warning: No writable tags set from ") for s in warnings):
            return
        raise RuntimeError(f"图像元数据保存失败：{result.stderr or result.stdout}")


def copy_output_metadata(source: Path, staged_image: Path, staged_sidecar: Path,
                         rgb: np.ndarray, icc_path: Path, *, cancelled=None,
                         strip_edit_settings=False) -> None:
    # 不块拷贝 RAW/TIFF 的 IFD0 或 MakerNotes：其中隐藏数据和偏移属于原始布局。
    # ExifIFD:all 本身也会带入 MakerNotes，必须显式排除整个二进制块；
    # 仅排除 MakerNotes:all（组内字段）仍会产生损坏的 Sony TIFF 隐藏数据引用。
    exclusions = ["--XMP-crs:all"] if strip_edit_settings else []
    _exiftool(["-TagsFromFile", str(source), "-ExifIFD:all", "-GPS:all", "-InteropIFD:all",
               "--MakerNotes", "-IFD0:Make", "-IFD0:Model", "-IFD0:Artist", "-IFD0:Copyright",
               "-IFD0:ImageDescription", "-IFD0:ModifyDate", "-XMP:all", "-IPTC:all",
               *exclusions, str(staged_image)],
              allow_empty=True)
    check_cancelled(cancelled)
    # 用户侧车中的标准字段优先；未知 namespace 完整保留在配套侧车中。
    _exiftool(["-TagsFromFile", str(staged_sidecar), "-XMP:all", str(staged_image)])
    height, width = rgb.shape[:2]
    args = ["-IFD0:Orientation#=1", "-XMP-tiff:Orientation#=1", "-ExifIFD:ColorSpace#=1",
            f"-ExifIFD:ExifImageWidth={width}", f"-ExifIFD:ExifImageHeight={height}",
            f"-XMP-tiff:ImageWidth={width}", f"-XMP-tiff:ImageHeight={height}",
            f"-XMP-exif:ExifImageWidth={width}", f"-XMP-exif:ExifImageHeight={height}",
            f"-ICC_Profile<={icc_path}"]
    if staged_image.suffix.lower() in (".jpg", ".jpeg"):
        # 只制作元数据小图，TIFF 主像素始终通过 uint16 数组写出。
        thumbnail = Image.fromarray(np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8))
        thumbnail.thumbnail((160, 160), Image.Resampling.LANCZOS)
        thumbnail_path = staged_image.parent / "thumbnail.jpg"
        thumbnail.save(thumbnail_path, quality=85)
        tw, th = thumbnail.size
        thumbnail.close()
        args += [f"-ThumbnailImage<={thumbnail_path}", "-IFD1:Orientation#=1",
                 f"-IFD1:ImageWidth={tw}", f"-IFD1:ImageHeight={th}"]
    _exiftool([*args, str(staged_image)])
    check_cancelled(cancelled)


def _name_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _check_destination(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        raise ValueError("图像输出不能覆盖原图")
    names = {_name_key(entry.name) for entry in destination.parent.iterdir()}
    if _name_key(destination.name) in names or _name_key(destination.with_suffix(".xmp").name) in names:
        raise FileExistsError(f"图像输出或其 XMP 已存在：{destination}")


def _publish_new(staged: Path, target: Path) -> tuple[int, int]:
    """硬链接原子发布；不支持时仅写独占打开的文件句柄，绝不替换目标路径。"""
    staged_stat = staged.stat()
    expected = (staged_stat.st_dev, staged_stat.st_ino)
    try:
        os.link(staged, target)
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV):
            raise
        identity = None
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            with os.fdopen(fd, "wb") as output, staged.open("rb") as source:
                owned_stat = os.fstat(output.fileno())
                identity = (owned_stat.st_dev, owned_stat.st_ino)
                # exFAT 的回退无法原子发布，但句柄始终指向自己的文件。
                # 不能用 stat + replace：核对后被其他程序替换的成片会遭覆盖。
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            current = target.lstat()
            if (current.st_dev, current.st_ino) != identity:
                raise FileExistsError(f"输出目标已被其他操作替换：{target}")
        except BaseException:
            if identity is not None:
                try:
                    current = target.lstat()
                    if (current.st_dev, current.st_ino) == identity:
                        target.unlink()
                except FileNotFoundError:
                    pass
            raise
        return identity
    # 使用源硬链接的身份，不能事后 stat 目标并把外部替换文件误记为自己的。
    return expected
