import json, pathlib
p=pathlib.Path(r'C:\Users\USER\Documents\CODES\djangoblog2\staticfiles\staticfiles.json')
if not p.exists():
    print('NO FILE')
    raise SystemExit(0)
j=json.load(p.open())
keys=[k for k in j.get('paths',{}) if k.startswith('controlcenter/')]
print('count',len(keys))
for k in keys:
    print(k, '->', j['paths'][k])
