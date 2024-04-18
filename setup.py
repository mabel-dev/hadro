import platform

import numpy
from Cython.Build import cythonize
from setuptools import Extension
from setuptools import find_packages
from setuptools import setup

LIBRARY = "hadro"


def is_mac():  # pragma: no cover
    return platform.system().lower() == "darwin"


if is_mac():
    COMPILE_FLAGS = ["-O2"]
else:
    COMPILE_FLAGS = ["-O2", "-march=native"]

__author__ = "notset"
__version__ = "notset"
with open(f"{LIBRARY}/__version__.py", mode="r") as v:
    vers = v.read()
exec(vers)  # nosec

with open("README.md", mode="r", encoding="UTF8") as rm:
    long_description = rm.read()

try:
    with open("requirements.txt", "r") as f:
        required = f.read().splitlines()
except:
    with open(f"{LIBRARY}.egg-info/requires.txt", "r") as f:
        required = f.read().splitlines()

extensions = [
    Extension(
        name="hadro.compiled.memtable",
        sources=["hadro/compiled/memtable.pyx"],
        language="c++",
        extra_compile_args=COMPILE_FLAGS + ["-std=c++11"],
    )
]

setup_config = {
    "name": LIBRARY,
    "version": __version__,
    "description": "Storage Engine",
    "long_description": long_description,
    "long_description_content_type": "text/markdown",
    "maintainer": __author__,
    "author": __author__,
    "author_email": "justin.joyce@joocer.com",
    "packages": find_packages(include=[LIBRARY, f"{LIBRARY}.*"]),
    "url": "https://github.com/mabel-dev/hadro/",
    "install_requires": required,
    "ext_modules": cythonize(extensions),
}

setup(**setup_config)
