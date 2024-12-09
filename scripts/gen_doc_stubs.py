"""This script walks through the source code and generates a markdown file for each python file.

The goal is then for the markdown files to be used by mkdocs to call the plugin mkdocstring.
"""

from pathlib import Path

import mkdocs_gen_files

src_root = Path("src")
for path in src_root.glob("**/*.py"):
    if "__init__" in str(path):
        print("Skipping", path)
        continue
    doc_path = Path("package", path.relative_to(src_root)).with_suffix(".md")

    if "seqly" not in str(path) and "__init__" not in str(path):
        with mkdocs_gen_files.open(doc_path, "w") as f:
            ident = ".".join(path.with_suffix("").parts)
            print("::: " + ident, file=f)
