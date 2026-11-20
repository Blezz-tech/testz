import re,sys
def blocks(t, fm):
    if fm: t=t.split('---',2)[2]
    t=t.split('### Примечания')[0]
    return [len([b for b in re.split(r'\n\s*\n',p) if b.strip() and b.strip()!='---']) for p in re.split(r'\n?————\n?',t)]
n=int(sys.argv[1])
z=open(f'chapters/zh/{n:04d}.md',encoding='utf-8').read()
s=open(f'chapters/ru/{n:04d}.md',encoding='utf-8').read()
b=blocks(s,True)
body=s.split('---',2)[2]; main=body.split('### Примечания')[0]
print('zh',blocks(z,False),'ru',b,'OK' if blocks(z,False)==b else 'MISMATCH')
print('bad',sorted({c for c in main if c.isalpha() and not ('\u0400'<=c<='\u04FF')}),'cjk',[m.group() for m in re.finditer(r'[\u4e00-\u9fff]+',main)])
refs=sorted({int(x) for x in re.findall(r'\[\^(\d+)\]',body)}); defs=[int(x) for x in re.findall(r'^\[\^(\d+)\]:',body,re.M)]
end=re.search(r'endnotes: (\d+)',s).group(1)
print('refs',refs,'defs',defs,'endnotes',end, 'OK' if refs==defs and int(end)==len(defs) else 'CHECK')
