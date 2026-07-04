import re
from web import portal
import inspect

l = re.search(r'_LOJA = """(.*?)"""', inspect.getsource(portal), re.S).group(1)
print(f"Template size: {len(l)}")
print(f"Has recalcLocal: {'recalcLocal' in l}")
