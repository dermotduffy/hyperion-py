"""Package setup file for Hyperion Lib."""

import setuptools

with open("README.md") as fh:
    long_description = fh.read()

setuptools.setup(
    name="hyperion-py",
    version="0.7.0",
    author="Dermot Duffy",
    author_email="dermot.duffy@gmail.com",
    description="Hyperion Ambient Lighting Python Package",
    include_package_data=True,
    long_description=long_description,
    long_description_content_type="text/markdown",
    keywords="hyperion ambilight",
    url="https://github.com/dermotduffy/hyperion-py",
    package_data={"hyperion-py": ["py.typed"]},
    packages=setuptools.find_packages(exclude=["tests", "tests.*"]),
    platforms="any",
    classifiers=[
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Home Automation",
    ],
    python_requires=">=3.7",
    zip_safe=False,  # Required for py.typed.
)
