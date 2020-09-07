"""Package setup file for Hyperion Lib."""

import setuptools

with open("README.md") as fh:
    long_description = fh.read()

setuptools.setup(
    name="hyperion-py",
    version="0.1.0",
    author="Dermot Duffy",
    author_email="dermot.duffy@gmail.com",
    description="Hyperion Ambient Lighting Python Package",
    include_package_data=True,
    long_description=long_description,
    long_description_content_type="text/markdown",
    keywords="hyperion ambilight",
    url="https://github.com/dermotduffy/hyperion-py",
    packages=setuptools.find_packages(),
    platforms="any",
    classifiers=[
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Home Automation",
    ],
    python_requires=">=3.6",
    zip_safe=True,
)
