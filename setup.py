from pathlib import Path
from setuptools import setup, find_packages

ROOT = Path(__file__).resolve().parent

def load_requirements():
    req_path = ROOT / "requirements.txt"
    if not req_path.exists():
        return []
    return [
        line.strip()
        for line in req_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

setup(
    name="video-to-wiki",
    version="1.1.0",
    description="A premium, first-principles CLI tool to ingest technical videos (YouTube, Bilibili) into structured Markdown notes for RAG and personal vaults (Obsidian).",
    author="Cooperun",
    packages=find_packages(),
    py_modules=["main"],
    install_requires=load_requirements(),
    entry_points={
        "console_scripts": [
            "video-to-wiki=main:main",
        ],
    },
    python_requires=">=3.9",
)
