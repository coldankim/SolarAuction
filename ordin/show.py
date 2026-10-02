import json,re,sys
d=json.load(open('ordin/compact.json',encoding='utf-8')); ks=json.load(open('ordin/compact_keys.json',encoding='utf-8'))
NOISE=re.compile(r"^[별표s*d+[^]]*]s*[<(〈]|대기환경|물환경|숙박시설|준주거|상업지역|「건축법 시행령」\s*별표\s*1\s*제\d|전용주거지역|일반주거지역|건축할 수 있는 건축물|진입도로|제곱미터|바닥면적|공장|창고|근린생활|교육연구|노유자|자동차관련")
a,b=int(sys.argv[1]),int(sys.argv[2]); cap=int(sys.argv[3]) if len(sys.argv)>3 else 2000
for i in range(a,min(b,len(ks))):
    k=ks[i]; lines=[l for l in d[k].split('\n') if not NOISE.search(l)]
    print(f"### {i} {k}\n"+'\n'.join(lines)[:cap]+"\n")
