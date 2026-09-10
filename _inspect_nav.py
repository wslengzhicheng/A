import pathlib
root = pathlib.Path("static")
for n in sorted(root.glob("*.html")):
    t = n.read_text(encoding="utf-8")
    print("====", n.name, "====")
    for i, l in enumerate(t.splitlines(), 1):
        if "href=" in l and (".html" in l or 'href="/"' in l):
            print("%d:%s" % (i, l))
    print()
ws = pathlib.Path("web.py").read_text(encoding="utf-8").splitlines()
print("--- web.py routing ---")
for i in list(range(80, 111)) + list(range(630, 711)):
    print("%d:%s" % (i, ws[i-1]))