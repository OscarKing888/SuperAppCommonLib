import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import Image

from app_common.photo_edit.model import Recipe, AdjustmentStep
from app_common.photo_edit.native import export_settings
from app_common.photo_edit.storage import RecipeStore


def run_cli(*args):
    script = '''
import builtins, runpy, sys
original = builtins.__import__
def checked(name,*args,**kwargs):
    if name.startswith(("PyQt","PySide")):
        raise AssertionError("CLI imported Qt: "+name)
    return original(name,*args,**kwargs)
builtins.__import__ = checked
sys.argv = ["photo_edit",*sys.argv[1:]]
runpy.run_module("app_common.photo_edit",run_name="__main__")
'''
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ,PYTHONPATH=str(root))
    return subprocess.run([sys.executable,"-c",script,*map(str,args)],cwd=root,
                          env=environment,capture_output=True,text=True,encoding="utf-8",timeout=60)


def test_help_never_requires_qt():
    result = run_cli("--help")
    assert result.returncode==0,result.stderr
    assert "Camera RAW" in result.stdout


def test_cli_real_render_reads_override_without_mutating_recipe(tmp_path):
    pytest.importorskip("app_common.photo_edit._native")
    source = tmp_path/"中文 原片.png"
    Image.new("RGB",(12,8),(40,70,90)).save(source)
    recipe = Recipe(steps=[AdjustmentStep("invert")])
    settings = tmp_path/"preset.json"
    settings.write_text(json.dumps(recipe.to_dict()),encoding="utf-8")
    result = run_cli("render",source,"--recipe",settings,"--format","png","--json")
    assert result.returncode==0,result.stderr+result.stdout
    report = json.loads(result.stdout)
    assert report["status"]=="success" and "中文" in report["source"]
    assert Path(report["destination"]).exists()
    assert not source.with_suffix(".xmp").exists()
    assert RecipeStore().load(source).current.recipe.is_identity()


def test_cli_settings_round_trip_and_exclusive_output(tmp_path):
    pytest.importorskip("app_common.photo_edit._native")
    source = tmp_path/"白鹭.jpg"
    Image.new("RGB",(12,8)).save(source)
    recipe = Recipe()
    recipe.camera["exposure"] = 1.25
    settings = tmp_path/"import.xmp"
    settings.write_bytes(export_settings(recipe.camera))
    result = run_cli("import-settings",source,settings)
    assert result.returncode==0,result.stderr
    assert RecipeStore().load(source).current.recipe.camera["exposure"]==1.25
    output = tmp_path/"export.xmp"
    result = run_cli("export-settings",source,output)
    assert result.returncode==0,result.stderr
    before = output.read_bytes()
    result = run_cli("export-settings",source,output)
    assert result.returncode!=0 and output.read_bytes()==before


def test_cli_batch_continues_after_missing_input(tmp_path):
    pytest.importorskip("app_common.photo_edit._native")
    source = tmp_path/"good.png"
    Image.new("RGB",(12,8)).save(source)
    result = run_cli("render",tmp_path/"missing.jpg",source,"--json")
    assert result.returncode==1,result.stderr
    reports = [json.loads(line) for line in result.stdout.splitlines()]
    assert [item["status"] for item in reports]==["failed","success"]
