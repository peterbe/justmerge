from os import path
from setuptools import setup, find_packages

_here = path.dirname(__file__)


setup(
    name="justmerge",
    version="0.0.1",
    author="Peter Bengtsson",
    author_email="mail@peterbe.com",
    url="https://github.com/peterbe/justmerge",
    description="Just merge the GitHub Pull Requests that are ready to go",
    long_description=open(path.join(_here, "README.rst")).read(),
    license="MIT",
    classifiers=[
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: Implementation :: CPython",
        "License :: OSI Approved :: MIT License",
    ],
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    install_requires=["requests", "python-decouple", "toml"],
    extras_require={"dev": ["tox", "twine", "therapist", "black", "flake8"]},
    # entry_points="""
    #     [console_scripts]
    #     gg=gg.main:cli
    # """,
    # setup_requires=["pytest-runner"],
    # tests_require=["pytest", "pytest-mock", "requests_mock"],
    keywords="github",
)