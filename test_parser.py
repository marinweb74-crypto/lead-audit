import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

try:
    from parser import run
    print("parser import OK")
except Exception as e:
    print(f"parser import FAIL: {e}")

try:
    from enricher import run
    print("enricher import OK")
except Exception as e:
    print(f"enricher import FAIL: {e}")

try:
    from auditor import run
    print("auditor import OK")
except Exception as e:
    print(f"auditor import FAIL: {e}")

try:
    from sender import run
    print("sender import OK")
except Exception as e:
    print(f"sender import FAIL: {e}")
