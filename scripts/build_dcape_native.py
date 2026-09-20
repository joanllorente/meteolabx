"""Compila la extensión opcional: python -m pip install pybind11; python scripts/build_dcape_native.py."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import sysconfig
import pybind11

root = Path(__file__).resolve().parents[1]
output = root / 'server/services' / ('_dcape_native' + sysconfig.get_config_var('EXT_SUFFIX'))
# -fvisibility=hidden es lo que pybind11 da por supuesto: sin él, GCC avisa de
# que `Profile` se declara con más visibilidad que su campo `info` y el módulo
# exporta símbolos que nadie enlaza desde fuera.
command = shlex.split(os.environ.get('CXX', 'c++')) + [
    '-O3', '-std=c++17', '-shared', '-fPIC', '-ffp-contract=off',
    '-fvisibility=hidden', '-fvisibility-inlines-hidden',
    '-I'+pybind11.get_include(), '-I'+sysconfig.get_paths()['include'],
    str(root/'native/dcape.cpp'), '-o', str(output)]
if sys.platform == 'darwin':
    command += ['-undefined', 'dynamic_lookup']
subprocess.run(command, check=True)
print(output)
