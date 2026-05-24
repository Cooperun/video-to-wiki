from setuptools import setup, find_packages

setup(
    name="video-to-wiki",
    version="1.0.0",
    description="A premium, first-principles CLI tool to ingest technical videos (YouTube, Bilibili) into structured Markdown notes for RAG and personal vaults (Obsidian).",
    author="Cooperun",
    packages=find_packages(),
    py_modules=["main"],
    install_requires=[
        "openai>=1.0.0",
        "pyyaml",
        "yt-dlp",
        "faster-whisper"
    ],
    entry_points={
        "console_scripts": [
            "video-to-wiki=main:main",
        ],
    },
    python_requires=">=3.9",
)
