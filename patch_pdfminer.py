"""
One-time fix for: ImportError: cannot import name 'PSSyntaxError' from 'pdfminer.pdfparser'

This is a real bug in unstructured==0.16.11: it hardcodes an import path that a newer
pdfminer.six release relocated. The class itself still exists (just at pdfminer.psparser
instead), so this patches the one bad import line in place rather than downgrading
pdfminer.six -- which would break pdfplumber, another dependency unstructured needs,
since pdfplumber explicitly requires the newer pdfminer.six.

Run this once: python patch_pdfminer.py
You'll need to re-run it if you ever `pip install --upgrade unstructured` later,
since that would overwrite this file with the original broken import again.
"""
import importlib.util
import pathlib

# Locate the file WITHOUT importing it -- importing it directly would hit the exact
# broken line we're trying to fix, before we ever get a chance to patch it.
spec = importlib.util.find_spec("unstructured.partition.pdf_image.pdfminer_utils")
if spec is None or spec.origin is None:
    print("Could not locate unstructured.partition.pdf_image.pdfminer_utils -- "
          "is `pip install -r requirements.txt` done?")
    raise SystemExit(1)

path = pathlib.Path(spec.origin)
content = path.read_text()

old_line = "from pdfminer.pdfparser import PSSyntaxError"
new_line = "from pdfminer.psparser import PSSyntaxError"

if new_line in content:
    print(f"Already patched: {path}")
elif old_line in content:
    content = content.replace(old_line, new_line)
    path.write_text(content)
    print(f"Patched successfully: {path}")
    print("PDF ingestion should work now -- restart your backend (uvicorn) and try again.")
else:
    print(f"Expected line not found in {path} -- the file may differ from what this "
          "patch expects. Open it and check line ~9 manually.")
